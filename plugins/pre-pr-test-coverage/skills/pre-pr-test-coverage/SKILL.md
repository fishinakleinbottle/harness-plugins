---
name: pre-pr-test-coverage
description: Use before opening a pull request on a Python repo, or when asked how well a branch is tested, whether coverage regressed, which endpoints have no test, or whether the tests are real. Needs pytest and pytest-cov.
allowed-tools: "Bash(python3 ${CLAUDE_SKILL_DIR}/coverage_signals.py *)"
---

# Pre-PR test coverage

coverage.py reports which lines ran. This skill adds what that number hides:
which HTTP endpoints no test exercises, which tests can't fail, which modules
no test reaches, and whether any of that got worse since the last check. A
bundled script computes those signals the same way every run and keeps a
baseline for the next one. You add the judgment the script can't: what each
gap means and which test to write first.

**Core principle:** every number in the report comes from pytest or the
script. You never count statements, tests, or endpoints by hand, and you
never invent a threshold the repo doesn't configure.

## Steps

1. **Find the runner.** `uv.lock` means `uv run pytest`; `poetry.lock` means
   `poetry run pytest`; `.venv/bin/pytest` means that; otherwise
   `python -m pytest`. `CLAUDE.md` wins when it documents a test command.
   Confirm `pytest-cov` is importable with that runner; if it isn't, say so
   and stop. Don't install packages into the project.
2. **Run the tests with coverage** from the repo root, and keep the exit code:

   ```bash
   <runner> pytest -q --cov --cov-branch --cov-report=term-missing:skip-covered --cov-report=json:coverage.json
   ```

   `--cov` with no value uses `[tool.coverage.run] source` from
   `pyproject.toml`. If that section is missing, pass `--cov=<package>`.
   When tests fail, the report has only the header, the failures, and the
   verdict FAIL. Skip the rest, and don't touch the baseline.
3. **Compute the signals.** The script finds the baseline on its own at the
   configured path when one exists:

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/coverage_signals.py --repo . --coverage coverage.json --pytest-exit-code <code> --json .ai/coverage/signals.json
   ```

   It prints a Markdown report with the gates, per-file coverage with deltas,
   endpoints, ghost tests, modules without tests, and uncollected test files.
   Exit code 1 means a gate failed. Run it with `--help` for the flags.
4. **Judge what the script flagged, and only that.**
   - Each file marked `regressed` or `new`, and each file at 0%: read its
     missing lines and name the untested behavior in one line.
   - Each `untested` or `weak` endpoint: the one test to add, with method,
     path, inputs, and the expected status and body.
   - Each ghost test: keep, fix, or delete, in a few words.
   - Each module without tests: the first test worth writing.
   - Two things the script can't see, when the diff shows them: error paths
     at decision points that matter, and tests that pass by accident through
     shared mutable state or ordering.
5. **Write the report** at the configured path, `.ai/latest-coverage-report.md`
   by default, in the shape below.
6. **Save the baseline**, only when the tests passed:

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/coverage_signals.py --repo . --coverage coverage.json --pytest-exit-code 0 --write-baseline .ai/coverage/baseline.json > /dev/null
   ```

   Tell the owner to commit `.ai/coverage/baseline.json` and the report with
   the PR, so the next run compares against them.

## Report shape

Write exactly these parts, in this order. Copy the script's tables rather
than retyping them.

1. **Header table:** date, branch, commit, the exact pytest command, the
   result line (passed, failed, skipped), and the baseline date or "first
   run".
2. **Verdict:** PASS or FAIL from the script's gates, then one sentence on
   the gate that decided it. Gates marked `info` or `skipped` don't fail the
   run; say which gate is unconfigured and how to configure it.
3. **Gates:** the script's table.
4. **Coverage by file:** the script's table, then one line per regressed,
   new, or 0% file from step 4.
5. **Endpoints:** the script's table, then **Tests to add**, a numbered list
   ordered by the risk each one closes.
6. **Ghost tests:** the script's table with an action per row.
7. **Modules without tests** and **Uncollected test files:** the script's
   sections with the first test to write per module.
8. **What the script can't see:** the error paths and order-dependent tests
   from step 4, only when you found some.
9. **Out of scope:** bugs noticed while reading, reported here and left
   alone.

Keep the report under about 120 lines. The tables carry the detail.

## Configuration

Optional `[tool.pre-pr-test-coverage]` section in `pyproject.toml`:

| Key | Default | Meaning |
|---|---|---|
| `source` | `[tool.coverage.run] source` | Source dirs to scan for endpoints and modules |
| `tests` | pytest `testpaths`, else `tests/` | Test dirs |
| `floor` | none, or the `target` in `codecov.yml` | Overall coverage floor in percent |
| `new_file_floor` | 50 | Minimum coverage for a new or never-executed file |
| `max_file_regression` | 1.0 | Points a file may drop against the baseline |
| `max_overall_regression` | 0.5 | Points the total may drop against the baseline |
| `baseline` | `.ai/coverage/baseline.json` | Where the baseline lives |
| `report` | `.ai/latest-coverage-report.md` | Where the report goes |

## Red flags

| Thought | Reality |
|---|---|
| "I'll count the statements and branches myself" | The script already did, the same way as last time. Hand counts drift between runs. |
| "80% is a sensible default floor" | An unconfigured gate is `info`, not a fail. Name the key that would configure it. |
| "While I'm here I'll fix that ghost test" | This is a report. Fixes go in **Tests to add**. |
| "Tests failed, but the coverage data is still useful" | A failing suite gives no baseline. Report the failures and stop. |
| "The script missed an endpoint, I'll add it to the table" | Add it under **Out of scope** with the decorator it uses, so the script can learn it. Don't edit the table. |

## Quick reference

```bash
python3 ${CLAUDE_SKILL_DIR}/coverage_signals.py --help
python3 ${CLAUDE_SKILL_DIR}/coverage_signals.py --repo . --coverage coverage.json --pytest-exit-code 0            # report only
python3 ${CLAUDE_SKILL_DIR}/coverage_signals.py --repo . --coverage coverage.json --no-baseline                   # ignore the baseline
python3 ${CLAUDE_SKILL_DIR}/coverage_signals.py --repo . --coverage coverage.json --write-baseline .ai/coverage/baseline.json
```
