# Blast Radius: coverage and static impact selector

The runtime selector uses two compressed indexes built exclusively from the unmodified sqlglot checkout at `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`. The sample answers are never read by the selector or the index builders.

## Index construction

1. Run the baseline tests with per-test coverage (`build_coverage.py`).
2. Profile the optimizer and executor's process-pool tasks in the parent process (`build_worker_coverage.py`), preserving Future behavior, to capture otherwise unobserved worker execution.
3. Merge both coverage maps (`merge_coverage.py`).
4. Build the source, symbol-reference, dialect-inheritance, fixture-consumer and serialization-boundary indexes (`build_index.py`).

## Runtime algorithm

Parse the unified diff and reconstruct changed Python files from the baseline source index. Combine exact changed-line coverage with enclosing boolean-expression and fallback scope coverage; for class/module attributes, use observed users and static symbol references. Fixture edits use the corresponding baseline test consumers. Unrecognized inputs and syntactically invalid edits get conservative fallbacks. Serialization boundaries use broader coverage because the parent and child processes observe different parts of serialization.

For each selected test set, try one through eight workers and greedily assign the longest tests to the least-loaded worker; choose the assignment minimizing the scorer's makespan-plus-worker-overhead cost. All output is JSONL; logs go to stderr. No networking or writing to the filesystem is required during scoring.
