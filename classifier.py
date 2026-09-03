"""
Requirement classifier — decides whether a posting is worth showing.

Your rule, restated as code:

    SHOW  STAFF_NURSE_I       title is Staff Nurse I / Nurse I / Clinical
                              Nurse I / RN I
    SHOW  NO_EXPERIENCE       requirements list education + licensure only
    SHOW  GENERAL_EXPERIENCE  requires nursing experience, but not acute
                              care — you want these for later
    HIDE  ACUTE_REQUIRED      requires acute-care/hospital experience
    SHOW  UNCLEAR             couldn't tell — shown, never silently dropped

Only ACUTE_REQUIRED is suppressed. Everything else reaches you.

Every verdict carries the sentence it was based on, so you can check the
call in two seconds instead of reopening the posting. If the evidence
doesn't support the verdict, the rule is wrong and should be fixed — a
classifier you can't audit is just noise with extra steps.

Structure this exploits, found by reading real Sutter postings:

    Job Description :
    EDUCATION: Graduate of an accredited school of nursing
    CERTIFICATION & LICENSURE: RN-Registered Nurse of California, BLS
    TYPICAL EXPERIENCE: 2 years recent relevant experience
    SKILLS AND KNOWLEDGE: ...

Postings with no TYPICAL EXPERIENCE section require no experience. That
absence is the single highest-value signal in the whole pipeline — it is
what separates "Ambulatory Services Nurse I, PreOp & PACU" from the 80-odd
Level II roles that look superficially similar in a list view.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

SHOW = {"STAFF_NURSE_I", "NO_EXPERIENCE", "GENERAL_EXPERIENCE", "UNCLEAR"}
HIDE = {"ACUTE_REQUIRED"}

# Rank for sorting the digest — lower is more interesting to you.
RANK = {"STAFF_NURSE_I": 0, "NO_EXPERIENCE": 1, "UNCLEAR": 2,
        "GENERAL_EXPERIENCE": 3, "ACUTE_REQUIRED": 4}


@dataclass
class Verdict:
    bucket: str
    evidence: str      # the text the decision rests on
    reason: str        # one line, human readable


# ── section parsing ──────────────────────────────────────────────────

SECTION_LABELS = (
    "AS TYPICALLY ACQUIRED IN", "MINIMUM QUALIFICATIONS",
    "TYPICAL EXPERIENCE", "EXPERIENCE", "MINIMUM EXPERIENCE",
    "REQUIRED EXPERIENCE", "PREFERRED EXPERIENCE",
    "EDUCATION", "CERTIFICATION & LICENSURE", "CERTIFICATION AND LICENSURE",
    "CERTIFICATIONS/LICENSURES", "CERTIFICATIONS / LICENSURES",
    "CERTIFICATION/LICENSURE", "CERTIFICATIONS", "LICENSURES",
    "CERTIFICATION", "LICENSURE", "SKILLS AND KNOWLEDGE", "SKILLS",
    "KNOWLEDGE", "QUALIFICATIONS", "MINIMUM QUALIFICATIONS",
    "JOB SHIFT", "SCHEDULE", "SHIFT HOURS", "DAYS OF THE WEEK",
    "WEEKEND REQUIREMENTS", "BENEFITS", "UNIONS", "POSITION STATUS",
    "PAY RANGE", "DEPARTMENT",
)
# Longest label first. Regex alternation is first-match-wins, so listing
# "EXPERIENCE" before "REQUIRED EXPERIENCE" made it match the short label
# inside the long one and split the section at the wrong offset — which is
# how John Muir's "Required Experience: 6 Months Nursing - Medical Acute
# Care - Required" ended up unparsed.
_LABEL_RE = re.compile(
    r"(?i)\b(" + "|".join(re.escape(s) for s in
                          sorted(SECTION_LABELS, key=len, reverse=True))
    + r")\s*:?\s")


def clean(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"(?s)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def sections(description: str) -> dict[str, str]:
    """Split a posting into its labeled requirement sections."""
    txt = clean(description)
    marks = [(m.start(), m.end(), m.group(1).upper())
             for m in _LABEL_RE.finditer(txt)]
    out: dict[str, str] = {}
    for i, (_s, e, label) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(txt)
        body = txt[e:end].strip(" :.-")
        if body and label not in out:
            out[label] = body
    return out


def experience_section(description: str) -> tuple[str, bool] | None:
    """
    Returns (section_text, is_preferred_section) or None.

    A section literally labeled PREFERRED EXPERIENCE is preferred in whole.
    Otherwise the required/preferred call is made per clause below.
    """
    sec = sections(description)
    if "PREFERRED EXPERIENCE" in sec and not any(
            k in sec for k in ("TYPICAL EXPERIENCE", "MINIMUM EXPERIENCE",
                               "REQUIRED EXPERIENCE")):
        return sec["PREFERRED EXPERIENCE"], True
    for key in ("TYPICAL EXPERIENCE", "MINIMUM EXPERIENCE",
                "REQUIRED EXPERIENCE", "AS TYPICALLY ACQUIRED IN",
                "MINIMUM QUALIFICATIONS", "EXPERIENCE"):
        if key in sec:
            return sec[key], False
    return None


# A bare duration statement anywhere in the posting that is not itself
# hedged as preferred. Used as a veto on NO_EXPERIENCE verdicts.
_HARD_DURATION = re.compile(
    r"(?i)(minimum\s+(of\s+)?)?\b(\d+|one|two|three|four|five|six|twelve)[\s-]*"
    r"(\+|plus)?\s*(year|yr|month|mo)s?\b[^.;]{0,80}")


def _has_unhedged_duration(text: str) -> str | None:
    """
    Return the offending span if the posting states ANY time requirement.

    Deliberately blunt. Earlier versions tried to decide whether a nearby
    "preferred" hedged the duration, and were wrong four separate ways on
    real Sutter postings — "Minimum one year of current experience" kept
    coming back as no-experience-required. A false "no experience needed"
    is the most costly error this system can make: it spends your time on
    an application you were never eligible for.

    So: any stated duration anywhere disqualifies a NO_EXPERIENCE verdict.
    The posting becomes UNCLEAR, which still reaches you, but honestly
    labeled. Recovering the genuinely-preferred cases from UNCLEAR is the
    LLM stage's job, not this function's.
    """
    m = _HARD_DURATION.search(text or "")
    return m.group(0).strip() if m else None


def _clauses(text: str) -> list[str]:
    """
    Split an experience section into independent requirement clauses.

    This matters more than it looks. Sutter writes sections like:

        "2 years of recent relevant experience. L&D experienced acute RN's
         preferred"

    Scanning the whole section for "preferred" reads that as optional and
    lets a two-year requirement through as new-grad friendly. Scanning for
    "acute" reads it as acute-required and hides a job that only prefers it.
    Both are wrong. The clauses have to be judged separately.
    """
    parts = re.split(
        r"(?<=[.;])\s+"
        r"|\s+(?:and|but)\s+(?=\w+\s+preferred)"
        # John Muir writes "6 Months Nursing - Medical Acute Care - Required
        # Certifications/Licensures: RN ... - Required BLS ..." with no
        # sentence punctuation, so the whole block arrived as one clause and
        # a later "preferred" cancelled a hard acute requirement.
        r"|(?<=-\s[Rr]equired)\s+(?=[A-Z])"
        r"|(?<=-\s[Pp]referred)\s+(?=[A-Z])", text)
    return [p.strip() for p in parts if p and p.strip()]


# ── signals ──────────────────────────────────────────────────────────

TITLE_LEVEL_I = re.compile(
    r"(?i)\b(staff nurse|clinical nurse|registered nurse|ambulatory services nurse"
    r"|nurse|rn)\s*(i|1)\b(?!\s*[iv])")

# "Nurse II" must never match Level I. Guard explicitly.
TITLE_LEVEL_2PLUS = re.compile(r"(?i)\b(ii|iii|iv|v|2|3|4|5)\b")

ACUTE = re.compile(
    r"(?i)\b(acute care|acute[- ]care|inpatient|hospital|med[- ]?surg"
    r"|telemetry|critical care|icu|intensive care|emergency (?:room|department|dept)"
    r"|er experience|ed experience|bedside)\b")

REQUIRED_WORD = re.compile(r"(?i)\b(required|must have|minimum of|at least)\b")
PREFERRED_ONLY = re.compile(r"(?i)\b(preferred|desirable|a plus|nice to have)\b")

# "2 years", "1 year", "six months"
DURATION = re.compile(
    r"(?i)\b(\d+(?:\.\d+)?|one|two|three|four|five|six|twelve)\s*"
    r"(\+|plus)?\s*(year|yr|month)s?\b")

NEW_GRAD = re.compile(
    r"(?i)\b(new grad(uate)?s?( are)?( welcome| encouraged| eligible)?"
    r"|nurse residen(cy|t)|graduate nurse program|no experience (is )?required"
    r"|new graduate rn)\b")


def _snippet(text: str, pattern: re.Pattern, width: int = 170) -> str:
    m = pattern.search(text or "")
    if not m:
        return (text or "")[:width]
    a = max(0, m.start() - width // 2)
    return ("..." if a else "") + text[a:m.end() + width // 2].strip() + "..."


# ── the rule pass ────────────────────────────────────────────────────

def classify(title: str, description: str) -> Verdict:
    t = title or ""
    desc = clean(description)

    # 0. A dash-delimited requirement line like John Muir's
    #    "6 Months Nursing - Medical Acute Care - Required"
    #    states an acute gate with no section header at all. Catch it before
    #    anything else; it was previously slipping through as NO_EXPERIENCE.
    for line in re.split(r"(?<=[.;])\s+", desc):
        if re.search(r"(?i)-\s*required\b", line) and ACUTE.search(line) \
                and DURATION.search(line) and not PREFERRED_ONLY.search(line):
            return Verdict("ACUTE_REQUIRED", line[:200],
                           "states a required acute-care duration")

    # 1. Explicit new-grad language beats everything.
    if NEW_GRAD.search(desc) or NEW_GRAD.search(t):
        return Verdict("STAFF_NURSE_I", _snippet(desc, NEW_GRAD),
                       "posting explicitly names new grads or a residency")

    # 2. Level I in the title — but only if no higher level is also present.
    #    "Clinical Nurse II" contains no Level-I match; "RN I/II" does, and
    #    should not count as Level I.
    head = re.split(r"[-–—,(]", t)[0]
    if TITLE_LEVEL_I.search(t) and not TITLE_LEVEL_2PLUS.search(head):
        return Verdict("STAFF_NURSE_I", t, "title is a Level I role")

    got = experience_section(desc)

    # 3. No experience section at all -> education + licensure only.
    if got is None:
        sec = sections(desc)
        if any(k in sec for k in ("EDUCATION", "CERTIFICATION & LICENSURE",
                                  "CERTIFICATION", "LICENSURE")):
            ev = "; ".join(f"{k}: {v[:90]}" for k, v in list(sec.items())[:2])
            hard = _has_unhedged_duration(desc)
            if hard:
                return Verdict("UNCLEAR", hard,
                               "no experience section parsed, but the posting "
                               "states a time requirement — read it yourself")
            return Verdict("NO_EXPERIENCE", ev,
                           "requirements list education and licensure only, "
                           "no experience section")
        return Verdict("UNCLEAR", desc[:170],
                       "no parseable requirements section")

    exp, section_is_preferred = got
    if section_is_preferred:
        hard = _has_unhedged_duration(desc)
        if hard:
            return Verdict("UNCLEAR", hard,
                           "experience sits under a PREFERRED heading, but the "
                           "posting states a time requirement elsewhere")
        return Verdict("NO_EXPERIENCE", exp[:200],
                       "experience appears only under a PREFERRED heading")

    # 4. Judge each clause on its own. A clause is a hard requirement if it
    #    states a duration or a required-word AND does not itself say
    #    "preferred".
    required_clauses = []
    for c in _clauses(exp):
        if PREFERRED_ONLY.search(c):
            continue                       # this clause is optional
        if DURATION.search(c) or REQUIRED_WORD.search(c):
            required_clauses.append(c)

    if not required_clauses:
        hard = _has_unhedged_duration(desc)
        if hard:
            return Verdict("UNCLEAR", hard,
                           "clauses read as preferred, but a time requirement "
                           "appears elsewhere in the posting")
        return Verdict("NO_EXPERIENCE", exp[:200],
                       "experience mentioned, but every clause is preferred "
                       "rather than required")

    # Acute care only counts if it appears in a clause that is required.
    for c in required_clauses:
        if ACUTE.search(c):
            return Verdict("ACUTE_REQUIRED", c[:200],
                           "requires acute-care or hospital experience")

    return Verdict("GENERAL_EXPERIENCE", required_clauses[0][:200],
                   "requires nursing experience, but not acute care")


def should_show(v: Verdict) -> bool:
    return v.bucket in SHOW


# ── optional LLM second opinion ──────────────────────────────────────

LLM_PROMPT = """You are screening a nursing job posting for a new-graduate RN.

Answer ONLY with JSON: {"bucket": ..., "evidence": ..., "reason": ...}

bucket must be exactly one of:
  STAFF_NURSE_I      entry-level / Level I / new-grad / residency role
  NO_EXPERIENCE      requires education and licensure only
  GENERAL_EXPERIENCE requires nursing experience, but NOT acute-care or
                     hospital experience specifically
  ACUTE_REQUIRED     requires acute-care, inpatient, hospital, ICU, ED,
                     med-surg or telemetry experience
  UNCLEAR            requirements are absent or genuinely ambiguous

evidence must be a VERBATIM span from the posting, under 200 characters,
containing the requirement your decision rests on. Never paraphrase it and
never invent it. If no such span exists, use UNCLEAR.

Experience described as "preferred" is NOT required.

TITLE: {title}
POSTING: {description}"""


def llm_classify(title, description, call_model):
    """
    `call_model(prompt) -> str` is supplied by the caller so this module
    stays dependency-free and testable. Route it to Haiku; at 2-3 scans a
    day this is a few dollars a month.

    Only send postings the rules marked UNCLEAR, or where you want a second
    opinion on an ACUTE_REQUIRED call before suppressing it. Sending all of
    them works but costs more for little benefit.
    """
    import json as _json
    raw = call_model(LLM_PROMPT.format(title=title, description=description[:6000]))
    data = _json.loads(re.sub(r"```(json)?", "", raw).strip())
    return Verdict(data["bucket"], data.get("evidence", ""), data.get("reason", ""))
