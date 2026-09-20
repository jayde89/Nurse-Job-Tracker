#!/usr/bin/env python3
"""
Remove ledger rows the current title filter would never have admitted.

A filter fix only changes what the NEXT scan lets in. Rows already written
stay until something removes them, so tightening `EXCLUDE_TITLE` silently
leaves the offending jobs in his list — which is exactly the complaint
that prompted the fix.

    python3 purge_ineligible.py           # report only
    python3 purge_ineligible.py --apply   # rewrite the ledger

Two rules, both load-bearing:

* **Never delete a row he has touched.** Any row with a Status other than
  unapplied, or with anything in Notes or Applied On, is his — it is the
  record that he applied somewhere, and the ledger is append-only for
  precisely this reason. Such a row is reported and kept.
* **Never delete a closed row.** Closed rows are history; an application
  he sent to a job that later closed must keep its trail.
"""
import argparse
import csv
import os
import shutil
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(REPO, "applications.csv")
sys.path.insert(0, REPO)
import adapters  # noqa: E402


def his(row: dict) -> bool:
    """
    Has he put anything of his own on this row?

    "closed" is written by the scanner, not by him, so it must be tested
    separately — folding it in here reported 25 closed rows as "yours",
    which would have taught him to distrust the report.
    """
    status = (row.get("Status") or "").strip().lower()
    return (status not in ("", "unapplied", "closed")
            or bool((row.get("Notes") or "").strip())
            or bool((row.get("Applied On") or "").strip()))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="rewrite the ledger (default: report only)")
    a = ap.parse_args()

    with open(LEDGER, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames or []
        rows = list(reader)

    drop, keep, protected, history = [], [], [], []
    for r in rows:
        if adapters.title_passes(r.get("Title", "")):
            keep.append(r)
        elif his(r):
            protected.append(r)
            keep.append(r)
        elif (r.get("Status") or "").strip().lower() == "closed":
            history.append(r)       # history, not clutter
            keep.append(r)
        else:
            drop.append(r)

    print(f"{len(rows)} rows: {len(drop)} to remove, {len(keep)} kept")
    for r in drop:
        print(f"  remove  {r.get('Title','')[:64]}")
    for r in protected:
        print(f"  KEPT (your data)  {r.get('Title','')[:50]} "
              f"[{r.get('Status','')}]")
    if history:
        print(f"  kept {len(history)} closed rows as history "
              f"(ineligible titles, but the record stays)")

    if not a.apply:
        print("\nreport only — pass --apply to rewrite")
        return 0
    if not drop:
        print("nothing to do")
        return 0

    shutil.copy2(LEDGER, LEDGER + ".bak")
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in keep:
            w.writerow(r)
    os.replace(tmp, LEDGER)
    print(f"\nrewrote {LEDGER} ({len(drop)} removed, backup at .bak)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
