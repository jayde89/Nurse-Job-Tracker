#!/usr/bin/env python3
"""
Fold the phone page's marks back into applications.csv.

  python3 sync_marks.py marks.json           apply
  python3 sync_marks.py --dry-run marks.json show the changes, write nothing
  python3 sync_marks.py --paste              read JSON from stdin

The page is where he decides; the ledger is what remembers. Without this
the loop is open at one end: he can tick Applied on his phone, the scan
rewrites index.html three times a day, the marks survive because they are
keyed by ledger Key in localStorage — and applications.csv never learns
any of it. The scan keeps offering a job he applied to a week ago.

Getting the file off the phone: the page has an **Export marks** button
that downloads `rn-marks.json`, or open the page on this Mac and run

    JSON.parse(localStorage.getItem('rnjobs.marks.v2'))

in the console. Either way it is the same object, mapping ledger Key to
a one-letter mark:

    {"PACS Group::JR179656": "p", "Kentfield Hospital (AAM)::294969": "a"}

Ported from sync_board.py in the closed PR #13, which did this for a
board.html that the phone page superseded. The safety rules below are
that PR's and they were right.

What it will and will not write:

- A mark only ever moves a row that is still OPEN (blank or `unapplied`).
  A row he has already set by hand to `applied`, `rejected`, `interview`
  or anything else is left exactly as it is — the ledger's own columns
  beat a stale tick in a page that may have been open for days.
- `Applied On` and `Notes` are never written. `applied` here means the
  Status column and nothing more.
- A key the ledger does not have is reported, never invented. Keys change
  when an employer renames a posting, and a mark attached to the wrong
  job is exactly the quiet error this project spends its effort avoiding.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

LEDGER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "applications.csv")

# The page's one-letter marks, in the ledger's own vocabulary.
# 'a' is the Applied button, 'p' is Pass.
STATUS_OF = {
    "a": "applied",
    "applied": "applied",
    "p": "not relevant",
    "pass": "not relevant",
    "not-relevant": "not relevant",
}

# A row in any of these states was set deliberately and is never moved.
OPEN_STATUS = {"", "unapplied"}


def read_marks(path: str | None) -> dict:
    raw = sys.stdin.read() if path is None else open(path).read()
    data = json.loads(raw)
    if isinstance(data, list):
        # Tolerate a list of {key, mark} documents too.
        return {d["key"]: d.get("mark", "") for d in data if d.get("key")}
    if not isinstance(data, dict):
        raise SystemExit("marks must be a JSON object or list")
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("marks", nargs="?", help="rn-marks.json")
    ap.add_argument("--paste", action="store_true", help="read JSON on stdin")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not a.marks and not a.paste:
        ap.error("give a marks file or --paste")

    marks = read_marks(None if a.paste else a.marks)
    if not marks:
        print("no marks to apply")
        return 0

    with open(LEDGER_PATH) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = list(reader.fieldnames or [])

    by_key = {r.get("Key"): r for r in rows}
    applied, skipped, missing = [], [], []

    for key, mark in marks.items():
        status = STATUS_OF.get(str(mark).strip().lower())
        if not status:
            continue
        row = by_key.get(key)
        if row is None:
            missing.append((key, status))
            continue
        current = (row.get("Status") or "").strip().lower()
        if current not in OPEN_STATUS:
            skipped.append((key, current, status))
            continue
        row["Status"] = status
        applied.append((key, status, row.get("Title", "")))

    for key, status, title in applied:
        print(f"  {status:13} {title[:44]:44} {key[:40]}")
    for key, current, status in skipped:
        print(f"  kept '{current}' (not overwriting) — {key[:52]}")
    for key, status in missing:
        print(f"  NOT IN LEDGER  {key[:60]}")

    print(f"\n{len(applied)} updated, {len(skipped)} left alone, "
          f"{len(missing)} unknown")

    if a.dry_run:
        print("dry run — nothing written")
        return 0
    if not applied:
        return 0

    tmp = LEDGER_PATH + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, LEDGER_PATH)
    print(f"wrote {LEDGER_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
