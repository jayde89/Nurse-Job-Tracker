"""
How the list decides what she sees first.

The page used to sort by tier, then drive time, then employer name.
Alphabetical order is not a priority, and it showed: six identical
"STAFF NURSE I" cards from Seton led the page while an Emergency
Department job at $113/hr, thirty minutes away, sat far below them.

Everything here is a preference, not a rule about who may apply. The
eligibility judgement belongs to classifier.py and mobile_page.tier_for,
which read the posting's own requirement sentence and are tested against
hundreds of real rows. Ranking only reorders what is already eligible: a
bad score never hides a job, it just puts it lower down.

The score has to survive missing data. Only 43 of 111 actionable postings
state pay at all, so a job that says nothing about money must not be
punished for it — it scores neutral on that axis and competes on the
others.
"""

import re

# What she is actually hunting. An acute-care hospital job is the entire
# point of the search: it pays more, and it is the experience that opens
# every door after it. A skilled-nursing job at the same wage is a
# sideways move, which is why it can never outscore a hospital post.
ACUTE_VALUE = {
    "icu": 10,        # the hardest to enter and the most valuable to hold
    "ed": 10,
    "stepdown": 9,
    "telemetry": 9,
    "cardiac": 8,
    "medsurg": 7,     # the classic acute bridge job
    "periop": 6,
    "other_acute": 5,
    "ltac": 5,        # a hospital, and her realistic near-term route in
    "clinic": 2,
    "snf": 0,         # what she already does
}

_UNIT_PATTERNS = [
    ("icu", re.compile(r"\bicu\b|intensive care|critical care|\bccu\b"
                       r"|\bcvicu\b|\bsicu\b|\bmicu\b|\bnicu\b|\bpicu\b", re.I)),
    ("ed", re.compile(r"emergency|\btrauma\b|\bed\b|\ber\b", re.I)),
    ("stepdown", re.compile(r"step.?down|\bpcu\b|progressive care"
                            r"|intermediate care|\bdou\b", re.I)),
    ("telemetry", re.compile(r"telemetry|\btele\b", re.I)),
    ("cardiac", re.compile(r"cardiac|cardiolog|cardiovascular|cath ?lab", re.I)),
    ("periop", re.compile(r"periop|operating room|\bor\b rn|\bpacu\b"
                          r"|post.?anesthesia|surgical services", re.I)),
    ("medsurg", re.compile(r"med.?surg|medical.?surgical|\bms\b tele"
                           r"|acute rehab|inpatient", re.I)),
]

_LTAC = re.compile(r"long.?term acute|\bLTACH?\b", re.I)

# Roles that borrow a unit's name without the bedside hours. These are
# real nursing jobs and stay in the list, but they do not build the acute
# experience the search exists to find, so they must not rank as if they
# did. "Trauma Performance Improvement Nurse" is quality improvement.
_NON_BEDSIDE = re.compile(
    r"performance improvement|quality|informatics|educator|education"
    r"|research|coordinator|navigator|abstractor|reviewer|auditor"
    r"|compliance|infection (?:control|prevention)|analyst"
    r"|program manager|placement|capacity|staffing", re.I)
_SNF = re.compile(r"skilled nursing|\bSNF\b|sub.?acute|post.?acute"
                  r"|long.?term care|\bLTC\b|nursing home", re.I)
_CLINIC = re.compile(r"clinic|ambulatory|outpatient|home health|hospice"
                     r"|school|occupational health|employee health"
                     r"|utilization|case manage|telephon"
                     # Infusion, dialysis and mental-health nursing are
                     # real nursing and stay in the list, but they are not
                     # the acute hospital experience the search is for.
                     # Left unclassified they scored as generic acute and
                     # outranked an Emergency Department job.
                     r"|infusion|dialysis|apheresis|mental health|behavioral"
                     r"|psychiatric|primary care|wellness|triage", re.I)


def unit_of(title: str, details: str = "", setting: str = "") -> str:
    """Which kind of nursing this posting is, for valuation purposes."""
    blob = f"{title} {details}"
    # A desk role borrows the unit's name without the bedside hours.
    # "Trauma Performance Improvement Nurse" is a quality-improvement job
    # that scored as an Emergency Department post and came sixth overall.
    if _NON_BEDSIDE.search(title):
        return "clinic"
    # LTAC before SNF: a long-term ACUTE care hospital says "long-term"
    # and is not a nursing home. Kentfield is the clearest example, and
    # it is the best bridge job in the list.
    if _LTAC.search(f"{blob} {setting}"):
        return "ltac"
    if _SNF.search(setting) or _SNF.search(details):
        return "snf"
    # The unmistakable inpatient units win outright, before any outpatient
    # wording is considered. An ED posting that mentions triage in its
    # description is still an ED posting.
    for name in ("icu", "ed", "stepdown"):
        if dict(_UNIT_PATTERNS)[name].search(title):
            return name
    # Outpatient before the softer unit keywords: a "Cardiology Clinic RN"
    # is a clinic job that happens to say cardiac, and scoring it as a
    # cardiac hospital post would put it above an inpatient cardiac floor.
    if _CLINIC.search(blob):
        return "clinic"
    for name, pat in _UNIT_PATTERNS:
        if pat.search(blob):
            return name
    return "other_acute"


# Pay, read strictly.
#
# "$2550/yr" appears on four San Joaquin postings and is a stipend, not a
# salary. Reading any dollar figure as an hourly wage put a $255 "rate" at
# the top of the list. So: an explicit hourly marker, and a sane band.
_HOURLY = re.compile(
    r"\$\s?(\d{2,3}(?:\.\d{1,2})?)"
    r"(?:\s*[-–—]\s*\$?\s?(\d{2,3}(?:\.\d{1,2})?))?"
    r"\s*(?:/|\s+per\s+)?\s*(hr|hour)\b", re.I)

_PLAUSIBLE_RN_HOURLY = (25.0, 200.0)


def hourly_pay(details: str):
    """
    (low, high) hourly rate this posting states, or None.

    None means "not stated", never "pays nothing" — most postings say
    nothing about money and must not be penalised for it.
    """
    best = None
    for m in _HOURLY.finditer(details or ""):
        lo = float(m.group(1))
        hi = float(m.group(2)) if m.group(2) else lo
        if not (_PLAUSIBLE_RN_HOURLY[0] <= lo <= _PLAUSIBLE_RN_HOURLY[1]):
            continue
        if hi < lo:
            lo, hi = hi, lo
        if best is None or hi > best[1]:
            best = (lo, hi)
    return best


# What she earns now. A job below this is a pay cut and should sink even
# if it is otherwise attractive.
CURRENT_HOURLY = 45.0

_DRIVE_SCORE = {"<30": 10.0, "30-60": 6.0, "60-90": 2.0, "90-120": -4.0}

# Tier already groups the list; this only breaks ties *within* a group and
# feeds the cross-tier "best bets" shortlist.
_TIER_SCORE = {
    "A_open": 12.0, "A_newgrad": 10.0, "A_pref": 9.0, "A_spec_entry": 8.0,
    "B_soon": 5.0, "B_bridge": 3.0, "C_unclear": 4.0,
    "D_specialty": 0.0, "E_acute_req": 0.0, "F_tenure": 0.0,
}


def score(job: dict) -> tuple:
    """
    (points, reasons) for one posting.

    `reasons` is shown on the card. A ranking she cannot interrogate is a
    ranking she has to trust blindly, and the rest of this repo refuses to
    ask that — every verdict ships with the sentence behind it.
    """
    title = job.get("title", "")
    details = job.get("details", "")
    setting = job.get("setting", "")
    reasons = []
    pts = 0.0

    unit = unit_of(title, details, setting)
    uv = ACUTE_VALUE.get(unit, 5)
    # Acute value is weighted heavily on purpose. The goal is not the
    # highest wage available today — it is the experience that unlocks
    # every hospital job after this one. A $90/hr clinic post that leads
    # nowhere must not outrank a $70/hr ED post that does.
    pts += uv * 1.6
    if uv >= 9:
        reasons.append(f"{unit.upper()} — the experience she is hunting")
    elif unit == "ltac":
        reasons.append("LTAC — acute experience, realistic entry")
    elif unit == "snf":
        reasons.append("skilled nursing — sideways move")
    elif unit == "clinic":
        reasons.append("outpatient — does not build acute hours")

    pay = hourly_pay(details)
    if pay:
        top = pay[1]
        delta = top - CURRENT_HOURLY
        # Capped hard, and deliberately smaller than the acute weighting.
        # A very high advertised ceiling is usually the top of a long
        # seniority ladder she would not start on.
        pts += max(-8.0, min(9.0, delta * 0.3))
        if delta >= 5:
            reasons.append(f"${top:.0f}/hr — ${delta:.0f} over her rate")
        elif delta < 0:
            reasons.append(f"${top:.0f}/hr — below her current rate")

    drive = _DRIVE_SCORE.get(job.get("drive", ""), 0.0)
    pts += drive
    if job.get("drive") == "<30":
        reasons.append("under 30 min")

    pts += _TIER_SCORE.get(job.get("tier", ""), 0.0)

    # Freshness: a posting she sees on day one is worth more than the same
    # posting three weeks later, when the req may already be filled.
    age = job.get("age_days")
    if isinstance(age, (int, float)):
        if age <= 3:
            pts += 4.0
            reasons.append("posted this week")
        elif age <= 10:
            pts += 2.0
        elif age > 45:
            pts -= 3.0
            reasons.append("posted over 6 weeks ago")

    return round(pts, 2), reasons


def rank(jobs: list) -> list:
    """Attach score/reasons and return the list in ranked order."""
    for j in jobs:
        j["score"], j["why_ranked"] = score(j)
    return sorted(jobs, key=lambda j: -j["score"])
