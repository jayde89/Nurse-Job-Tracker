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
from dataclasses import asdict, dataclass
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
                 "Marked active", "URL"]

# ── what a status means to the dashboard ─────────────────────────────
# You set Status by hand. The scanner only ever writes "unapplied" on a
# brand-new row, or "closed" when a posting disappears while you had not
# applied to it.
#
#   ACTIVE    an application in flight. It is remembered for as long as it
#             stays in flight, it gets its own section, and it comes OFF
#             every list of jobs to apply to — you already did.
#   ARCHIVED  done with. Off the main page, still in the ledger. "closed"
#             is the scanner's own: the posting stopped appearing.
#   OPEN      still a candidate. This is the only state that belongs in
#             "Worth applying to now".
#
# Anything unrecognised is treated as OPEN, so a typo in the CSV shows the
# job to you again rather than silently swallowing it.
ACTIVE_STATUS = {"applied", "pending", "interviewing", "offer"}
ARCHIVED_STATUS = {"rejected", "declined", "withdrawn", "closed"}

# Most-advanced first, so an offer never sorts below a bare "applied".
ACTIVE_ORDER = {"offer": 0, "interviewing": 1, "pending": 2, "applied": 3}

# The ones you set yourself when an application ends. "closed" is excluded:
# it means the posting vanished while you had not applied, which is not
# something you did and does not belong in a list of your outcomes.
FINISHED_STATUS = {"rejected", "declined", "withdrawn"}


def normalize_status(value) -> str:
    """A blank Status is an unapplied one. Case and spacing never matter."""
    return (value or "").strip().lower() or "unapplied"


def is_active(value) -> bool:
    """An application in flight."""
    return normalize_status(value) in ACTIVE_STATUS


def is_open(value) -> bool:
    """Still worth showing you as something to apply to."""
    return normalize_status(value) not in (ACTIVE_STATUS | ARCHIVED_STATUS
                                           | FINISHED_STATUS)


def active_applications(ledger) -> list:
    """
    Your applications in flight, read from the LEDGER rather than from the
    postings this scan happened to return.

    That distinction is the whole difference between a tracker and a list.
    A posting you applied to is among the likeliest to disappear — the
    employer fills it, or pulls it down while they interview — and this
    section used to be built from the scan's own results, so the moment
    that happened your application dropped off the dashboard entirely. The
    row was still sitting in applications.csv; nothing put it in front of
    you. An application in progress is exactly the thing a job tracker must
    not forget.
    """
    rows = [r for r in ledger.values() if is_active(r.get("Status"))]
    rows.sort(key=lambda r: (
        ACTIVE_ORDER.get(normalize_status(r.get("Status")), 9),
        r.get("Applied On") or r.get("Marked active") or "",
        r.get("Employer") or ""))
    return rows


def finished_applications(ledger) -> list:
    rows = [r for r in ledger.values()
            if normalize_status(r.get("Status")) in FINISHED_STATUS]
    rows.sort(key=lambda r: (r.get("Applied On") or r.get("Marked active") or "",
                             r.get("Employer") or ""), reverse=True)
    return rows


def applied_on(row) -> str:
    """
    The date to show beside an application.

    "Applied On" is yours and the scanner never writes it — but you are
    usually marking a job applied by editing a CSV on a phone, where typing
    a date as well is exactly the step that gets skipped. So the scanner
    keeps its own "Marked active": the first scan at which it saw the row
    in an active status. Yours wins when you filled it in.
    """
    return (row.get("Applied On") or "").strip() or (
        row.get("Marked active") or "").strip()


def still_listed(row, now) -> bool:
    """Was this posting in the scan that just ran?"""
    return (row.get("Last seen") or "") == now

# The two buckets a new grad can act on today.
ENTRY_LEVEL = ("STAFF_NURSE_I", "NO_EXPERIENCE")


@dataclass
class Digest:
    """Everything the two renderers draw, decided once in build()."""
    shown: list
    top: list           # entry-level and unapplied — the day's action list
    new: list           # new since last scan, unapplied
    watch: list         # needs experience you do not have yet
    active: list        # ledger rows: applications in flight
    finished: list      # ledger rows: rejected / declined / withdrawn
    review: list
    hidden: int
    now: str
    quick: bool = False


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
        if key not in live and is_open(row.get("Status")):
            row["Status"] = "closed"

    # Remember when an application became active. This is a scanner-owned
    # column: "Applied On" is yours and is never written here, but it is
    # also the field that gets skipped when you are editing a CSV on a
    # phone, and the dashboard needs a date to sort by and to show. Written
    # once, on the first scan that sees the row active, and cleared only if
    # you put the row back to unapplied.
    for row in ledger.values():
        if is_active(row.get("Status")):
            if not (row.get("Marked active") or "").strip():
                row["Marked active"] = now[:10]
        elif is_open(row.get("Status")):
            row["Marked active"] = ""

    save_ledger(ledger)

    for p in shown:
        p.status = ledger[p.key]["Status"]

    # A job you have applied to is not a job to apply to. Everything you
    # have marked — active or finished — comes off the lists below and
    # lives in its own section, which is what "remove it from the main
    # page" means. Only ACUTE_REQUIRED is still hidden outright; nothing
    # here is ever dropped, only moved.
    open_shown = [p for p in shown if is_open(getattr(p, "status", ""))]
    new_open = [p for p in new if is_open(getattr(p, "status", ""))]
    top = [p for p in open_shown if p.bucket in ENTRY_LEVEL]
    # "Watching" is the roles needing experience you do not have yet, as
    # the README has always described it. It used to be every unapplied
    # posting that was not new, so an entry-level job appeared in both
    # "Worth applying to now" and here, in the same digest, twice.
    watch = [p for p in open_shown if p not in top and p not in new_open]

    d = Digest(shown=shown, top=top, new=new_open, watch=watch,
               active=active_applications(ledger),
               finished=finished_applications(ledger),
               review=review, hidden=hidden, now=now, quick=quick)

    html = render(d)
    with open("digest.html", "w") as f:
        f.write(html)

    # The repo is public and served by GitHub Pages, so the same HTML goes
    # to index.html: Pages serves index.html at the site root, and without
    # it the bare URL 404s and you have to remember to type /digest.html.
    # Written as a copy rather than a redirect so both paths keep working —
    # digest.html is what earlier commits and any saved bookmark point at.
    with open("index.html", "w") as f:
        f.write(html)

    # DIGEST.md still matters: GitHub renders Markdown on mobile without
    # waiting for a Pages build, and it is what the commit history shows as
    # a readable diff between scans. Pages is the nicer read; DIGEST.md is
    # the one that always works.
    with open("DIGEST.md", "w") as f:
        f.write(render_md(d))

    for p in shown:
        seen.setdefault(p.key, now)
    save_seen(seen)
    return shown, new


def render_md(d):
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

    def app_row(r):
        detail = (r.get("Details") or "").replace("|", "/")
        title = (r.get("Title") or "").replace("|", "/")
        role = f"[{title}]({r.get('URL')})" + (f"<br>{detail}" if detail else "")
        since = applied_on(r) or "—"
        # Whether the posting is still up is real information about an
        # application in flight: a listing that comes down is usually the
        # role being filled or frozen.
        last = (r.get("Last seen") or "")[:10]
        listing = ("still listed" if still_listed(r, d.now)
                   else f"not listed since {last}" if last else "not listed")
        return (f"| **{normalize_status(r.get('Status'))}** | {since} | {role} | "
                f"{r.get('Employer')} | {r.get('Location')} | {listing} |")

    def app_table(rows, empty):
        if not rows:
            return f"_{empty}_"
        return ("| Status | Since | Role | Employer | Location | Listing |\n"
                "|---|---|---|---|---|---|\n"
                + "\n".join(app_row(r) for r in rows))

    finished_line = "\n".join(
        f"- **{normalize_status(r.get('Status'))}** — [{r.get('Title')}]"
        f"({r.get('URL')}), {r.get('Employer')}"
        + (f" ({applied_on(r)})" if applied_on(r) else "")
        for r in d.finished[:20]) or "_Nothing closed out yet._"

    return f"""# Staff RN openings within two hours of Oakland

_Scanned {d.now.replace('T', ' ')[:16]} UTC. {len(d.shown)} shown, {d.hidden} hidden
as acute-care-required._

**{len(d.top)} worth your attention** — Level I or no experience required,
and not already in your pile. {len(d.watch)} more need experience you do not
have yet; they are here to watch, not to apply to.
{f"You have {len(d.active)} application{'s' if len(d.active) != 1 else ''} in progress."
 if d.active else "Nothing sent yet."}

## Worth applying to now — {len(d.top)}

{table(d.top, 'Nothing entry-level open right now.')}

## In progress — {len(d.active)}

Applications you have marked. These are kept here whatever happens to the
posting, and they no longer appear in the lists above.

{app_table(d.active, 'Nothing sent yet. Set Status to applied in applications.csv.')}

## New since last scan — {len(d.new)}

{table(d.new, 'Nothing new this run.')}

## Watching — {len(d.watch)}

Experience you do not have yet. Here so you can see them coming, not to
apply to today.

{table(d.watch, 'Nothing waiting.')}

## Closed out — {len(d.finished)}

{finished_line}

## Location needs checking — {len(d.review)}

{chr(10).join(f'- {p.title} — {p.employer}, {p.location}' for p in d.review[:20])
 or '_Every location resolved._'}

---

Each row shows the requirement sentence its verdict rests on. If a quote does
not support its label, the rule is wrong — that has happened six times in this
project. Read the quote before trusting the label.

**To track an application**, change its **Status** column in
`applications.csv`:

| Set it to | What happens |
|---|---|
| `applied`, `pending`, `interviewing`, `offer` | Moves to **In progress** and off every list above |
| `rejected`, `declined`, `withdrawn` | Moves to **Closed out** |
| `unapplied` | Comes back to the main lists |

The scanner never writes Status, Applied On or Notes. **Since** shows your
Applied On when you filled it in, and otherwise the date this scanner first
saw the row marked. A posting that stops appearing is marked `closed` only
if you had not applied to it — your applications are never overwritten.
"""


def render(d):
    shown, new, review, now, quick = d.shown, d.new, d.review, d.now, d.quick
    hidden = d.hidden

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

    def acard(r):
        """A card for a ledger row rather than a live posting."""
        status = normalize_status(r.get("Status"))
        last = (r.get("Last seen") or "")[:10]
        listing = ("still listed" if still_listed(r, now)
                   else f"not listed since {last}" if last else "not listed")
        since = applied_on(r)
        return f"""
      <li class="job app">
        <div class="meta"><span class="status">{esc(status)}</span>
          {f'<span class="since">since {esc(since)}</span>' if since else ""}
          <span class="listing">{esc(listing)}</span></div>
        <h3><a href="{esc(r.get('URL'))}">{esc(r.get('Title'))}</a></h3>
        {f'<p class="detail">{esc(r.get("Details"))}</p>' if r.get("Details") else ""}
        <p class="where">{esc(r.get('Employer'))} &middot; {esc(r.get('Location'))}</p>
        {f'<p class="notes">{esc(r.get("Notes"))}</p>' if r.get("Notes") else ""}
      </li>"""

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
  .note{{color:var(--dim);font-size:.85rem;margin:-.4rem 0 .6rem}}
  .job.app{{border-left:3px solid var(--signal);padding-left:.8rem}}
  .status{{color:var(--signal);font-weight:600;text-transform:uppercase;
          letter-spacing:.04em}}
  .since,.listing{{color:var(--dim)}}
  .notes{{margin:.35rem 0 0;font-size:.85rem;color:var(--ink)}}
  footer{{margin-top:3rem;padding-top:1rem;border-top:1px solid var(--line);
         color:var(--dim);font-size:.8rem}}
  a:focus-visible{{outline:2px solid var(--signal);outline-offset:3px}}
</style></head><body><div class="wrap">
<header>
  <h1>Staff RN openings within two hours of Oakland</h1>
  <p class="sub">Scanned {esc(now.replace('T',' ')[:16])} UTC &middot;
     {len(shown)} shown &middot; {hidden} hidden as acute-care-required
     {f' &middot; {len(d.active)} application' + ('s' if len(d.active) != 1 else '') + ' in progress' if d.active else ''}
     {' &middot; quick mode, requirements not analysed' if quick else ''}</p>
  <p class="tallies">{tally}</p>
</header>

<h2>Worth applying to now &mdash; {len(d.top)}</h2>
<ul>{''.join(card(p) for p in d.top) or '<li class="empty">Nothing entry-level open right now.</li>'}</ul>

<h2>In progress &mdash; {len(d.active)}</h2>
<p class="note">Applications you have marked. Kept here whatever happens to
the posting, and gone from every list above.</p>
<ul>{''.join(acard(r) for r in d.active) or '<li class="empty">Nothing sent yet. Set Status in applications.csv once you apply.</li>'}</ul>

<h2>New since last scan &mdash; {len(new)}</h2>
<ul>{''.join(card(p) for p in new) or '<li class="empty">Nothing new this run.</li>'}</ul>

<h2>Watching &mdash; {len(d.watch)}</h2>
<p class="note">Experience you do not have yet. Here so you can see them
coming, not to apply to today.</p>
<ul>{''.join(card(p) for p in d.watch) or '<li class="empty">Nothing waiting.</li>'}</ul>

<h2>Closed out &mdash; {len(d.finished)}</h2>
<ul>{''.join(acard(r) for r in d.finished[:20]) or '<li class="empty">Nothing closed out yet.</li>'}</ul>

<h2>Location needs checking &mdash; {len(review)}</h2>
<ul>{''.join(f'<li class="job"><h3>{esc(p.title)}</h3><p class="where">{esc(p.employer)} &middot; {esc(p.location)}</p></li>' for p in review[:20]) or '<li class="empty">Every location resolved.</li>'}</ul>

<footer>Set <strong>Status</strong> in applications.csv to
<code>applied</code>, <code>pending</code>, <code>interviewing</code> or
<code>offer</code> and the job moves to <strong>In progress</strong> and off
every list above; <code>rejected</code>, <code>declined</code> or
<code>withdrawn</code> moves it to <strong>Closed out</strong>;
<code>unapplied</code> brings it back. The scanner never writes Status,
Applied On or Notes. <em>Since</em> is your Applied On where you filled it
in, otherwise the date this scanner first saw the row marked. A posting that
disappears is marked closed only if you had not applied to it.<br><br>
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
    print("wrote DIGEST.md, digest.html, index.html, applications.csv, "
          "state/seen.json")
