#!/usr/bin/env python3
"""
Daily scan runner.

  python3 run_scan.py            full scan, write digest + csv
  python3 run_scan.py --quick    skip detail fetches (fast, no classification)

Pipeline:
    adapters -> title prefilter -> geo filter -> detail fetch
             -> requirement classifier -> dedupe against seen.json
             -> digest.html + postings.csv

State lives in state/seen.json so "new since last scan" is real across runs.
Run it three times a day; anything already seen stays in the CSV but drops
out of the New section.

Suppression is narrow on purpose: only ACUTE_REQUIRED is hidden. Everything
else reaches you, ranked, with the requirement sentence attached.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone

import adapters
import classifier as C
import digest_page
import geo

STATE_DIR = "state"
SEEN_PATH = os.path.join(STATE_DIR, "seen.json")
LEDGER_PATH = "applications.csv"

LEDGER_FIELDS = ["Key", "Status", "Applied On", "Notes", "Bucket", "Title",
                 "Employer", "Location", "Drive time", "Requirement evidence",
                 "Posted", "First seen", "Last seen", "URL"]

# Status values you set by hand. The scanner only ever writes "unapplied"
# on a brand-new row, or "closed" when a posting disappears.
STATUS_APPLIED = {"applied", "pending", "interviewing", "rejected", "offer"}

BUCKET_LABEL = {
    "STAFF_NURSE_I": "Level I / new grad",
    "NO_EXPERIENCE": "No experience required",
    "UNCLEAR": "Requirements unclear",
    "GENERAL_EXPERIENCE": "Experience required, not acute",
    "ACUTE_REQUIRED": "Acute care required",
}


def load_ledger() -> dict:
    try:
        with open(LEDGER_PATH, newline="") as f:
            return {r["Key"]: r for r in csv.DictReader(f) if r.get("Key")}
    except (OSError, KeyError):
        return {}


def save_ledger(ledger: dict) -> None:
    with open(LEDGER_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS, extrasaction="ignore")
        w.writeheader()
        for row in ledger.values():
            w.writerow(row)


def load_seen() -> dict:
    try:
        with open(SEEN_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_seen(seen: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(SEEN_PATH, "w") as f:
        json.dump(seen, f, indent=1)


def scan(fetch_details=True):
    rows, review = [], []
    for ad in adapters.ADAPTERS:
        name = getattr(ad, "employer", type(ad).__name__)
        try:
            listings = ad.fetch_listings()
        except Exception as e:                              # noqa: BLE001
            print(f"  !! {name}: {e}")
            continue

        passed = [p for p in listings if adapters.title_passes(p.title)]
        in_range, needs_review, too_far = geo.partition(passed)
        print(f"  {name}: {len(listings)} listings -> {len(passed)} nurse "
              f"-> {len(in_range)} in range ({len(too_far)} far, "
              f"{len(needs_review)} review)")

        for p in in_range:
            if fetch_details:
                try:
                    p = ad.fetch_detail(p)
                except Exception as e:                      # noqa: BLE001
                    print(f"     detail failed {p.req_id}: {e}")
            v = C.classify(p.title, p.description)
            p.bucket, p.evidence, p.why = v.bucket, v.evidence, v.reason
            rows.append(p)
        review.extend(needs_review)
    return rows, review


def build(rows, review, quick=False):
    seen = load_seen()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    shown = [p for p in rows if p.bucket in C.SHOW]
    hidden = len(rows) - len(shown)
    for p in shown:
        p.is_new = p.key not in seen
    shown.sort(key=lambda p: (not p.is_new, C.RANK.get(p.bucket, 9),
                              p.drive_time_bucket or "zz", p.employer))

    new = [p for p in shown if p.is_new]

    # ---- application ledger -------------------------------------------
    # APPEND-ONLY. The scanner may add rows and may update the posting's own
    # details, but it must never touch Status / Applied On / Notes — those
    # are yours. An earlier version opened this file in "w" mode and would
    # have wiped your tracking three times a day.
    ledger = load_ledger()
    for p in shown:
        row = ledger.get(p.key)
        if row is None:
            ledger[p.key] = {
                "Key": p.key, "Status": "unapplied", "Applied On": "",
                "Notes": "", "Bucket": BUCKET_LABEL.get(p.bucket, p.bucket),
                "Title": p.title, "Employer": p.employer,
                "Location": p.location, "Drive time": p.drive_time_bucket or "",
                "Requirement evidence": (p.evidence or "")[:300],
                "Posted": p.posted_date or "", "First seen": now,
                "Last seen": now, "URL": p.url,
            }
        else:
            # Refresh what the employer controls; leave your columns alone.
            row.update({
                "Bucket": BUCKET_LABEL.get(p.bucket, p.bucket),
                "Title": p.title, "Location": p.location,
                "Drive time": p.drive_time_bucket or "",
                "Requirement evidence": (p.evidence or "")[:300],
                "Last seen": now, "URL": p.url,
            })

    # A posting that stops appearing has closed. Flag it rather than deleting
    # it, so an application you already sent keeps its history.
    live = {p.key for p in shown}
    for key, row in ledger.items():
        if key not in live and row.get("Status") == "unapplied":
            row["Status"] = "closed"

    save_ledger(ledger)

    for p in shown:
        p.status = ledger[p.key]["Status"]

    with open("digest.html", "w") as f:
        f.write(digest_page.render(digest_page.from_postings(shown),
                                   review, hidden, now, quick))

    # Both formats are written every run and both are committed.
    #
    # digest.html is the one to read: filterable, searchable, one job at a
    # time, with a reading mode for a phone. It is self-contained, so it
    # opens from a download or the Files app with no server involved.
    #
    # DIGEST.md stays because GitHub renders Markdown inside a private repo
    # for free, on mobile, with no download step — which is the one thing
    # the HTML cannot do while this repo is private. It is the fallback,
    # not the main event.
    with open("DIGEST.md", "w") as f:
        f.write(render_md(shown, new, review, hidden, now))

    for p in shown:
        seen.setdefault(p.key, now)
    save_seen(seen)
    return shown, new


def render_md(shown, new, review, hidden, now):
    applied = [p for p in shown if getattr(p, "status", "") in STATUS_APPLIED]
    rest = [p for p in shown
            if not getattr(p, "is_new", False) and p not in applied]

    def row(p):
        drive = p.drive_time_bucket or "?"
        ev = (p.evidence or "").replace("|", "/").replace("\n", " ")[:150]
        return (f"| {drive} | [{p.title}]({p.url}) | {p.employer} | "
                f"{p.location} | {BUCKET_LABEL.get(p.bucket, p.bucket)} | {ev} |")

    head = ("| Drive | Role | Employer | Location | Requirements | Evidence |\n"
            "|---|---|---|---|---|---|")

    def table(items, empty):
        if not items:
            return f"_{empty}_"
        return head + "\n" + "\n".join(row(p) for p in items)

    top = [p for p in shown
           if p.bucket in ("STAFF_NURSE_I", "NO_EXPERIENCE")]

    return f"""# Staff RN openings within two hours of Oakland

_Scanned {now.replace('T', ' ')[:16]} UTC. {len(shown)} shown, {hidden} hidden
as acute-care-required._

**{len(top)} worth your attention** — Level I or no experience required.
The other {len(shown) - len(top)} need experience you do not have yet; they are
here to watch, not to apply to.

## Worth applying to now — {len(top)}

{table(top, 'Nothing entry-level open right now.')}

## New since last scan — {len(new)}

{table(new, 'Nothing new this run.')}

## Not applied yet — {len(rest)}

{table(rest, 'Nothing waiting.')}

## Applications pending — {len(applied)}

{table(applied, 'Nothing sent yet. Set Status to applied in applications.csv.')}

## Location needs checking — {len(review)}

{chr(10).join(f'- {p.title} — {p.employer}, {p.location}' for p in review[:20])
 or '_Every location resolved._'}

---

Each row shows the requirement sentence its verdict rests on. If a quote does
not support its label, the rule is wrong — that has happened six times in this
project. Read the quote before trusting the label.

To move a job to Applications pending, change its **Status** column in
`applications.csv` from `unapplied` to `applied`. The scanner never overwrites
that column.
"""


if __name__ == "__main__":
    quick = "--quick" in sys.argv
    print(f"Scanning{' (quick)' if quick else ''}...")
    rows, review = scan(fetch_details=not quick)
    shown, new = build(rows, review, quick)
    print(f"\n{len(shown)} shown, {len(new)} new, "
          f"{len(rows) - len(shown)} hidden as acute-required")
    print("wrote DIGEST.md, digest.html, applications.csv, state/seen.json")
