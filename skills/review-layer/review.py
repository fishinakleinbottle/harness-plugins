#!/usr/bin/env python3
"""Add the review layer to an HTML page, and read or answer its comments.

The review layer lets a reviewer select text in a local HTML page (in Chrome),
leave typed comments, and save them to <page>.comments.json beside the page.
An agent answers them with this script, which keeps the JSON file and the
page's embedded copy of the comments identical.

Usage:
  review.py add    PAGE.html             add the layer, or upgrade it in place
  review.py status PAGE.html [--all]     list comments waiting on the agent
  review.py reply  PAGE.html ID --body TEXT [--status addressed|question]
  review.py bump   PAGE.html             record that the page content changed

Standard library only. See SKILL.md in this folder for the full workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SCHEMA = "niab-review/1"
LAYER_VERSION = "1"

LAYER_START = "<!-- review-layer:start"
LAYER_END = "<!-- review-layer:end -->"
DATA_START = "<!-- review-data:start"
DATA_END = "<!-- review-data:end -->"
DATA_RE = re.compile(r'(<script type="application/json" id="review-data">)(.*?)(</script>)', re.S)

TYPES = {"improve", "delete", "explain", "note"}
STATUSES = {"open", "question", "addressed", "resolved"}


# --------------------------------------------------------------------------
# Comment model (mirrors norm() and mergeComment() in review-layer.js)
# --------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def norm(c: dict) -> dict:
    created = c.get("createdAt") or now_iso()
    a = c.get("anchor")
    out: dict[str, Any] = {
        "id": str(c["id"]),
        "type": c.get("type") if c.get("type") in TYPES else "note",
        "body": str(c.get("body") or ""),
        "status": c.get("status") if c.get("status") in STATUSES else "open",
        "anchor": None if not a else {
            "quote": str(a.get("quote") or ""),
            "prefix": str(a.get("prefix") or ""),
            "suffix": str(a.get("suffix") or ""),
            "sectionId": a.get("sectionId"),
            "sectionTitle": a.get("sectionTitle"),
            "position": a.get("position") or 0,
        },
        "documentRevision": c.get("documentRevision") or 1,
        "createdAt": created,
        "updatedAt": c.get("updatedAt") or created,
        "statusAt": c.get("statusAt") or created,
        "replies": [
            {"id": str(r["id"]), "author": "claude" if r.get("author") == "claude" else "you",
             "body": str(r.get("body") or ""), "at": r.get("at") or created}
            for r in (c.get("replies") or []) if isinstance(r, dict) and r.get("id")
        ],
    }
    if c.get("deleted"):
        out["deleted"] = True
        out["deletedAt"] = c.get("deletedAt") or created
    return out


def merge_comment(a: dict | None, b: dict) -> dict:
    b = norm(b)
    if a is None:
        return b
    core = b if b["updatedAt"] > a["updatedAt"] else a
    st = b if b["statusAt"] > a["statusAt"] else a
    replies: dict[str, dict] = {}
    for r in a["replies"] + b["replies"]:
        replies.setdefault(r["id"], r)
    merged = dict(core, status=st["status"], statusAt=st["statusAt"],
                  replies=sorted(replies.values(), key=lambda r: r["at"]))
    if a.get("deleted") or b.get("deleted"):
        merged["deleted"] = True
        merged["deletedAt"] = a.get("deletedAt") or b.get("deletedAt")
    return norm(merged)


def merge_lists(*lists: list | None) -> dict[str, dict]:
    store: dict[str, dict] = {}
    for lst in lists:
        for c in lst or []:
            if isinstance(c, dict) and c.get("id"):
                store[str(c["id"])] = merge_comment(store.get(str(c["id"])), c)
    return store


# --------------------------------------------------------------------------
# Page and file access
# --------------------------------------------------------------------------

class Page:
    def __init__(self, path: str):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            sys.exit(f"error: {self.path} does not exist")
        self.html = self.path.read_text(encoding="utf-8")
        self.json_path = self.path.with_name(self.path.stem + ".comments.json")
        self.review_id: str | None = None

    def embedded(self) -> dict | None:
        found = DATA_RE.findall(self.html)
        if not found:
            return None
        if len(found) > 1:
            sys.exit("error: the page has more than one review-data block")
        return json.loads(found[0][1].replace("<\\/", "</"))

    def file_data(self) -> dict | None:
        if not self.json_path.is_file():
            return None
        data = json.loads(self.json_path.read_text(encoding="utf-8") or "{}")
        if data.get("document") and data["document"] != self.path.name:
            sys.exit(f"error: {self.json_path.name} holds comments for {data['document']}, not {self.path.name}")
        emb = self.embedded() or {}
        if emb.get("reviewId") and data.get("reviewId") and data["reviewId"] != emb["reviewId"]:
            sys.exit(f"error: {self.json_path.name} holds comments for an earlier version of this page "
                     f"(review {data['reviewId']}, page is review {emb['reviewId']}). Move it aside and try again.")
        return data

    def load(self) -> tuple[dict[str, dict], int]:
        emb = self.embedded()
        self.review_id = (emb or {}).get("reviewId")
        if emb is None:
            sys.exit(f"error: {self.path.name} has no review layer. Run: review.py add {self.path}")
        fdata = self.file_data() or {}
        store = merge_lists(emb.get("comments"), fdata.get("comments"))
        revision = max(int(emb.get("documentRevision") or 1), int(fdata.get("documentRevision") or 0))
        return store, revision

    def save(self, store: dict[str, dict], revision: int) -> None:
        comments = sorted(store.values(), key=lambda c: (c["createdAt"], c["id"]))
        embedded = {"schema": SCHEMA, "document": self.path.name, "documentRevision": revision, "comments": comments}
        if self.review_id:
            embedded = {"schema": SCHEMA, "document": self.path.name, "reviewId": self.review_id,
                        "documentRevision": revision, "comments": comments}
        blob = json.dumps(embedded, indent=2, ensure_ascii=False).replace("</", "<\\/")
        self.html = DATA_RE.sub(lambda m: m.group(1) + "\n" + blob + "\n" + m.group(3), self.html, count=1)
        self.path.write_text(self.html, encoding="utf-8")
        payload = {
            "schema": SCHEMA, "document": self.path.name, **({"reviewId": self.review_id} if self.review_id else {}),
            "documentRevision": revision,
            "about": (f"Review comments on {self.path.name}. anchor.quote is the page text the comment refers to. "
                      "status: open (waiting on the agent) | question (the agent needs an answer) | "
                      "addressed (the agent replied or changed the page) | resolved (closed by the reviewer)."),
            "savedAt": now_iso(), "comments": comments,
        }
        self.json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def layer_sources() -> tuple[str, str]:
    return ((HERE / "review-layer.css").read_text(encoding="utf-8"),
            (HERE / "review-layer.js").read_text(encoding="utf-8"))


def layer_fingerprint() -> str:
    """Short hash of the current CSS and JS. Stamped into each page so that
    `status` can tell when a page carries an older copy of the layer."""
    css, js = layer_sources()
    return hashlib.sha256((css + "\0" + js).encode("utf-8")).hexdigest()[:12]


FINGERPRINT_RE = re.compile(r"<!-- review-layer:start v\S+ build ([0-9a-f]{12})")


def layer_block() -> str:
    css, js = layer_sources()
    return (f"{LAYER_START} v{LAYER_VERSION} build {layer_fingerprint()}. Generated from {HERE}; do not edit here. "
            f"Re-run review.py add to upgrade. -->\n"
            f'<style id="review-layer-style">\n{css}</style>\n'
            f'<script id="review-layer-script">\n{js}</script>\n'
            f"{LAYER_END}")


def data_block(name: str) -> str:
    data = {"schema": SCHEMA, "document": name, "reviewId": "rv-" + secrets.token_hex(6),
            "documentRevision": 1, "comments": []}
    return (f"{DATA_START}: review comments, kept in sync with the .comments.json file by review.py -->\n"
            f'<script type="application/json" id="review-data">\n{json.dumps(data, indent=2)}\n</script>\n'
            f"{DATA_END}")


def cmd_add(args) -> None:
    page = Page(args.page)
    html = page.html
    has_layer = LAYER_START in html
    has_data = bool(DATA_RE.search(html))
    if not has_layer and (has_data or 'class="rv-panel"' in html or "rv-pop" in html):
        sys.exit("error: this page carries an older inline review layer without markers. "
                 "Remove its review-layer <style> rules and <script>, keep the review-data block, then run add again.")
    if has_layer:
        start = html.index(LAYER_START)
        end = html.index(LAYER_END, start) + len(LAYER_END)
        html = html[:start] + layer_block() + html[end:]
        action = "upgraded"
    else:
        close = html.lower().rfind("</body>")
        insert = ("\n" + (data_block(page.path.name) + "\n" if not has_data else "") + layer_block() + "\n")
        html = html + insert if close == -1 else html[:close] + insert + html[close:]
        action = "added"
    page.path.write_text(html, encoding="utf-8")
    print(f"Review layer {action} in {page.path}")
    print(f"Comments will save to {page.json_path}")


def visible_text(html_text: str) -> str:
    """Page text as the layer's index sees it: no script, style, or comments,
    tags removed without adding spaces, entities decoded, whitespace collapsed."""
    body = re.sub(r"<(script|style|noscript|template)\b.*?</\1\s*>", "", html_text, flags=re.S | re.I)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", body)).split())


def cmd_status(args) -> None:
    page = Page(args.page)
    store, revision = page.load()
    live = [c for c in store.values() if not c.get("deleted")]
    shown = live if args.all else [c for c in live if c["status"] == "open"]
    shown.sort(key=lambda c: (c["anchor"] is not None, (c["anchor"] or {}).get("position", 0), c["createdAt"]))
    counts = {s: sum(1 for c in live if c["status"] == s) for s in ("open", "question", "addressed", "resolved")}
    print(f"{page.path.name} · revision {revision} · " + (", ".join(f"{n} {s}" for s, n in counts.items() if n) or "no comments"))
    stamp = FINGERPRINT_RE.search(page.html)
    if LAYER_START not in page.html:
        print("note: this page has an older inline layer without markers; it can't be upgraded automatically.")
    elif not stamp or stamp.group(1) != layer_fingerprint():
        print(f"note: this page's review layer is out of date. Run: review.py add {page.path}")
    if not shown:
        print("Nothing waiting on you." if not args.all else "No comments.")
    for c in shown:
        a = c["anchor"]
        print(f"\n[{c['id']}] {c['type'].upper()} · {c['status']} · "
              + (a["sectionTitle"] or a["sectionId"] or "no section" if a else "general comment"))
        if a:
            in_page = " ".join(a["quote"].split()) in visible_text(page.html)
            print(f"  quote{'' if in_page else ' (no longer on the page)'}: {a['quote']}")
        print(f"  comment: {c['body'] or '(none)'}")
        for r in c["replies"]:
            print(f"  {r['author']}: {r['body']}")


def cmd_reply(args) -> None:
    page = Page(args.page)
    store, revision = page.load()
    c = store.get(args.id)
    if not c or c.get("deleted"):
        sys.exit(f"error: no comment with id {args.id}")
    at = now_iso()
    c["replies"].append({"id": "r-" + secrets.token_hex(5), "author": "claude", "body": args.body.strip(), "at": at})
    c["status"], c["statusAt"] = args.status, at
    store[c["id"]] = norm(c)
    page.save(store, revision)
    print(f"Replied to {c['id']} and set it to {args.status}.")


def cmd_bump(args) -> None:
    page = Page(args.page)
    store, revision = page.load()
    page.save(store, revision + 1)
    print(f"{page.path.name} is now revision {revision + 1}. Open pages will ask the reviewer to reload.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="add the review layer to a page, or upgrade it")
    a.add_argument("page")
    s = sub.add_parser("status", help="list comments waiting on the agent")
    s.add_argument("page")
    s.add_argument("--all", action="store_true", help="include addressed, question, and resolved comments")
    r = sub.add_parser("reply", help="reply to a comment and set its status")
    r.add_argument("page")
    r.add_argument("id")
    r.add_argument("--body", required=True)
    r.add_argument("--status", choices=["addressed", "question"], default="addressed")
    b = sub.add_parser("bump", help="record that the page content changed (once per round of edits)")
    b.add_argument("page")
    args = p.parse_args()
    {"add": cmd_add, "status": cmd_status, "reply": cmd_reply, "bump": cmd_bump}[args.cmd](args)


if __name__ == "__main__":
    main()
