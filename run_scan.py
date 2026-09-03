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
import geo
import highlights

STATE_DIR = "state"
SEEN_PATH = os.path.join(STATE_DIR, "seen.json")
LEDGER_PATH = "applications.csv"

LEDGER_FIELDS = ["Key", "Status", "Applied On", "Notes", "Bucket", "Title",
                 "Details", "Employer", "Location", "Drive time",
                 "Requirement evidence", "Posted", "First seen", "Last seen",
                 "URL"]

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
            # What the job actually is — facility, setting, hours, pay — so
            # a row reading "RN" is still triageable without opening it.
            p.details = highlights.summarize(p)
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
                "Title": p.title, "Details": getattr(p, "details", ""),
                "Employer": p.employer,
                "Location": p.location, "Drive time": p.drive_time_bucket or "",
                "Requirement evidence": (p.evidence or "")[:300],
                "Posted": p.posted_date or "", "First seen": now,
                "Last seen": now, "URL": p.url,
            }
        else:
            # Refresh what the employer controls; leave your columns alone.
            row.update({
                "Bucket": BUCKET_LABEL.get(p.bucket, p.bucket),
                "Title": p.title, "Details": getattr(p, "details", ""),
                "Location": p.location,
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
        f.write(render(shown, new, review, hidden, now, quick))

    # DIGEST.md matters more than the HTML for most people: GitHub renders
    # Markdown inside private repos, on mobile, for free. GitHub Pages does
    # not serve private repos on a free account, so the HTML version would
    # otherwise force a paid plan or a public repo full of your application
    # history. Read DIGEST.md on your phone; keep the HTML for desktop.
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
        # The detail line rides in the Role cell rather than in a column of
        # its own: the table already carries six, and GitHub renders <br>
        # inside a cell on both the mobile and the desktop view.
        detail = (getattr(p, "details", "") or "").replace("|", "/")
        role = f"[{p.title}]({p.url})" + (f"<br>{detail}" if detail else "")
        return (f"| {drive} | {role} | {p.employer} | "
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


def render(shown, new, review, hidden, now, quick):
    def esc(s):
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    def card(p):
        cls = p.bucket.lower()
        flag = '<span class="new">new</span>' if getattr(p, "is_new", False) else ""
        drive = f'{esc(p.drive_time_bucket)} min' if p.drive_time_bucket else "&mdash;"
        return f"""
      <li class="job {cls}">
        <div class="meta"><span class="drive">{drive}</span>{flag}</div>
        <h3><a href="{esc(p.url)}">{esc(p.title)}</a></h3>
        {f'<p class="detail">{esc(getattr(p, "details", ""))}</p>' if getattr(p, "details", "") else ""}
        <p class="where">{esc(p.employer)} &middot; {esc(p.location)}</p>
        <p class="verdict">{esc(BUCKET_LABEL.get(p.bucket, p.bucket))}</p>
        <blockquote>{esc((p.evidence or '')[:260])}</blockquote>
      </li>"""

    applied = [p for p in shown if getattr(p, "status", "") in STATUS_APPLIED]
    rest = [p for p in shown
            if not getattr(p, "is_new", False) and p not in applied]
    counts = {}
    for p in shown:
        counts[p.bucket] = counts.get(p.bucket, 0) + 1
    tally = " ".join(
        f'<span class="tally {b.lower()}">{n} {esc(BUCKET_LABEL[b].lower())}</span>'
        for b, n in sorted(counts.items(), key=lambda kv: C.RANK.get(kv[0], 9)))

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RN scan &mdash; {esc(now[:10])}</title>
<style>
  :root {{
    --ink:#12232e; --dim:#5a6b76; --line:#dfe5e8; --bg:#fbfcfc;
    --signal:#0b7285;          /* reserved: entry-level only */
    --watch:#8a6d1f;           /* unclear */
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--bg);color:var(--ink);
       font:16px/1.5 "Charter","Iowan Old Style",Georgia,serif;
       -webkit-font-smoothing:antialiased}}
  .wrap{{max-width:44rem;margin:0 auto;padding:2rem 1.25rem 5rem}}
  header{{border-bottom:2px solid var(--ink);padding-bottom:1rem;margin-bottom:1.5rem}}
  h1{{font-size:1.6rem;margin:0 0 .35rem;letter-spacing:-.01em}}
  .sub{{color:var(--dim);font-size:.9rem;margin:0}}
  .tallies{{margin:.9rem 0 0;font-size:.8rem;line-height:2}}
  .tally{{padding:.15rem .5rem;border:1px solid var(--line);border-radius:2px;
         background:#fff;color:var(--dim);white-space:nowrap}}
  .tally.staff_nurse_i,.tally.no_experience{{color:var(--signal);
         border-color:var(--signal)}}
  h2{{font-size:1rem;font-weight:600;margin:2.25rem 0 .75rem;
     padding-bottom:.35rem;border-bottom:1px solid var(--line)}}
  ul{{list-style:none;margin:0;padding:0}}
  .job{{padding:1rem 0 1.1rem;border-bottom:1px solid var(--line)}}
  .job h3{{font-size:1.05rem;margin:.15rem 0 .2rem;font-weight:600}}
  .job a{{color:inherit;text-decoration:none;
         border-bottom:1px solid rgba(18,35,46,.25)}}
  .job a:hover,.job a:focus{{border-bottom-color:var(--ink)}}
  .meta{{display:flex;gap:.6rem;align-items:baseline;font-size:.78rem;
        color:var(--dim);font-family:system-ui,sans-serif}}
  .drive{{font-variant-numeric:tabular-nums}}
  .new{{color:var(--signal);font-weight:600}}
  .detail{{margin:.15rem 0 .1rem;font-size:.86rem;color:var(--ink);
          font-family:system-ui,sans-serif}}
  .where{{margin:0;color:var(--dim);font-size:.88rem}}
  .verdict{{margin:.45rem 0 .3rem;font-size:.82rem;
           font-family:system-ui,sans-serif;color:var(--dim)}}
  .staff_nurse_i .verdict,.no_experience .verdict{{color:var(--signal);font-weight:600}}
  .unclear .verdict{{color:var(--watch)}}
  blockquote{{margin:.3rem 0 0;padding-left:.85rem;
             border-left:2px solid var(--line);color:var(--dim);
             font-size:.85rem}}
  .empty{{color:var(--dim);font-size:.9rem;padding:.5rem 0}}
  footer{{margin-top:3rem;padding-top:1rem;border-top:1px solid var(--line);
         color:var(--dim);font-size:.8rem}}
  a:focus-visible{{outline:2px solid var(--signal);outline-offset:3px}}
</style></head><body><div class="wrap">
<header>
  <h1>Staff RN openings within two hours of Oakland</h1>
  <p class="sub">Scanned {esc(now.replace('T',' ')[:16])} UTC &middot;
     {len(shown)} shown &middot; {hidden} hidden as acute-care-required
     {' &middot; quick mode, requirements not analysed' if quick else ''}</p>
  <p class="tallies">{tally}</p>
</header>

<h2>New since last scan &mdash; {len(new)}</h2>
<ul>{''.join(card(p) for p in new) or '<li class="empty">Nothing new this run.</li>'}</ul>

<h2>Not applied yet &mdash; {len(rest)}</h2>
<ul>{''.join(card(p) for p in rest) or '<li class="empty">Nothing waiting.</li>'}</ul>

<h2>Applications pending &mdash; {len(applied)}</h2>
<ul>{''.join(card(p) for p in applied) or '<li class="empty">Nothing sent yet. Set Status in applications.csv once you apply.</li>'}</ul>

<h2>Location needs checking &mdash; {len(review)}</h2>
<ul>{''.join(f'<li class="job"><h3>{esc(p.title)}</h3><p class="where">{esc(p.employer)} &middot; {esc(p.location)}</p></li>' for p in review[:20]) or '<li class="empty">Every location resolved.</li>'}</ul>

<footer>Set <strong>Status</strong> in applications.csv to move a job from
Not applied to Applications pending &mdash; the scanner never overwrites that
column. A posting that disappears is marked closed, not deleted.<br><br>
Each posting shows the requirement sentence the verdict rests on.
If a quote does not support its label, the rule is wrong &mdash; the classifier
has been wrong before. Only acute-care-required roles are hidden.</footer>
</div></body></html>"""


if __name__ == "__main__":
    quick = "--quick" in sys.argv
    print(f"Scanning{' (quick)' if quick else ''}...")
    rows, review = scan(fetch_details=not quick)
    shown, new = build(rows, review, quick)
    print(f"\n{len(shown)} shown, {len(new)} new, "
          f"{len(rows) - len(shown)} hidden as acute-required")
    print("wrote DIGEST.md, digest.html, applications.csv, state/seen.json")
