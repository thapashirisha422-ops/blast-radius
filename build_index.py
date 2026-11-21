"""Build a static impact index from the pinned, unmodified sqlglot checkout.

Only baseline source, fixture paths, and baseline test coverage are used.
No patches or answer labels are inputs to this script.
"""
import ast
import gzip
import json
import re
import sys
from collections import defaultdict
from pathlib import Path


def main():
    root = Path(sys.argv[1])
    cov = json.load(gzip.open(sys.argv[2], "rt"))
    contexts = cov["contexts"]
    tests = [x for x in contexts if x.startswith("tests/")]
    test_numbers = {t: i for i, t in enumerate(tests)}
    by_file = defaultdict(list)
    for t in tests:
        by_file[t.split("::")[0]].append(t)
    refs = defaultdict(set)
    dialects = {p.stem for p in (root / "sqlglot/dialects").glob("*.py")}
    dialects -= {"__init__", "dialect"}
    dialect_tests = defaultdict(set)
    fixture_tests = defaultdict(set)
    sources = {}
    boundary_files = set()
    files = cov["files"]
    for path in sorted((root / "sqlglot").rglob("*.py")):
        name = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        sources[name] = source
        lines = files.get(name, {})
        parts = name.split("/")
        if len(parts) == 3 and parts[1] in {"dialects", "parsers", "generators", "typing"} and path.stem in dialects:
            for values in lines.values():
                dialect_tests[path.stem].update(contexts[i] for i in values if contexts[i].startswith("tests/"))
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {"__reduce__", "__reduce_ex__", "__getstate__", "__setstate__"}:
                boundary_files.add(name)
                for child in ast.walk(node):
                    if isinstance(child, ast.ImportFrom) and child.module and child.module.startswith("sqlglot."):
                        boundary_files.add(child.module.replace(".", "/") + ".py")
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                symbol = node.attr
            elif isinstance(node, ast.Name):
                symbol = node.id
            else:
                continue
            if isinstance(node.ctx, ast.Load):
                refs[symbol].update(contexts[i] for i in lines.get(str(node.lineno), ()) if contexts[i].startswith("tests/"))
    fixtures = [p.relative_to(root).as_posix() for p in (root / "tests/fixtures").rglob("*") if p.is_file()]
    fixture_keys = defaultdict(list)
    for path in fixtures:
        p = Path(path)
        for key in {p.stem, p.name, path.removeprefix("tests/fixtures/"), path}:
            fixture_keys[key].append(path)
    dialect_rx = re.compile(r"\b(" + "|".join(sorted(map(re.escape, dialects), key=len, reverse=True)) + r")\b", re.I)
    for path in sorted((root / "tests").rglob("*.py")):
        name = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        file_tests = by_file[name]
        for t in file_tests:
            stem = path.stem.removeprefix("test_")
            if stem in dialects:
                dialect_tests[stem].add(t)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test_"):
                continue
            matching = [t for t in file_tests if t.split("::")[-1].split("[")[0] == node.name]
            if not matching:
                continue
            for child in ast.walk(node):
                if isinstance(child, ast.Constant) and isinstance(child.value, str):
                    value = child.value
                    for fixture in fixture_keys.get(value, ()):
                        fixture_tests[fixture].update(matching)
                    for dialect in dialect_rx.findall(value):
                        dialect_tests[dialect.lower()].update(matching)
                elif isinstance(child, ast.Name):
                    refs[child.id].update(matching)
                elif isinstance(child, ast.Attribute):
                    refs[child.attr].update(matching)
    # Dialect subclasses also use inherited implementations. Derive inheritance
    # directly from the pinned dialect, parser, generator, and typing modules.
    for _ in range(len(dialects)):
        changed = False
        for name, source in sources.items():
            parts = name.split("/")
            stem = Path(name).stem
            if len(parts) != 3 or parts[0] != "sqlglot" or parts[1] not in {"dialects", "parsers", "generators", "typing"} or stem not in dialects:
                continue
            for module in re.findall(r"from sqlglot\.(?:dialects|parsers|generators|typing)\.([a-z_0-9]+) import", source):
                if module in dialects and module != stem:
                    before = len(dialect_tests[module])
                    dialect_tests[module].update(dialect_tests[stem])
                    changed |= len(dialect_tests[module]) != before
        if not changed:
            break
    def encode(mapping):
        return {k: sorted(test_numbers[t] for t in v) for k, v in mapping.items() if v}
    index = {"sources": sources, "test_ids": tests, "references": encode(refs), "dialects": encode(dialect_tests), "fixtures": encode(fixture_tests), "boundary_files": sorted(boundary_files)}
    with gzip.open(sys.argv[3], "wt") as output:
        json.dump(index, output, separators=(",", ":"))


if __name__ == "__main__":
    main()
