---
name: task-dispatch
description: Use when asked to work through, execute, or finish a list of tracked tasks with subagents, whether the tasks are bd beads (bd ready, an epic, "the open beads") or unticked items in a Markdown checklist or plan file. Also use when the user says "dispatch these one by one", "keep this context clean", or hands over a single task ID to run.
---

# Task dispatch

This session is the control panel. Each task goes to a fresh `task-worker`
agent that works in its own context window, commits once, and reports back in
a fixed shape. You read every report, review every diff yourself, and keep the
task tracker as the single source of truth.

**Core principle:** the tracker's state changes only through a task's own
commit, and a task with a missing answer stays open. A wrong guess ticked as
done costs a rework nobody asked for; a BLOCKED task costs one question.

## When to use

- The owner names a set of tracked tasks to execute: beads from `bd ready` or
  an epic, or `- [ ]` items in a plan or checklist file.
- The owner hands over one task ID and wants it done in a fresh context.

Not for a plan with no tracker and tightly coupled steps; execute that inline
or with superpowers:subagent-driven-development when it is installed. Not for
work that has no task yet: write the task first, then dispatch.

## Backends

| | `bd` | `checklist` |
|---|---|---|
| Detect | `.beads/` exists and `bd` is on PATH | Otherwise, or when the owner names a file |
| Ready tasks | `bd ready`, plus `bd list --status=in_progress` for beads a worker left BLOCKED in an earlier run | Bullets that start with `- [ ]` |
| Task identity | Bead ID | The text before the first colon in the bullet, when present; else `<file>#<n>` |
| Read one task | `bd show <id>` | The bullet plus every indented line under it |
| Done looks like | `bd show <id>` reports CLOSED | The bullet reads `- [x]` |

Checklist format: a task is a top-level `- [ ]` bullet. Indented lines under it
are its detail, not separate tasks. `- [ ] T2: Add divide(a, b)` has the ID
`T2`. A bullet with no colon has no ID; refer to it by file and position.

When no file is named and there is no `.beads/`, look for a file with unticked
bullets in `.ai/plans/`, `.ai/tasks/current/`, and `docs/plans/`, newest first,
and confirm the file with the owner before dispatching.

## The loop

1. **Preflight.** Run `git status`; stop if the tree is dirty with work you
   didn't author. If HEAD is on `main` or `master`, create a branch named
   after the task list and say so. List the ready tasks. If the owner named the
   exact tasks, go. Otherwise show the list and confirm the batch.
2. **Dispatch one task.** Use the Agent tool with `subagent_type` set to
   `task-dispatch:task-worker` and the message from the template below. One
   worker at a time, always. Two workers in one tree collide on shared files,
   and the worker's clean-tree check fails for the second one. Don't create
   worktrees for workers; `bd` state doesn't follow them.
3. **Read the report.** It starts with `Status: DONE` or `Status: BLOCKED`.
4. **DONE: review the diff yourself.** Run `git show --stat <commit>` and
   `git show <commit>`, then check:
   - The change does what the task text and any referenced spec say, no more.
   - The `Gates:` line reports PASS for the gates `CLAUDE.md` documents.
   - The commit touches only files for this task and ends with the trailer
     (`Bead: <id>` or `Task: <id>`).
   - The tracker moved: the bead is CLOSED, or the bullet is ticked in that
     same commit.

   Anything off: send the same worker a message with the Agent's ID that
   names the defect and the fix, wait for its new report, and review again.
   Don't fix the worker's commit yourself.
5. **BLOCKED: don't rule.** The worker stopped because a spec, file, or
   decision is missing. Record its `Need:` line, leave the task open, and move
   to the next task that doesn't depend on it. When the owner already answered
   that question in this conversation, resume the worker with the answer and
   nothing else.
6. **Repeat** from step 2 until the batch is done or every remaining task is
   blocked.
7. **Close out** with the summary below. Push, merge, or open a PR only when
   the owner asks.

## Dispatch message template

```
Backend: <bd | checklist>
Repo path: <absolute path>
Task ID: <id, or "none">
<bd: nothing more. checklist: Checklist file: <path> and Bullet: <exact bullet text>>
Extra context: <spec paths, decisions the owner made, or "none">
```

Give the worker nothing from this session's history beyond those lines. It
reads the task, `CLAUDE.md`, and the spec itself.

## Closing summary

Write exactly these parts, in this order:

1. A table with one row per task: Task, Status (DONE, BLOCKED, or SKIPPED),
   Commit, Gates.
2. **Blocked:** each blocked task with its `Need:` line, or "none".
3. **Follow-ups:** the `Follow-ups:` lines from the reports, deduplicated, or
   "none". Add them to the tracker only when the owner says so.
4. One line on branch state: branch name, commits ahead, pushed or not.

## Red flags

| Thought | Reality |
|---|---|
| "Rather than stall, I'll implement a provisional version" | A guess ticked as done hides in the tracker. Leave it BLOCKED and ask. |
| "I'll tick all the boxes in one commit at the end" | The tracker and the commits drift apart. Each task's commit carries its own tick or close. |
| "Parallel workers will be faster" | They collide in one tree and the second one's preflight fails. Serial. |
| "A reviewer subagent per task is more thorough" | The report plus `git show` is the review. You are the reviewer. |
| "I'll fix the worker's small mistake myself" | Then the commit has two authors of intent. Send it back. |
| "The owner is away, so I'll push to unblock" | Pushing is the owner's call. End with the summary. |

## Quick reference

```bash
bd ready                      # ready beads
bd show <id>                  # one bead
grep -n '^- \[ \]' <file>     # unticked checklist items
git show --stat <commit>      # review a worker's commit
```
