"""
Decide whether this scan is worth an email, and write the body if it is.

Three scans a day means three emails a day, which is how a job alert stops
being read. So this only fires for postings that are BOTH new in this scan
and in a bucket you could actually apply to. Everything else — new roles
needing experience, and the whole standing list — stays in DIGEST.md for
when you go looking.

Writes state/alert.md and exits 0 when there is something to send.
Writes nothing and exits 0 when there isn't. Never fails the scan: a
broken notifier must not cost you the scan results.

Run after run_scan.py, which is what sets First seen / Last seen.
"""

import csv
import os
import sys

import mobile_page as MP
import run_scan as R

LEDGER = "applications.csv"
ALERT = os.path.join("state", "alert.md")
TITLE = os.path.join("state", "alert_title.txt")

# "You can apply to this today" is the phone page's rule, read from the
# Tier column the scan writes, so the email, the digest and the phone page
# all count the same jobs. Reachable soon (B_soon) is deliberately out: it
# is its own group, and not today's list (Jayde, 2026-10-31).
APPLY_NOW = MP.APPLY_NOW

# Only for a row written before the Tier column existed. Every row the
# scan touches gets a tier, so this matters for at most one scan.
WORTH_APPLYING = {"Level I / new grad", "No experience required"}


def applicable(r) -> bool:
    tier = (r.get("Tier") or "").strip()
    if tier:
        return tier in APPLY_NOW
    return r.get("Bucket") in WORTH_APPLYING

# Nearest first. Anything not listed sorts last.
DRIVE_ORDER = {"<30": 0, "30-60": 1, "60-90": 2, "90-120": 3}


def new_and_applicable(rows):
    """
    A row is new when the scan has only ever seen it once, which run_scan
    records by writing the same timestamp into First seen and Last seen.
    Comparing those two is what makes this independent of seen.json, which
    the scan rewrites before this runs.
    """
    out = []
    for r in rows:
        # Anything you have already marked — applied, interviewing, even
        # rejected — is not a new posting to tell you about. Read through
        # the same helper the dashboard uses so the email and the digest
        # can never disagree about what "already handled" means.
        if not R.is_open(r.get("Status")):
            continue
        if not applicable(r):
            continue
        first, last = r.get("First seen", ""), r.get("Last seen", "")
        if first and first == last:
            out.append(r)
    out.sort(key=lambda r: (DRIVE_ORDER.get(r.get("Drive time", ""), 9),
                            r.get("Employer", "")))
    return out


def body(rows, repo):
    lines = [
        f"**{len(rows)} new posting{'s' if len(rows) != 1 else ''} you could "
        f"apply to today.**",
        "",
        "Read the evidence column before applying — it is the requirement "
        "sentence the verdict rests on. If it does not support the label, "
        "the rule is wrong and worth reporting.",
        "",
        "| Drive | Role | Employer | Location | Why it qualified |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        ev = (r.get("Requirement evidence") or "").replace("|", "/")
        ev = " ".join(ev.split())[:180]
        title = (r.get("Title") or "").replace("|", "/")
        # The email is read on a phone, where "RN" and "RN" and "RN" is not
        # a list you can act on. The detail line is what tells them apart.
        detail = (r.get("Details") or "").replace("|", "/")
        role = f"[{title}]({r.get('URL')})" + (f"<br>{detail}" if detail else "")
        lines.append(
            f"| {r.get('Drive time') or '?'} | {role} | "
            f"{r.get('Employer')} | {r.get('Location')} | {ev} |")
    lines += [
        "",
        f"Full list, including the jobs reachable soon and the ones needing "
        f"acute experience: [DIGEST.md](https://github.com/{repo}/blob/main/DIGEST.md)",
        "",
        "When you apply, change **Status** from `unapplied` to `applied` in "
        f"[applications.csv](https://github.com/{repo}/blob/main/"
        "applications.csv) and commit. Closing this issue does not track "
        "anything — the ledger does.",
    ]
    return "\n".join(lines)


def main():
    try:
        with open(LEDGER, newline="") as f:
            rows = list(csv.DictReader(f))
    except OSError as e:
        print(f"notify: cannot read {LEDGER} ({e}) — no alert")
        return 0

    hits = new_and_applicable(rows)
    if not hits:
        print("notify: nothing new worth applying to, no email")
        return 0

    os.makedirs("state", exist_ok=True)
    repo = os.environ.get("GITHUB_REPOSITORY", "jayde89/Nurse-Job-Tracker")
    with open(ALERT, "w") as f:
        f.write(body(hits, repo))
    # The subject line. Lead with the count and the nearest drive time,
    # because that is all you see on a phone's lock screen.
    nearest = hits[0].get("Drive time") or "?"
    plural = "s" if len(hits) != 1 else ""
    with open(TITLE, "w") as f:
        f.write(f"{len(hits)} new RN posting{plural} you can apply to "
                f"(nearest {nearest} min)")
    print(f"notify: {len(hits)} new applicable posting(s) -> {ALERT}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                              # noqa: BLE001
        # Never let a notification problem fail the scan.
        print(f"notify: failed ({type(e).__name__}: {e}) — scan results stand")
        sys.exit(0)
