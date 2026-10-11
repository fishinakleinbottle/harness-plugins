---
name: review-layer
description: Adds a comment layer to standalone HTML deliverables (reports, explainers, plans, audits) and acts on the reviewer's comments. Use after writing any HTML deliverable, and whenever the user asks you to act on, respond to, or address comments on an HTML page.
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/review.py *)
---

# Review layer

A comment layer for local HTML deliverables. The reviewer opens the page in
Chrome, selects text, and leaves typed comments. The page saves them to
`<page>.comments.json` next to itself. An agent reads those comments, changes the
page, and replies, and the replies show up in the page.

Everything goes through one Python script that uses only Python's standard
library, so there is nothing to install beyond Python 3 and Chrome.

## Files

| File | Purpose |
|---|---|
| `review.py` | CLI: adds the layer to a page, lists comments, records replies |
| `review-layer.css`, `review-layer.js` | The layer itself. `review.py add` inlines both into the page |
| `SKILL.md` | This workflow |

The commands in this file write `<skill-dir>` for this skill's folder. Your
harness names that folder when it loads the skill; run `review.py` by its
absolute path from any working directory.

## Add the layer to a page

After you write a standalone HTML deliverable, run:

```bash
python3 <skill-dir>/review.py add path/to/page.html
```

This inlines the layer before `</body>`, between `review-layer:start` and
`review-layer:end` markers, and adds an empty `review-data` block. Running it
again upgrades the layer to the current version and leaves the comments alone.

To get the best results from the page:

- Give each major `<section>` an `id` and an `<h1>` or `<h2>`. Comments are
  labelled with that section's heading.
- Comments can go anywhere inside `<main>`. To use a different region, put
  `data-review-root` on its element, and put `data-review-ignore` on anything
  inside it that shouldn't take comments.
- Leave the page's own CSS and JS inline, as usual. The layer brings its own
  colours and follows the page's light or dark background.

## What the reviewer does

1. Opens the page in Chrome.
2. Selects text, then chooses **Improve**, **Delete**, **Explain**, or **Note**
   and types a comment. **General comment** in the panel covers the whole page.
3. The first time, clicks **Save comments…** and saves `<page>.comments.json`
   in the page's folder. The page copies the folder path to the clipboard so
   the reviewer can press ⌘⇧G, ⌘V, and Return in the save dialog, which also
   reaches hidden folders like `.ai/`. After that, comments save automatically.
4. Asks the agent to act on the comments.

## Act on the comments

1. List what's waiting:

   ```bash
   python3 <skill-dir>/review.py status path/to/page.html
   ```

   If it reports that the page's layer is out of date, run `review.py add` on
   the page first; that upgrades the layer and keeps every comment.

   Each comment shows its ID, type, section, the quoted page text, and the
   reviewer's note, plus any earlier replies. Add `--all` to include comments
   that are addressed or resolved.

2. Make the changes in the page content. The quote tells you where. For
   `explain`, add the explanation to the page, not only to your reply.

3. Reply to each comment you handled:

   ```bash
   python3 <skill-dir>/review.py reply path/to/page.html c-abc123 \
     --body "What changed, or the answer."
   ```

   The status becomes `addressed`. If you need the reviewer to decide something
   first, pass `--status question` and ask in the body.

4. If you changed the page content, run this once at the end:

   ```bash
   python3 <skill-dir>/review.py bump path/to/page.html
   ```

   If the page is open, it then shows a banner asking the reviewer to reload.

Rules:

- Never edit or delete the reviewer's comments, and never edit the
  `review-data` block or the JSON file by hand. `review.py` merges the file and
  the page's embedded copy and writes both.
- If you regenerate the page from scratch, carry over the whole block between
  `review-data:start` and `review-data:end` unchanged, then run `add` again.
- Status meanings: `open` means waiting on the agent; `question` means the
  agent asked the reviewer something; `addressed` means the agent replied or
  changed the page; `resolved` means the reviewer closed it.

## Change the layer

Edit `review-layer.css` or `review-layer.js` in the plugin's source and
release a new version; don't edit an installed copy. Pages don't pick a
change up by themselves: each page
inlines a copy of the layer, stamped with a build fingerprint, so it keeps
working offline. `review.py status` flags a page whose copy is out of date, and
`review.py add` upgrades it. Every write reads the JSON file first
and merges it, so the browser and `review.py` can both write without losing
each other's changes. Comments find their text again by quote, surrounding
text, and section, so they survive edits elsewhere on the page. A comment whose
text was removed is shown as **No longer on the page**.

Requirements: Chrome, or another Chromium browser, for highlights and for
writing the file directly. Other browsers can still use **Download JSON**.
