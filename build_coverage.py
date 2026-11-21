import gzip
import json
from pathlib import Path

import coverage
import pytest


def main():
    root = Path.cwd()
    output = Path("/app/coverage_map.json.gz")

    cov = coverage.Coverage(
        source=[str(root / "sqlglot")],
        config_file=False,
        data_file="/app/.coverage-progress"
    )

    class CoveragePlugin:
        completed = 0

        @pytest.hookimpl(hookwrapper=True, tryfirst=True)
        def pytest_runtest_protocol(self, item, nextitem):
            cov.switch_context(item.nodeid)
            try:
                yield
            finally:
                cov.switch_context("__outside__")
                self.completed += 1

                if self.completed % 100 == 0:
                    cov.save()

    cov.start()
    cov.switch_context("__collection__")

    try:
        pytest.main(
            ["-v", "tests", "-p", "no:cacheprovider"],
            plugins=[CoveragePlugin()]
        )
    finally:
        cov.stop()
        cov.save()

        data = cov.get_data()
        contexts = sorted(data.measured_contexts())
        ids = {name: i for i, name in enumerate(contexts)}
        files = {}

        for filename in sorted(data.measured_files()):
            path = Path(filename).resolve().relative_to(root).as_posix()

            files[path] = {
                str(line): sorted(ids[c] for c in names)
                for line, names in data.contexts_by_lineno(filename).items()
            }

        with gzip.open(output, "wt") as f:
            json.dump(
                {"contexts": contexts, "files": files},
                f,
                separators=(",", ":")
            )

        print("Mapped files:", len(files))
        print("Mapped contexts:", len(contexts))
        print("Map size:", output.stat().st_size)


if __name__ == "__main__":
    main()