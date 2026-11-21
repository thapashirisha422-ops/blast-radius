import concurrent.futures
import gzip
import json
from pathlib import Path

import coverage
import pytest


class InlinePool:
    def __init__(self, *args, initializer=None, initargs=(), **kwargs):
        if initializer:
            initializer(*initargs)

    def submit(self, fn, *args, **kwargs):
        future = concurrent.futures.Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as exc:
            future.set_exception(exc)
        return future

    def shutdown(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.shutdown()


def main():
    concurrent.futures.ProcessPoolExecutor = InlinePool

    root = Path.cwd()
    cov = coverage.Coverage(
        source=[str(root / "sqlglot")],
        config_file=False,
        data_file="/app/.worker-coverage"
    )

    class Plugin:
        @pytest.hookimpl(hookwrapper=True, tryfirst=True)
        def pytest_runtest_protocol(self, item, nextitem):
            cov.switch_context(item.nodeid)
            try:
                yield
            finally:
                cov.switch_context("__outside__")

    cov.start()
    cov.switch_context("__collection__")

    try:
        pytest.main(
            [
                "-v",
                "tests/test_optimizer.py",
                "tests/test_executor.py",
                "-p", "no:cacheprovider"
            ],
            plugins=[Plugin()]
        )
    finally:
        cov.stop()
        cov.save()

        data = cov.get_data()
        contexts = sorted(data.measured_contexts())
        ids = {name: i for i, name in enumerate(contexts)}
        files = {}

        for filename in data.measured_files():
            path = Path(filename).resolve().relative_to(root).as_posix()
            files[path] = {
                str(line): sorted(ids[c] for c in names)
                for line, names in data.contexts_by_lineno(filename).items()
            }

        with gzip.open("/app/worker_coverage.json.gz", "wt") as f:
            json.dump({"contexts": contexts, "files": files}, f)

        print("Worker coverage saved successfully.")


if __name__ == "__main__":
    main()