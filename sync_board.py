#!/usr/bin/env python3
"""
Fold the board's marks back into applications.csv.

  python3 sync_board.py marks/            a directory of mark documents
  python3 sync_board.py marks.json        one JSON file (object or list)
  python3 sync_board.py --dry-run marks/  show the changes, write nothing

The board is where you decide; the ledger is what remembers. Marking a
posting in the page writes a small document into the artifact's store:

    {"key": "PACS Group::JR179656", "mark": "not-relevant", "at": "...",
     "title": "RN", "employer": "PACS Group", "url": "..."}

Claude reads those documents out of the store and hands them to this
script, which is the only thing that writes them into the ledger.

It matches on `key`, never on the document id: the id is a slug of the key
and could in principle change, and a mark attached to the wrong posting is
exactly the kind of quiet error this project spends its effort avoiding.

What it will and will not write:

- A mark only ever moves a row that is still OPEN. A posting you have
  already marked `applied`, `rejected` or anything else by hand is left
  exactly as it is — the ledger's own columns win over a stale tick in a
  page that may have been open for days.
- `Applied On` and `Notes` are never written, by this or by anything else.
  `applied` here means the Status column and nothing more; the date is
  picked up by the scan's own `Marked active`.
- A key the ledger does not have is reported, not invented.
"""

from __future__ import annotations

import csv
import json
import os
import sys

LEDGER_PATH = "applications.csv"

# What a mark means in the ledger's own vocabulary.
STATUS_OF = {
    "not-relevant": "not relevant",
    "not_relevant": "not relevant",
    "dismissed": "not relevant",
    "applied": "applied",
}


def read_marks(path) -> list:
    """
    Accept the two shapes Claude can hand over: a directory of one-document
    JSON files (what `read_db` writes with `out_dir`), or a single JSON
    file holding a list of documents or an id-keyed object.
    """
    docs = []
    if os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            for name in sorted(files):
                if name.endswith(".json"):
                    with open(os.path.join(root, name)) as f:
                        docs.append(json.load(f))
    else:
        with open(path) as f:
            blob = json.load(f)
        if isinstance(blob, dict):
            # Either {id: doc} or a single document.
            docs = ([blob] if "key" in blob
                    else [v for v in blob.values() if isinstance(v, dict)])
        else:
            docs = list(blob)
    return [d for d in docs if isinstance(d, dict) and d.get("key")]


def apply_marks(rows, docs, verbose=True):
    """Returns (changed, skipped, unknown). `rows` is mutated in place."""
    import run_scan as R

    by_key = {r["Key"]: r for r in rows if r.get("Key")}
    changed, skipped, unknown = [], [], []

    for doc in docs:
        key, mark = doc["key"], (doc.get("mark") or "").strip().lower()
        status = STATUS_OF.get(mark)
        row = by_key.get(key)
        if row is None:
            unknown.append((key, mark))
            continue
        if status is None:
            unknown.append((key, mark or "(no mark)"))
            continue
        current = R.normalize_status(row.get("Status"))
        if current == R.normalize_status(status):
            continue
        if not R.is_open(row.get("Status")):
            # Already decided in the ledger. The ledger wins.
            skipped.append((key, current, status))
            continue
        row["Status"] = status
        changed.append((key, status, row.get("Title", "")))

    if verbose:
        for key, status, title in changed:
            print(f"  {status:<13} {title[:44]:<44} {key}")
        for key, current, status in skipped:
            print(f"  kept {current!r} over {status!r} (set by hand) — {key}")
        for key, mark in unknown:
            print(f"  !! no ledger row, or unknown mark {mark!r} — {key}")
    return changed, skipped, unknown


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("-")]
    dry = "--dry-run" in argv or "-n" in argv
    if not args:
        print(__doc__.strip().split("\n\n")[1])
        return 2

    docs = read_marks(args[0])
    with open(LEDGER_PATH, newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        rows = list(reader)

    print(f"{len(docs)} mark(s) against {len(rows)} ledger rows")
    changed, skipped, unknown = apply_marks(rows, docs)

    if not changed:
        print("nothing to write")
        return 0
    if dry:
        print(f"--dry-run: {len(changed)} row(s) would change")
        return 0

    with open(LEDGER_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(changed)} change(s) to {LEDGER_PATH}")

    # The board is built from the ledger, so it is stale the moment the
    # ledger moves. Rebuild it here rather than leaving a page that still
    # shows a posting you just ticked off.
    import board
    last = max((r.get("Last seen") or "" for r in rows), default="")
    payload = board.write(rows, now=last)
    print(f"rebuilt board.html — {len(payload['jobs'])} open postings")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
