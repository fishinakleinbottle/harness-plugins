# sreeram-plugins

A Claude Code plugin marketplace. Add it once, then install any plugin in it
by name and receive updates when a new version is released.

## Add the marketplace

In a Claude Code session:

```
/plugin marketplace add fishinakleinbottle/sreeram-plugins
```

Or from your shell:

```bash
claude plugin marketplace add fishinakleinbottle/sreeram-plugins
```

## Plugins

### review-layer

Mark up any standalone HTML page that Claude writes for you. Open the page in
Chrome, select text, and leave typed comments. Claude reads the comments,
changes the page, and replies to each one inside the page.

Install:

```
/plugin install review-layer@sreeram-plugins
```

After installing, the skill runs on its own whenever Claude writes an HTML
deliverable or you ask Claude to act on comments. To run it by hand, type
`/review-layer:review-layer`.

Requirements: Google Chrome (or another Chromium browser) and Python 3. The
bundled script uses only Python's standard library.

How a review round works:

1. Ask Claude for a page, such as an explainer or a report, as a standalone
   HTML file. Claude writes it and adds the comment layer.
2. Open the page in Chrome. Select text, then choose **Improve**, **Delete**,
   **Explain**, or **Note** and type your comment. **General comment** in the
   side panel covers the whole page.
3. The first time, click **Save comments…** and save the file in the page's
   folder. After that, comments save on their own.
4. Tell Claude to act on your comments. Claude edits the page and replies to
   each comment, and the replies show up when you reload the page.

Full workflow and the script's commands:
[plugins/review-layer/skills/review-layer/SKILL.md](plugins/review-layer/skills/review-layer/SKILL.md).

### task-dispatch

Work through a tracked task list one task at a time. Each task goes to a fresh
worker agent that runs in its own context window, implements the task, runs the
project's quality gates, commits once with a task trailer, marks the task done,
and reports back in a fixed shape. Your main session stays the control panel:
it reviews each diff, sends a worker back when something is off, and ends with
a summary of what landed and what is blocked.

Works with two task sources:

- **bd beads**, when the repo has a `.beads/` folder and `bd` is installed. The
  worker claims with `bd update --claim` and closes with `bd close`.
- **A Markdown checklist**, such as a plan file with `- [ ] T1: ...` items. The
  worker ticks the item in the same commit as the work.

Install:

```
/plugin install task-dispatch@sreeram-plugins
```

Then ask for the work in plain words, for example "work through the open
beads" or "finish the unticked tasks in docs/plans/feature.md with subagents".
To run it by hand, type `/task-dispatch:task-dispatch`. To hand a single task
to a worker, dispatch the `task-dispatch:task-worker` agent.

A task whose spec or decision is missing comes back as BLOCKED with what it
needs, and stays open. Nothing is pushed unless you ask.

Requirements: git. `bd` only if you use beads. In a repo that uses `bd`, the
plugin also runs `bd prime` at session start and before compaction.

Details and the worker's report format:
[plugins/task-dispatch/skills/task-dispatch/SKILL.md](plugins/task-dispatch/skills/task-dispatch/SKILL.md)
and
[plugins/task-dispatch/agents/task-worker.md](plugins/task-dispatch/agents/task-worker.md).

### pre-pr-test-coverage

A test coverage check for Python repos that goes past the line percentage.
Claude runs pytest with pytest-cov, then a bundled script computes the same
deterministic signals every run:

- per-file line and branch coverage, with deltas against the last saved
  baseline
- HTTP endpoints (FastAPI, Starlette, Flask, Django, Litestar) and whether each
  one is exercised by a real test, only by a ghost test, or not at all
- ghost tests: no assertion, trivially true, empty, skipped, or shadowed by a
  duplicate name
- test files pytest never collects, and modules no test reaches
- quality gates with pass, fail, skipped, or info status

Claude then adds the judgment on top, such as which test to write first, and
saves a baseline so the next run reports what changed.

Install:

```
/plugin install pre-pr-test-coverage@sreeram-plugins
```

Then ask for it before a PR, for example "run the pre-PR coverage check" or
"how well is this branch tested?". To run it by hand, type
`/pre-pr-test-coverage:pre-pr-test-coverage`. The report lands at
`.ai/latest-coverage-report.md` and the baseline at
`.ai/coverage/baseline.json`; commit both with the PR.

Thresholds are off until you set them in `pyproject.toml` under
`[tool.pre-pr-test-coverage]`, or through a `target` in `codecov.yml`. The
script is standard-library Python 3.10 or later, and you can run it on its
own:

```bash
python3 plugins/pre-pr-test-coverage/skills/pre-pr-test-coverage/coverage_signals.py --help
```

Requirements: Python 3.10 or later, pytest, and pytest-cov in the project's
environment.

Details and the report shape:
[plugins/pre-pr-test-coverage/skills/pre-pr-test-coverage/SKILL.md](plugins/pre-pr-test-coverage/skills/pre-pr-test-coverage/SKILL.md).

## Try a plugin before installing

To load a plugin for one session without installing it:

```bash
claude --plugin-dir ./plugins/review-layer
```

## Add a plugin to this marketplace

1. Create `plugins/<name>/` with a `.claude-plugin/plugin.json` and the
   plugin's components, such as `skills/<skill>/SKILL.md`.
2. Add an entry to `.claude-plugin/marketplace.json` with the same `name` and
   `"source": "./plugins/<name>"`.
3. Validate, then commit and push:

   ```bash
   claude plugin validate ./plugins/<name>
   claude plugin validate .
   ```

To release a new version of a plugin, change `version` in its `plugin.json`
and push. Users receive it with `claude plugin update <name>@sreeram-plugins`.

## License

[MIT](LICENSE)
