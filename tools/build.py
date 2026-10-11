#!/usr/bin/env python3
"""Build the per-harness packaging from the shared skills/ folder.

skills/<name>/ is the source of truth. This script:

- copies each skill into its Claude Code plugin at plugins/<name>/skills/<name>
- renders skills/task-dispatch/worker.md into the task-worker agent file for
  Claude Code, OpenCode, and Codex

Run it after you change a skill or worker.md. Run it with --check in CI or
before a commit to fail when a generated file is out of date.

The plugins hold copies rather than links because Claude Code's eval sandbox
and some installs read a plugin without following links that leave it.
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
PLUGINS = ROOT / "plugins"
WORKER_BODY = SKILLS / "task-dispatch" / "worker.md"
IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache", ".DS_Store", "*.pyc")
IGNORED_NAMES = {"__pycache__", ".pytest_cache", ".DS_Store"}

WORKER_DESCRIPTION = (
    "Executes exactly one tracked task end to end in its own context window. "
    "Claims it, implements it per the task text and any spec it references, "
    "runs the project's quality gates, commits once with a task trailer, marks "
    "the task done, and reports back in a fixed shape. Use when an orchestrator "
    "hands over a single task ID from bd (bd ready, bd show) or a single item "
    "from a Markdown checklist. Not for work that has no tracked task."
)


def render_claude(body: str) -> str:
    return (
        "---\n"
        "name: task-worker\n"
        f"description: {WORKER_DESCRIPTION}\n"
        "model: sonnet\n"
        "effort: high\n"
        "disallowedTools: Agent\n"
        "---\n\n"
        f"{body}"
    )


def render_opencode(body: str) -> str:
    # model is left unset so the worker inherits the caller's model.
    # permission.task: deny stops the worker from spawning subagents.
    return (
        "---\n"
        f"description: {WORKER_DESCRIPTION}\n"
        "mode: subagent\n"
        "permission:\n"
        "  task: deny\n"
        "---\n\n"
        f"{body}"
    )


def render_codex(body: str) -> str:
    # model is left unset so the worker inherits the parent's model.
    # A literal multi-line string keeps the Markdown body byte for byte.
    assert "'''" not in body, "worker.md must not contain three single quotes"
    return (
        'name = "task-worker"\n'
        f'description = "{WORKER_DESCRIPTION}"\n'
        'model_reasoning_effort = "high"\n'
        "developer_instructions = '''\n"
        f"{body}"
        "'''\n"
    )


def agent_files() -> dict[Path, str]:
    body = WORKER_BODY.read_text()
    if not body.endswith("\n"):
        body += "\n"
    return {
        PLUGINS / "task-dispatch" / "agents" / "task-worker.md": render_claude(body),
        ROOT / "opencode" / "agents" / "task-worker.md": render_opencode(body),
        ROOT / "codex" / "agents" / "task-worker.toml": render_codex(body),
    }


def skill_copies() -> dict[Path, Path]:
    """Map each plugin's skill copy to its source under skills/."""
    out = {}
    for skill in sorted(p for p in SKILLS.iterdir() if (p / "SKILL.md").is_file()):
        plugin = PLUGINS / skill.name
        if plugin.is_dir():
            out[plugin / "skills" / skill.name] = skill
    return out


def differences(source: Path, copy: Path) -> list[str]:
    if copy.is_symlink() or not copy.is_dir():
        return [f"{copy.relative_to(ROOT)} is not a directory"]
    out = []
    cmp = filecmp.dircmp(source, copy, ignore=list(IGNORED_NAMES))
    stack = [cmp]
    while stack:
        c = stack.pop()
        rel = Path(c.right).relative_to(ROOT)
        out += [f"{rel / n} is missing" for n in c.left_only]
        out += [f"{rel / n} is not in the source" for n in c.right_only]
        out += [f"{rel / n} differs from the source" for n in c.diff_files + c.funny_files]
        stack += list(c.subdirs.values())
    return out


def build() -> int:
    for path, content in agent_files().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        print(f"wrote {path.relative_to(ROOT)}")
    for copy, source in skill_copies().items():
        if copy.is_symlink() or copy.exists():
            if copy.is_symlink():
                copy.unlink()
            else:
                shutil.rmtree(copy)
        shutil.copytree(source, copy, ignore=IGNORE)
        print(f"copied {source.relative_to(ROOT)} -> {copy.relative_to(ROOT)}")
    return 0


def check() -> int:
    stale = []
    for path, content in agent_files().items():
        if not path.is_file() or path.read_text() != content:
            stale.append(f"{path.relative_to(ROOT)} is out of date")
    for copy, source in skill_copies().items():
        stale += differences(source, copy)
    for line in stale:
        print(line, file=sys.stderr)
    if stale:
        print("run: python3 tools/build.py", file=sys.stderr)
        return 1
    print("packaging is up to date")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="verify instead of writing")
    args = parser.parse_args(argv)
    return check() if args.check else build()


if __name__ == "__main__":
    sys.exit(main())
