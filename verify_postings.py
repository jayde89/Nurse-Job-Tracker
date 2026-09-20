#!/usr/bin/env python3
"""
Verify every posting in the ledger against every rule he has set.

Run this after ANY change to a filter, a classifier, an adapter or the
tiering — and run it again afterwards, because each pass historically
found something the last one did not. That is not a joke about diligence:
the structural check (does every row have a URL, a tier, a drive band)
passed cleanly on 2026-09-20 while twelve postings he cannot hold were
sitting in the actionable list. Structure is not eligibility.

    python3 verify_postings.py            # report, exit 1 on any violation
    python3 verify_postings.py --loop     # re-run until two clean passes
    python3 verify_postings.py --json     # machine-readable

Each check is a named function returning the rows that VIOLATE it, so a
new rule is three lines and gets picked up automatically. Add a check the
moment a bad posting reaches him; that is the whole point of the file.

Nothing here writes. It reports, and the caller decides.
"""

import argparse
import collections
import csv
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import adapters
import mobile_page

LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "applications.csv")

# His commute bands. 90-120 is listed but hidden behind a toggle; a row
# outside all four is a geocoding failure, not a distant job.
DRIVE_BANDS = ("<30", "30-60", "60-90", "90-120")

CHECKS = []


def check(name, severity="error"):
    """Register a check. It returns the rows that fail it."""
    def deco(fn):
        CHECKS.append((name, severity, fn))
        return fn
    return deco


# ── eligibility: postings he cannot hold ────────────────────────────────

@check("an actionable row is not an RN role")
def not_rn(rows, act):
    return [r for r in act if not adapters.title_passes(r["Title"])]


@check("an RN role was excluded by the title filter", severity="warn")
def over_filtered(rows, act):
    """
    The filter throwing away real jobs is as bad as letting noise in, and
    it is invisible — a suppressed row is never written to the ledger at
    all. This can only catch rows already recorded, so it is a floor, not
    a guarantee. `RN - CMC Emergency Services ... - CNA` (a bargaining
    unit, not a certified nurse assistant) is the known trap.
    """
    out = []
    for r in rows:
        t = r["Title"]
        if adapters.title_passes(t):
            continue
        # An RN title that the filter rejects is worth a human look.
        if re.search(r"\bRN\b|registered nurse|staff nurse", t, re.I):
            if not re.search(r"\b(?:LVN|LPN|CNA|aide|assistant|technician|"
                             r"anesthe|midwife|nurse practitioner|"
                             r"clinical nurse specialist|first assist)",
                             t, re.I):
                out.append(r)
    return out


@check("an actionable row demands more experience than he will have")
def too_much_experience(rows, act):
    out = []
    for r in act:
        m = mobile_page.required_months(r.get("Requirement evidence", ""))
        if m and m > mobile_page._REACHABLE_MONTHS:
            out.append(r)
    return out


@check("an actionable row demands specialty experience he cannot accrue")
def specialty_bar(rows, act):
    return [r for r in act
            if mobile_page._specialty_experience(
                r.get("Requirement evidence", ""))]


@check("the stored tier disagrees with the posting's own text")
def stale_tier(rows, act):
    """
    The ledger is rewritten by the scan, but a filter fix does not
    retroactively re-tier rows already in it. A disagreement means the
    stored value predates the current rules.
    """
    out = []
    for r in rows:
        fresh = mobile_page.tier_for(
            r.get("Bucket") or "", setting=r.get("Setting", ""),
            evidence=r.get("Requirement evidence", ""), title=r["Title"])
        if fresh != r.get("Tier"):
            out.append(dict(r, _detail=f"{r.get('Tier')} -> {fresh}"))
    return out


# ── structure: rows he cannot act on ────────────────────────────────────

@check("a row has no usable apply link")
def no_url(rows, act):
    return [r for r in rows if not (r.get("URL") or "").startswith("http")]


@check("a row has an unknown tier")
def bad_tier(rows, act):
    return [r for r in rows if r.get("Tier") not in mobile_page.TIER_HEADING]


@check("a row's commute is outside his bands")
def bad_drive(rows, act):
    return [r for r in rows if r.get("Drive time") not in DRIVE_BANDS]


@check("an actionable row quotes no evidence")
def no_evidence(rows, act):
    return [r for r in act if not (r.get("Requirement evidence") or "").strip()]


@check("an actionable row has no employer or title")
def blank_identity(rows, act):
    return [r for r in act
            if not (r.get("Employer") or "").strip()
            or not (r.get("Title") or "").strip()]


# ── coverage: sources that quietly stopped working ──────────────────────

@check("a configured source produced no rows at all")
def dead_source(rows, act):
    """
    An adapter that returns zero looks identical to an employer with no
    openings. Kentfield was invisible for weeks because the Jibe adapter
    confidently returned nothing after the hospital changed owner.

    A source that needs a credential nobody has entered is a different
    thing: it is a known gap, not a silent one, so it reports as a
    warning. It must still report — an unconfigured source that goes
    unmentioned is how the VA's four in-range hospitals stay invisible
    indefinitely.
    """
    state = os.path.join(os.path.dirname(LEDGER), "state", "sources.json")
    if not os.path.exists(state):
        return []
    try:
        with open(state) as f:
            src = json.load(f)
    except Exception:
        return []
    out = []
    for key, v in src.items():
        if v.get("status") == "failed" or not v.get("listings"):
            err = str(v.get("error") or "")
            if _NEEDS_CREDENTIAL.search(err):
                continue        # reported by needs_credential below
            out.append({"Title": key, "Employer": v.get("employer", ""),
                        "_detail": f"status={v.get('status')} "
                                   f"listings={v.get('listings')} "
                                   f"error={err[:80]}"})
    return out


# An adapter whose only problem is a missing key or token. Distinguished
# from a broken one so the error list stays actionable.
_NEEDS_CREDENTIAL = re.compile(
    r"not set|no api key|missing key|missing token|unauthori[sz]ed|401",
    re.I)


@check("a source is unconfigured and cannot run", severity="warn")
def needs_credential(rows, act):
    state = os.path.join(os.path.dirname(LEDGER), "state", "sources.json")
    if not os.path.exists(state):
        return []
    try:
        with open(state) as f:
            src = json.load(f)
    except Exception:
        return []
    return [{"Title": k, "Employer": v.get("employer", ""),
             "_detail": str(v.get("error") or "")[:110]}
            for k, v in src.items()
            if _NEEDS_CREDENTIAL.search(str(v.get("error") or ""))]


@check("a registered adapter has never been recorded by a scan")
def unrecorded_adapter(rows, act):
    """
    An adapter in ADAPTERS but absent from state/sources.json has never
    run to completion. It is not failing loudly; it simply is not there,
    and nothing in the pipeline says so.
    """
    state = os.path.join(os.path.dirname(LEDGER), "state", "sources.json")
    if not os.path.exists(state):
        return []
    try:
        with open(state) as f:
            seen = json.load(f)
    except Exception:
        return []
    known = set()
    for key in seen:
        known.add(key)
        known.add(key.split(":", 1)[-1])
    out = []
    for a in adapters.ADAPTERS:
        emp = getattr(a, "employer", None) or type(a).__name__
        if emp not in known:
            out.append({"Title": emp, "Employer": type(a).__name__,
                        "_detail": "registered but never in state/sources.json"})
    return out


@check("an agency has been unread by consecutive scans", severity="warn")
def persistently_missed(rows, act):
    """
    `degraded` is reported per scan, which makes a permanent gap look
    like a transient. NEOGOV iterated a fixed dict, so the same tail was
    starved by the wall-clock budget every single run: Santa Clara Valley
    Medical Center was not unreachable-sometimes, it was never read, and
    three scans a day of "degraded" said nothing about which.

    A missed agency whose newest ledger row is over a month old is not a
    blip — either it is being starved, or it genuinely has nothing, and
    both are worth knowing.
    """
    state = os.path.join(os.path.dirname(LEDGER), "state", "sources.json")
    if not os.path.exists(state):
        return []
    try:
        with open(state) as f:
            src = json.load(f)
    except Exception:
        return []
    missed = set()
    for v in src.values():
        missed |= set(v.get("missed") or [])
    if not missed:
        return []
    newest = collections.defaultdict(str)
    for r in rows:
        e = r.get("Employer") or ""
        p = (r.get("Posted") or "")[:10]
        if p > newest[e]:
            newest[e] = p
    import datetime
    cutoff = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    out = []
    for m in sorted(missed):
        seen = newest.get(m, "")
        if not seen or seen < cutoff:
            out.append({"Title": m, "Employer": "(NEOGOV agency)",
                        "_detail": f"not read this scan; newest ledger row "
                                   f"{seen or 'none'}"})
    return out


def load():
    with open(LEDGER) as f:
        rows = list(csv.DictReader(f))
    open_rows = [r for r in rows
                 if (r.get("Status") or "").lower() != "closed"]
    act = [r for r in open_rows if r.get("Tier") in mobile_page.ELIGIBLE]
    return open_rows, act


def run_once(verbose=True):
    rows, act = load()
    results = []
    for name, severity, fn in CHECKS:
        try:
            bad = fn(rows, act)
        except Exception as e:
            bad = [{"Title": "CHECK CRASHED", "Employer": "",
                    "_detail": f"{type(e).__name__}: {e}"}]
            severity = "error"
        results.append((name, severity, bad))

    errors = sum(len(b) for n, s, b in results if s == "error")
    warns = sum(len(b) for n, s, b in results if s == "warn")

    if verbose:
        print(f"{len(rows)} open rows, {len(act)} actionable, "
              f"{len(CHECKS)} checks")
        for name, severity, bad in results:
            if not bad:
                continue
            tag = "ERROR" if severity == "error" else "warn "
            print(f"\n{tag} {len(bad):4}  {name}")
            for r in bad[:6]:
                detail = r.get("_detail", "")
                ev = (r.get("Requirement evidence") or "")[:70]
                print(f"          {(r.get('Title') or '')[:44]:44} "
                      f"| {(r.get('Employer') or '')[:20]}")
                if detail:
                    print(f"             {detail}")
                elif ev:
                    print(f"             {ev}")
            if len(bad) > 6:
                print(f"          ... and {len(bad) - 6} more")
        if not errors and not warns:
            print("\nCLEAN — every posting meets every parameter")
        elif not errors:
            print(f"\nno errors ({warns} warnings)")
    return errors, warns, results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true",
                    help="re-run until two consecutive clean passes")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max-passes", type=int, default=10)
    a = ap.parse_args()

    if a.json:
        errors, warns, results = run_once(verbose=False)
        print(json.dumps({
            "errors": errors, "warnings": warns,
            "checks": [{"name": n, "severity": s, "violations": len(b),
                        "rows": [{"title": r.get("Title"),
                                  "employer": r.get("Employer"),
                                  "detail": r.get("_detail", "")}
                                 for r in b[:20]]}
                       for n, s, b in results]}, indent=2))
        return 1 if errors else 0

    if not a.loop:
        errors, _, _ = run_once()
        return 1 if errors else 0

    # He asked for a loop that keeps revisiting until a pass finds
    # nothing. Two consecutive clean passes, because a fix for one check
    # has more than once created work for another.
    clean = 0
    for p in range(1, a.max_passes + 1):
        print(f"\n{'=' * 58}\npass {p}\n{'=' * 58}")
        errors, warns, _ = run_once()
        if errors == 0:
            clean += 1
            if clean >= 2:
                print(f"\ntwo consecutive clean passes at pass {p}")
                return 0
        else:
            clean = 0
            print(f"\npass {p}: {errors} errors remain — "
                  f"fix them, then run again")
            return 1
    print(f"\nreached --max-passes={a.max_passes} without settling")
    return 1


if __name__ == "__main__":
    sys.exit(main())
