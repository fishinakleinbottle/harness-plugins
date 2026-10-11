# sreeram-plugins

Skills I use across projects, packaged for Claude Code, OpenCode, and Codex.
Each skill is written once under `skills/` in the
[Agent Skills](https://agentskills.io/specification) format. A build script
copies it into its Claude Code plugin under `plugins/`, and the `opencode/`
and `codex/` folders hold the agent file those harnesses need for
task-dispatch.

## Install in Claude Code

Add the marketplace once, then install plugins by name and receive updates
when a new version is released.

In a Claude Code session:

```
/plugin marketplace add fishinakleinbottle/harness-plugins
```

Or from your shell:

```bash
claude plugin marketplace add fishinakleinbottle/harness-plugins
```

Then install the plugins you want:

```
/plugin install review-layer@sreeram-plugins
/plugin install task-dispatch@sreeram-plugins
/plugin install pre-pr-test-coverage@sreeram-plugins
```

To load a plugin for one session without installing it:

```bash
claude --plugin-dir ./plugins/review-layer
```

## Install in OpenCode, Codex, or another harness

Any harness that reads `SKILL.md` can use the skills. The
[skills CLI](https://github.com/vercel-labs/skills) installs them into
`.agents/skills/` and links the folder each installed agent reads:

```bash
npx skills add fishinakleinbottle/harness-plugins
```

To install by hand, copy `skills/<name>/` to `.agents/skills/<name>/` in the
project, or to `~/.agents/skills/<name>/` for every project.

task-dispatch also needs its worker agent:

- **OpenCode:** copy `opencode/agents/task-worker.md` to `.opencode/agents/`
  in the project, or to `~/.config/opencode/agents/`.
- **Codex:** copy `codex/agents/task-worker.toml` to `.codex/agents/` in the
  project, or to `~/.codex/agents/`.

Both agent files leave the model unset, so the worker inherits the model of
the session that dispatches it. To pin one, add a `model` line to the copied
file.

Three parts are Claude Code only: the `bd prime` hooks in task-dispatch, the
eval cases under `plugins/*/evals/`, and marketplace install. The skills work
without them.

## Plugins

### review-layer

Mark up any standalone HTML page that the agent writes for you. Open the page
in Chrome, select text, and leave typed comments. The agent reads the
comments, changes the page, and replies to each one inside the page.

After installing, the skill runs on its own whenever the agent writes an HTML
deliverable or you ask it to act on comments. In Claude Code, run it by hand
with `/review-layer:review-layer`.

Requirements: Google Chrome (or another Chromium browser) and Python 3. The
bundled script uses only Python's standard library.

How a review round works:

1. Ask for a page, such as an explainer or a report, as a standalone HTML
   file. The agent writes it and adds the comment layer.
2. Open the page in Chrome. Select text, then choose **Improve**, **Delete**,
   **Explain**, or **Note** and type your comment. **General comment** in the
   side panel covers the whole page.
3. The first time, click **Save comments…** and save the file in the page's
   folder. After that, comments save on their own.
4. Tell the agent to act on your comments. It edits the page and replies to
   each comment, and the replies show up when you reload the page.

Full workflow and the script's commands:
[skills/review-layer/SKILL.md](skills/review-layer/SKILL.md).

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

Ask for the work in plain words, for example "work through the open beads" or
"finish the unticked tasks in docs/plans/feature.md with subagents". In Claude
Code, run it by hand with `/task-dispatch:task-dispatch`, or hand a single task
to the `task-dispatch:task-worker` agent.

A task whose spec or decision is missing comes back as BLOCKED with what it
needs, and stays open. Nothing is pushed unless you ask.

Requirements: git. `bd` only if you use beads. In Claude Code, the plugin also
runs `bd prime` at session start and before compaction in a repo that uses
`bd`.

Details and the worker's report format:
[skills/task-dispatch/SKILL.md](skills/task-dispatch/SKILL.md) and
[skills/task-dispatch/worker.md](skills/task-dispatch/worker.md).

### pre-pr-test-coverage

A test coverage check for Python repos that goes past the line percentage. The
agent runs pytest with pytest-cov, then a bundled script computes the same
deterministic signals every run:

- per-file line and branch coverage, with deltas against the last saved
  baseline
- HTTP endpoints (FastAPI, Starlette, Flask, Django, Litestar) and whether each
  one is exercised by a real test, only by a ghost test, or not at all
- ghost tests: no assertion, trivially true, empty, skipped, or shadowed by a
  duplicate name
- test files pytest never collects, and modules no test reaches
- quality gates with pass, fail, skipped, or info status

The agent then adds the judgment on top, such as which test to write first, and
saves a baseline so the next run reports what changed.

Ask for it before a PR, for example "run the pre-PR coverage check" or "how
well is this branch tested?". In Claude Code, run it by hand with
`/pre-pr-test-coverage:pre-pr-test-coverage`. The report lands at
`.ai/latest-coverage-report.md` and the baseline at
`.ai/coverage/baseline.json`; commit both with the PR.

Thresholds are off until you set them in `pyproject.toml` under
`[tool.pre-pr-test-coverage]`, or through a `target` in `codecov.yml`. The
script is standard-library Python 3.10 or later, and you can run it on its
own:

```bash
python3 skills/pre-pr-test-coverage/coverage_signals.py --help
```

Requirements: Python 3.10 or later, pytest, and pytest-cov in the project's
environment.

Details and the report shape:
[skills/pre-pr-test-coverage/SKILL.md](skills/pre-pr-test-coverage/SKILL.md).

## Repository layout

| Path | Contents |
|---|---|
| `skills/<name>/` | The skill: `SKILL.md` plus its scripts. The only copy |
| `skills/task-dispatch/worker.md` | The worker's instructions, shared by every harness |
| `plugins/<name>/` | Claude Code plugin: manifest, a generated copy of the skill, and any hooks, agents, and evals |
| `opencode/agents/` | Worker agent for OpenCode, generated from `worker.md` |
| `codex/agents/` | Worker agent for Codex, generated from `worker.md` |
| `tests/` | Unit tests for the bundled scripts |
| `tools/build.py` | Copies the skills into the plugins and generates the agent files |

Skill text names bundled files as `<skill-dir>/script.py`. Every harness
tells the agent the skill's folder when it loads the skill, so the text needs
no harness-specific variable.

The plugins hold copies rather than links: Claude Code's eval sandbox reads a
plugin without following links that leave it. `tools/build.py --check` fails
when a copy drifts from its source.

## Add a plugin to this marketplace

1. Create `skills/<name>/SKILL.md` and put any scripts next to it.
2. Create `plugins/<name>/.claude-plugin/plugin.json`. Leave the plugin's
   `skills/` folder to the build script.
3. Add an entry to `.claude-plugin/marketplace.json` with the same `name` and
   `"source": "./plugins/<name>"`.
4. Build, test, and validate:

   ```bash
   python3 tools/build.py
   python3 tools/build.py --check
   python3 -m pytest -q tests
   claude plugin validate .
   ```

5. Commit and push.

To change a skill, edit it under `skills/` and run the build script. The
plugin copies and the three agent files are generated, and `--check` fails
when they drift. The worker's text lives in `skills/task-dispatch/worker.md`.

To release a new version of a plugin, change `version` in its `plugin.json`
and push. Claude Code users receive it with
`claude plugin update <name>@sreeram-plugins`.

## License

[MIT](LICENSE)
