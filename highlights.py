"""
Front-of-list detail — what the job actually is, without opening it.

The digest showed a posting's title and nothing else about the work. That
is fine for Sutter, which titles a posting "Registered Nurse II, Cath Lab",
and useless for PACS, which titles thirteen different jobs "RN". One real
digest had six consecutive rows reading "RN", "RN", "RN - SOUTH",
"Registered Nurse (RN)", "Registered Nurse", "RN - Full Time": a list you
cannot triage without opening every posting, which is the one thing this
scanner exists to spare you.

The detail is already in the postings. PACS in particular writes it out in
labeled form near the top of the description:

    Position Details Position: Registered Nurse (RN) Employment Type:
    Full-Time Shift: AM Shift Schedule: Full-Time AM Pay Rate: $45.00–$52.00
    per hour, DOE Location: Moraga Post Acute

and St. Rose opens every posting with:

    Full-Time (0.9) NOC Shift (1900-0700) APPROXIMATE PAY RANGE: $73.35 - $90.22

So this module reads, it does not guess. Same contract as the classifier:
**every field here is a span the posting actually contains.** A shift that
isn't stated is left blank rather than filled in from the facility type or
the title's vibe — a wrong "AM shift" on a night job wastes an application
exactly the way a wrong "no experience required" does.

Care setting is the one field not read from prose, deliberately. Matching
"skilled nursing" in body text labels a hospital job that merely *prefers*
skilled-nursing experience as a skilled-nursing job. So setting comes from
the adapter, which knows what kind of employer it is reading, and is
absent when the adapter doesn't say.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import classifier as C


# ── labeled fields ───────────────────────────────────────────────────
# Postings that state their own details do it as "Label: value" runs with
# no punctuation between them, so a value ends where the next label starts.
# Same shape as classifier.sections(), different vocabulary: that one reads
# requirements, this one reads the job's shape.

_LABELS = (
    "Employment Type", "Employment Status", "Position Type", "Job Type",
    "Position Status", "Job Status", "Work Type", "Status", "Position",
    "Shift Hours", "Shifts", "Shift", "Schedule", "Hours", "Hours Per Week",
    "Pay Range", "Pay Rate", "Salary Range", "Salary", "Rate of Pay",
    "Wage", "Pay", "Compensation",
    "Days of the Week", "Weekend Requirements", "Weekly Hours",
    "Scheduled Weekly Hours", "Employee Status",
    "Location", "Facility", "Department", "Unit", "Reports To",
    "FLSA Status", "Job Family", "Occupations", "Degree Required",
    "Experience", "Requisition", "Job Summary", "Responsibilities",
    "Qualifications", "Benefits", "About Us", "Position Summary",
    "Position Details", "What We Offer", "Job Description",
)

# Longest first: "Pay Rate" must win over "Pay", "Shift Hours" over "Shift".
# The classifier learned this the hard way when a bare "EXPERIENCE" matched
# inside "REQUIRED EXPERIENCE" and split a section at the wrong offset.
_ORDERED_LABELS = sorted(_LABELS, key=len, reverse=True)
_LABEL_RE = re.compile(
    r"(?i)\b(" + "|".join(re.escape(s) for s in _ORDERED_LABELS) + r")\s*:\s*")


def labeled(text: str) -> dict[str, str]:
    """Every "Label: value" pair in the posting, value ending at the next one."""
    txt = C.clean(text)
    marks = [(m.start(), m.end(), m.group(1).upper())
             for m in _LABEL_RE.finditer(txt)]
    out: dict[str, str] = {}
    for i, (_s, e, label) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(txt)
        # A labeled value is a phrase, not a paragraph. Anything past the
        # first sentence belongs to the prose that follows the field block.
        body = re.split(r"(?<=[.!?])\s", txt[e:end].strip())[0].strip(" ;,|-")
        if body and label not in out:
            out[label] = body[:80]
    return out


def _first(fields: dict[str, str], *keys: str) -> str | None:
    for k in keys:
        v = fields.get(k.upper())
        if v:
            return v
    return None


# ── employment type ──────────────────────────────────────────────────
# The written forms actually seen across the nine sources, mapped to one
# spelling each so a list of thirty rows doesn't show "Full-Time",
# "Full Time", "FT" and "Regular/Full time" as four different things.

# Note the scoped (?i:...) groups. The acronyms are case-sensitive on
# purpose: "PT" is Physical Therapy far more often than it is part time,
# which is why there is no PT pattern at all, and a lowercase "ft" is a
# foot, a fort or the tail of a word, never a job type.
_EMPLOYMENT = [
    (re.compile(r"(?i:\bper[\s-]?diem\b)|\bPRN\b"), "Per diem"),
    (re.compile(r"(?i:\bon[\s-]?call\b)"), "On-call"),
    (re.compile(r"(?i:\bfull[\s-]?time\b)|\bFT\b"), "Full-time"),
    (re.compile(r"(?i:\bpart[\s-]?time\b)"), "Part-time"),
    (re.compile(r"(?i:\btemporary\b|\bcontingent\b)"), "Temporary"),
]


def employment(title: str, fields: dict[str, str], desc: str,
               stated: str | None = None) -> str | None:
    """
    Full-time / Part-time / Per diem / On-call, and more than one when the
    posting offers more than one — Napa Valley Care Center advertises
    "Full-Time, Part-Time & Per Diem Opportunities Available" in a single
    requisition, and picking just the first would misdescribe it.

    `stated` is the source's own structured field where it has one —
    Smart Hires and SmartRecruiters both return the job type as data rather
    than as prose, and data beats parsing. After that the posting's own
    labeled field is read before the title, because a title is a summary and
    a field is a statement: Medical Hill titles its posting "RN - On Call"
    and states "Employment Type: On-Call / Per Diem". Both are true; the
    field is the more complete of the two.
    """
    from_title = _employment_in(title)
    for src in (stated or "",
                _first(fields, "Employment Type", "Employment Status",
                       "Job Type", "Position Type", "Work Type",
                       "Position Status", "Schedule") or "",
                title,
                # Fall back to the whole posting last, and only to the first
                # 400 characters: PACS restates the type in the benefits
                # blurb ("full-time employees receive...") and reading the
                # whole body turns every per-diem job into "Full-time".
                desc[:400]):
        found = _employment_in(src)
        if not found:
            continue
        # A posting may contradict itself. Santa Rosa Post Acute titles a
        # requisition "RN- part time" and then states "Schedule: Full-Time,
        # 2 PM shifts and 2 NOC shifts" in the body. Printing "Full-time"
        # next to that title puts a contradiction inside a single digest
        # row, which costs the whole line its credibility. When the title
        # names a type the body does not, the title wins — it is the half
        # the reader can see. When the body merely says more (Medical Hill:
        # title "RN - On Call", body "On-Call / Per Diem"), the body wins.
        if from_title and not set(from_title) <= set(found):
            return " / ".join(from_title[:3])
        return " / ".join(found[:3])
    return None


def _employment_in(text: str) -> list[str]:
    # Ordered by where the posting says it, not by the order of the table
    # above — Napa lists "Full-Time, Part-Time & Per Diem" and reading it
    # back as "Per diem / Full-time / Part-time" is a needless lie about
    # emphasis.
    found = sorted(((m.start(), label) for rx, label in _EMPLOYMENT
                    for m in [rx.search(text or "")] if m))
    out, seen = [], set()
    for _pos, label in found:
        if label not in seen:
            seen.add(label)
            out.append(label)
    return out


# ── shift ────────────────────────────────────────────────────────────
# AM / PM / NOC is how post-acute writes it; Days / Evenings / Nights is
# how the hospitals do. Both are kept in the employer's own vocabulary
# rather than translated, because "NOC" is what the posting and the
# interview will both call it.

_SHIFT_TOKEN = re.compile(
    r"(?i)\b(NOC|AM|PM|day|days|evening|evenings|night|nights|swing"
    r"|overnight|weekend|weekends|rotating|variable|graveyard)\b")

# A shift statement, not a stray "AM". "8:00 AM" is a time, "AM Shift" is a
# shift, and "PM" inside "5 PM - 1:30 AM" is neither on its own. Require the
# word shift, or a slash-joined run of shift tokens, so a pay line reading
# "$45.00 per hour" can never become a shift.
_SHIFT_PHRASE = re.compile(
    r"(?i)\b((?:NOC|AM|PM|day|night|evening|swing|overnight|weekend|rotating"
    r"|variable|graveyard)s?"
    r"(?:\s*(?:,|/|&|and|\+)\s*(?:NOC|AM|PM|day|night|evening|swing|overnight"
    r"|weekend|rotating|variable|graveyard)s?)*)"
    r"\s+shifts?\b"
    r"|\bshifts?\s*:?\s*((?:NOC|AM|PM|day|night|evening|swing|overnight"
    r"|weekend|rotating|variable|graveyard)s?"
    r"(?:\s*(?:,|/|&|and|\+)\s*(?:NOC|AM|PM|day|night|evening|swing|overnight"
    r"|weekend|rotating|variable|graveyard)s?)*)\b")

# Hours attached to a shift are worth carrying: "NOC Shift (1900-0700)" tells
# a new grad more than "NOC" does.
_SHIFT_HOURS = re.compile(
    r"(?i)\(?\b(\d{1,2}:?\d{2}\s*(?:am|pm)?\s*[-–—]\s*\d{1,2}:?\d{2}\s*(?:am|pm)?)\)?")


# Two places a bare shift word needs no "shift" after it. In a title:
# "NOC RN" and "RN Registered Nurse Full Time Days RN" are unambiguous, and
# requiring the word dropped the shift from both. And in the value of a
# field literally named Shift — Sutter writes "Job Shift: Days", where the
# field name IS the missing word, and every one of its hundred-odd postings
# was coming back with no shift at all.
#
# Those two only. Against body prose this would be far too loose.
#
# The word forms are plural on purpose. "Days" is a shift; "Day" is half of
# "Day Surgery", and "Night" is half of "Night Clinic". The acronyms are
# case-sensitive for the same reason the employment ones are: lowercase
# "am" is a verb.
_TITLE_SHIFT = re.compile(
    r"(?i:\b(days|nights|evenings|overnight|graveyard|swing|weekends)\b)"
    r"|\b(NOC|AM|PM)\b")


def _tidy_shift(value: str) -> str:
    parts = [m.group(0) for m in _SHIFT_TOKEN.finditer(value)]
    if not parts:
        return ""
    out, seen = [], set()
    for p in parts:
        norm = p.upper() if p.upper() in {"NOC", "AM", "PM"} else p.capitalize()
        if norm.lower() not in seen:
            seen.add(norm.lower())
            out.append(norm)
    return " / ".join(out[:4])


def shift(title: str, fields: dict[str, str], desc: str,
          stated: str | None = None) -> str | None:
    """
    The shift the posting states, with its hours when it gives them.

    Title first: PACS titles like "Part-Time NOC Shift Registered Nurse (RN)"
    and "RN NOC Per Diem" are the source's own summary and are never wrong.
    Then `stated`, the shift line an adapter lifted out of the posting
    (St. Rose opens every description with "Full-Time (0.9) NOC Shift
    (1900-0700)"), then the posting's own Shift field, then the opening of
    the body. Never the whole body — "AM, PM & NOC shift opportunities"
    appears in PACS's benefits boilerplate on postings that are for one
    shift only.
    """
    for src, bare in ((title, True), (stated or "", False),
                      (_first(fields, "Shift", "Shifts", "Schedule",
                              "Shift Hours") or "", True),
                      (desc[:400], False)):
        # Every shift phrase in the source, not just the first. Santa Rosa
        # writes "Full-Time, 2 PM shifts and 2 NOC shifts", and stopping at
        # the first match reported a PM job that is half nights.
        spans = [m.group(1) or m.group(2) or ""
                 for m in _SHIFT_PHRASE.finditer(src)]
        if not spans and bare:
            spans = [m.group(0) for m in _TITLE_SHIFT.finditer(src)]
        got = _tidy_shift(" ".join(spans))
        if not got:
            continue
        hours = _SHIFT_HOURS.search(src)
        return f"{got} {hours.group(1)}" if hours else got
    return None


# ── pay ──────────────────────────────────────────────────────────────
# Stated pay only. This is the single most useful fact a job list can carry
# and the one most likely to be invented if the code is sloppy, so it is
# read as a literal span and reprinted with its own numbers.

_PAY = re.compile(
    r"(?i)\$\s?([\d,]+(?:\.\d{2})?)"
    r"(?:\s*(?:[-–—]|to)\s*\$?\s?([\d,]+(?:\.\d{2})?))?"
    r"\s*(?:per\s+|/\s*|an?\s+)?(hour|hr|hourly|year|yr|annually|annual)?")


# What an RN in this region is actually paid by the hour. Used only to
# decide whether an unlabeled range is a wage or a bonus.
_HOURLY_FLOOR, _HOURLY_CEILING = 15.0, 500.0


def pay(fields: dict[str, str], desc: str) -> str | None:
    """
    The rate the posting states, or nothing.

    Every dollar figure is considered, not just the first. Postings lead
    with the sign-on bonus — "Ask about our $5,000 bonus!" — and stopping
    at the first "$" threw away the wage three paragraphs down.

    A figure is only reported as pay when the posting says what it is per,
    or when it is a range that could not be anything else: $46.00–$47.00 is
    an hourly band, $5,000–$10,000 is a bonus, and guessing between them
    from the digits alone is how a job list starts lying about money.
    """
    src = (_first(fields, "Pay Rate", "Pay Range", "Pay", "Salary Range",
                  "Salary", "Rate of Pay", "Wage", "Compensation")
           or "")
    for text in (src, desc):
        for m in _PAY.finditer(text):
            lo, hi, unit = m.group(1), m.group(2), (m.group(3) or "").lower()
            if unit:
                per = ("/yr" if unit.startswith(("year", "yr", "annual"))
                       else "/hr")
            elif hi and all(_HOURLY_FLOOR <= float(x.replace(",", ""))
                            <= _HOURLY_CEILING for x in (lo, hi)):
                per = "/hr"
            else:
                continue
            return (f"${lo}–${hi}{per}" if hi else f"${lo}{per}")
    return None


# ── the line the digest shows ────────────────────────────────────────

@dataclass
class Highlights:
    facility: str | None = None      # where you would actually work
    setting: str | None = None       # what kind of nursing, from the adapter
    employment: str | None = None
    shift: str | None = None
    pay: str | None = None

    def line(self) -> str:
        parts = [self.facility, self.setting, self.employment,
                 self.shift, self.pay]
        return " · ".join(p for p in parts if p)


def summarize(posting) -> str:
    """
    One line of stated fact about a posting, for the digest and the ledger.

    Facility is included only when it says something the Location column
    doesn't. "Medical Hill Healthcare Center" next to "Oakland" is worth the
    width; "Moraga Post Acute" next to "Moraga Post Acute" — which is what
    an unmapped PACS facility falls back to — is not.
    """
    desc = C.clean(getattr(posting, "description", "") or "")
    fields = labeled(desc)
    title = getattr(posting, "title", "") or ""

    facility = (getattr(posting, "department", None) or "").strip()
    if facility and facility.lower() == (posting.location or "").strip().lower():
        facility = ""

    h = Highlights(
        facility=facility or None,
        setting=getattr(posting, "setting", None) or None,
        employment=employment(title, fields, desc,
                              getattr(posting, "schedule", None)),
        shift=shift(title, fields, desc, getattr(posting, "shift", None)),
        pay=pay(fields, desc),
    )
    return h.line()
