"""Patch-aware test impact selection using baseline coverage and source structure."""
import ast
import gzip
import json
import re
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
FUNCTION = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
ASSIGN = (ast.Assign, ast.AnnAssign, ast.AugAssign)


def load(path):
    with gzip.open(path, "rt") as f:
        return json.load(f)


def parse_patch(text):
    files = {}
    current = None
    old = new = 0
    hunk = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            current = line.split(" b/", 1)[-1]
            files[current] = {"deleted": set(), "added": set(), "hunks": []}
            hunk = None
        elif current is not None:
            match = HUNK.match(line)
            if match:
                old, _, new, _ = match.groups()
                old, new = int(old), int(new)
                hunk = [old, []]
                files[current]["hunks"].append(hunk)
            elif hunk is not None and line[:1] in (" ", "+", "-"):
                hunk[1].append(line)
                if line[0] == "-":
                    files[current]["deleted"].add(old)
                    old += 1
                elif line[0] == "+":
                    files[current]["added"].add(new)
                    new += 1
                else:
                    old += 1
                    new += 1
    return files


def patched_source(source, hunks):
    lines = source.splitlines()
    result = []
    pos = 0
    for start, hunk in hunks:
        start = max(0, start - 1)
        if start < pos:
            raise ValueError("overlapping hunks")
        result.extend(lines[pos:start])
        pos = start
        for line in hunk:
            if line[0] in " -":
                if pos >= len(lines) or lines[pos] != line[1:]:
                    raise ValueError("patch context mismatch")
                pos += 1
            if line[0] in " +":
                result.append(line[1:])
    result.extend(lines[pos:])
    return "\n".join(result) + "\n"


def containing(nodes, line, types):
    return sorted((n for n in nodes if isinstance(n, types) and getattr(n, "lineno", 0) <= line <= getattr(n, "end_lineno", -1)), key=lambda n: (n.end_lineno - n.lineno, -n.lineno, -getattr(n, "col_offset", 0)))


def names_of_assignment(node):
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return {n.id if isinstance(n, ast.Name) else n.attr for target in targets for n in ast.walk(target) if isinstance(n, (ast.Name, ast.Attribute))}


class Selector:
    def __init__(self, tests, coverage, index):
        self.tests = {t["id"]: t for t in tests}
        self.all = set(self.tests)
        self.contexts = coverage["contexts"]
        self.coverage = coverage["files"]
        self.sources = index["sources"]
        self.index_ids = index["test_ids"]
        self.references = index["references"]
        self.dialects = index["dialects"]
        self.fixtures = index["fixtures"]
        self.boundary_files = set(index.get("boundary_files", ()))
        self.file_cache = {}
        self.line_cache = {}
        self.by_file = {}
        for name, test in self.tests.items():
            self.by_file.setdefault(test["file"], set()).add(name)

    def line(self, path, n):
        key = (path, n)
        if key not in self.line_cache:
            self.line_cache[key] = {self.contexts[i] for i in self.coverage.get(path, {}).get(str(n), ()) if self.contexts[i] in self.tests}
        return self.line_cache[key]

    def span(self, path, start, end):
        selected = set()
        for n in range(start, end + 1):
            selected.update(self.line(path, n))
        return selected

    def file(self, path):
        if path not in self.file_cache:
            selected = set()
            for items in self.coverage.get(path, {}).values():
                selected.update(self.contexts[i] for i in items if self.contexts[i] in self.tests)
            self.file_cache[path] = selected
        return self.file_cache[path]

    def refs(self, symbol):
        return {self.index_ids[i] for i in self.references.get(symbol, ())} & self.all

    def dialect(self, path):
        parts = path.split("/")
        if len(parts) == 3 and parts[1] in ("dialects", "parsers", "generators", "typing"):
            return {self.index_ids[i] for i in self.dialects.get(Path(path).stem, ())} & self.all
        return set()

    def static_impact(self, path, nodes, n):
        dialect = self.dialect(path)
        classes = containing(nodes, n, ast.ClassDef)
        assignments = containing(nodes, n, ASSIGN)
        symbols = set()
        if assignments:
            assignment = assignments[0]
            symbols = names_of_assignment(assignment)
        selected = set()
        for symbol in symbols:
            selected.update(self.refs(symbol))
        if classes:
            cls = classes[0]
            class_tests = self.refs(cls.name) | self.span(path, cls.lineno, cls.end_lineno)
            # A generic class attribute such as arg_types is shared by many
            # classes. Use the concrete class to restrict its impact.
            if class_tests and selected and not dialect:
                intersection = class_tests & selected
                selected = class_tests | intersection
            elif not selected and not dialect:
                selected = class_tests
        if dialect:
            # A class variable may be read during class creation, rather than
            # during the test. All observed users of that dialect are candidates.
            selected = dialect
        if not selected:
            selected = self.file(path)
        return selected or self.all

    def code_impact(self, path, change):
        original = self.sources.get(path)
        if original is None:
            return self.all
        try:
            modified = patched_source(original, change["hunks"])
            before = ast.parse(original, type_comments=True)
            after = ast.parse(modified, type_comments=True)
            compile(modified, path, "exec")
        except (SyntaxError, ValueError):
            # Import and syntax errors can break arbitrary test collection.
            return self.all
        if ast.dump(before, include_attributes=False) == ast.dump(after, include_attributes=False):
            # Verified equivalent syntax (including docstrings and type
            # comments), not a guess that every comment change is harmless.
            return set()
        nodes = list(ast.walk(before))
        new_nodes = list(ast.walk(after))
        # An invalid import can cause unrelated test files to fail collection.
        if any(containing(nodes, n, (ast.Import, ast.ImportFrom)) for n in change["deleted"]) or any(containing(new_nodes, n, (ast.Import, ast.ImportFrom)) for n in change["added"]):
            return self.all
        selected = set()
        for n in sorted(change["deleted"]):
            direct = self.line(path, n)
            functions = containing(nodes, n, FUNCTION)
            if not functions:
                selected.update(direct)
                selected.update(self.static_impact(path, nodes, n))
            elif direct:
                selected.update(direct)
            elif functions:
                stmt = containing(nodes, n, ast.stmt)
                if stmt:
                    selected.update(self.span(path, stmt[0].lineno, stmt[0].end_lineno))
                if not stmt or not self.span(path, stmt[0].lineno, stmt[0].end_lineno):
                    fn = functions[0]
                    selected.update(self.span(path, fn.lineno, fn.end_lineno))
                    if not self.span(path, fn.lineno, fn.end_lineno) and not isinstance(fn, ast.Lambda):
                        selected.update(self.refs(fn.name))
            # Short-circuit conditions can be reached through a different
            # clause after an edit. Include the complete boolean expression.
            for boolean in containing(nodes, n, ast.BoolOp):
                selected.update(self.span(path, boolean.lineno, boolean.end_lineno))
        if not change["deleted"]:
            # Insertions need the enclosing old function or class, since the
            # inserted lines themselves have no baseline coverage.
            for n in sorted(change["added"]):
                fns = containing(new_nodes, n, FUNCTION)
                if fns and not isinstance(fns[0], ast.Lambda):
                    fn = fns[0]
                    for old in nodes:
                        if isinstance(old, type(fn)) and getattr(old, "name", None) == fn.name:
                            selected.update(self.span(path, old.lineno, old.end_lineno))
                    selected.update(self.refs(fn.name))
                else:
                    selected.update(self.dialect(path) or self.file(path) or self.all)
        # The executor uses subprocesses for integration tests. The parent
        # process's coverage doesn't fully describe those child processes.
        # Executor implementation changes therefore also cover integration.
        if path.startswith("sqlglot/executor/") or path == "sqlglot/planner.py":
            selected.update(t for t in self.all if self.tests[t]["file"] == "tests/test_executor.py")
        if path.startswith("sqlglot/typing/"):
            selected.update(self.by_file.get("tests/test_typing.py", ()))
        if path in self.boundary_files:
            selected.update(self.file(path))
        return selected

    def select(self, patch):
        result = set()
        paths = parse_patch(patch)
        if not paths:
            return self.all
        for path, change in paths.items():
            if not change["hunks"]:
                result.update(self.all)
            elif path.startswith("tests/fixtures/"):
                candidates = {self.index_ids[i] for i in self.fixtures.get(path, ())} & self.all
                if not candidates:
                    folder = path.split("/")[2]
                    candidates = {t for t in self.all if folder in self.tests[t]["file"]}
                result.update(candidates or self.all)
            elif path.startswith("tests/"):
                result.update(self.by_file.get(path) or self.all)
            elif path.startswith("sqlglot/") and path.endswith(".py"):
                result.update(self.code_impact(path, change))
            else:
                result.update(self.all)
        return result


def schedule(tests):
    if not tests:
        return []
    items = sorted(((round(t["seconds"] * 1000), t["number"]) for t in tests), key=lambda x: (-x[0], x[1]))
    best = None
    best_cost = float("inf")
    for count in range(1, min(8, len(items)) + 1):
        workers = [[] for _ in range(count)]
        loads = [0] * count
        for duration, number in items:
            i = min(range(count), key=lambda j: (loads[j], len(workers[j]), j))
            workers[i].append(number)
            loads[i] += duration
        cost = max(loads) + 2000 * count
        if cost < best_cost:
            best_cost, best = cost, workers
    return best


def main():
    data = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/data")
    tests = json.loads((data / "tests.json").read_text())["tests"]
    selector = Selector(tests, load(HERE / "coverage_map.json.gz"), load(HERE / "impact_index.json.gz"))
    for patch in sorted((data / "commits").glob("*.patch")):
        try:
            selected = selector.select(patch.read_text())
        except Exception as e:
            print(f"Conservative fallback: {e}", file=sys.stderr)
            selected = selector.all
        workers = schedule([selector.tests[name] for name in selected])
        print(json.dumps({"commit": patch.stem, "workers": workers}, separators=(",", ":")))


if __name__ == "__main__":
    main()
