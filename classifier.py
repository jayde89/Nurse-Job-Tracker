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
HIDE = {"ACUTE_REQUIRED", "LEVEL_II_TITLE"}

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
    # Vibra's headings. Without these its postings parsed to no requirements
    # section at all and the evidence shown was the marketing overview.
    "ADDITIONAL QUALIFICATIONS/SKILLS", "ADDITIONAL QUALIFICATIONS",
    "REQUIRED SKILLS",
)
# Several labels are also ordinary English words, and a bare match on one
# mid-sentence is not a section header. Vibra writes "Previous acute care
# experience is strongly preferred"; matching the bare word EXPERIENCE
# inside that sentence made the requirement itself into a heading and left
# the evidence as "is strongly preferred. Ability to project a professional
# image" — a quote that supports nothing, in a system whose whole promise
# is that you can audit the label against the quote. Require a colon for
# these. The distinctive multi-word labels stay colon-optional, because
# sources really do use them as bare headings.
PROSE_LABELS = {
    "EXPERIENCE", "EDUCATION", "SKILLS", "KNOWLEDGE", "CERTIFICATION",
    "CERTIFICATIONS", "LICENSURE", "LICENSURES", "QUALIFICATIONS",
    "BENEFITS", "SCHEDULE", "DEPARTMENT", "UNIONS",
}

# Longest label first. Regex alternation is first-match-wins, so listing
# "EXPERIENCE" before "REQUIRED EXPERIENCE" made it match the short label
# inside the long one and split the section at the wrong offset — which is
# how John Muir's "Required Experience: 6 Months Nursing - Medical Acute
# Care - Required" ended up unparsed.
_DISTINCT_LABELS = sorted((s for s in SECTION_LABELS
                           if s.upper() not in PROSE_LABELS),
                          key=len, reverse=True)
_PROSE_LABELS = sorted((s for s in SECTION_LABELS
                        if s.upper() in PROSE_LABELS), key=len, reverse=True)

_LABEL_RE = re.compile(
    r"(?i)\b(" + "|".join(re.escape(s) for s in _DISTINCT_LABELS) + r")\s*:?\s"
    r"|\b(" + "|".join(re.escape(s) for s in _PROSE_LABELS) + r")\s*:\s")


def clean(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"(?s)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def sections(description: str) -> dict[str, str]:
    """Split a posting into its labeled requirement sections."""
    txt = clean(description)
    marks = [(m.start(), m.end(), (m.group(1) or m.group(2)).upper())
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
                "MINIMUM QUALIFICATIONS", "EXPERIENCE",
                # Vibra states its experience requirement here and nowhere
                # else, so without this the posting reads as having no
                # requirements at all.
                "ADDITIONAL QUALIFICATIONS/SKILLS", "ADDITIONAL QUALIFICATIONS",
                "REQUIRED SKILLS"):
        if key in sec:
            return sec[key], False

    # Free-form postings have no headings to find. PACS writes pure
    # marketing copy and states its requirement in an ordinary sentence:
    # "...post-acute or long-term care setting is highly preferred, but
    # passionate new grads are welcome to apply." Fall back to the
    # sentences that actually mention experience. Whole sentences, never
    # fragments — this is the text quoted back to you as evidence, and a
    # truncated clause is what made thirteen PACS postings unreadable.
    txt = clean(description)
    said = [s for s in re.split(r"(?<=[.;!?])\s+", txt)
            if re.search(r"(?i)\bexperience\b", s)]
    if said:
        return " ".join(said)[:600], False
    return None


# Government and county postings write a duration as a spelled number
# followed by the same number in parentheses: "One (1) year of full time
# experience", "Two (2) years", "six (6) months". The parenthetical sits
# between the number and its unit, and every duration pattern here used to
# require them adjacent — so none of these matched at all. Six Contra Costa
# RN postings demanding one to two years of acute-care experience were
# classified NO_EXPERIENCE as a result, quoting the very sentence that
# disqualified them. Allow the parenthetical everywhere a duration is read.
_PAREN_NUM = r"(?:\s*\(\s*\d+\s*\))?"
_NUM_WORD = (r"\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine"
             r"|ten|eleven|twelve|eighteen|twenty")

# A bare duration statement anywhere in the posting that is not itself
# hedged as preferred. Used as a veto on NO_EXPERIENCE verdicts.
_HARD_DURATION = re.compile(
    r"(?i)(minimum\s+(of\s+)?)?\b(" + _NUM_WORD + r")" + _PAREN_NUM +
    r"[\s-]*(\+|plus)?\s*(year|yr|month|mo)s?\b[^.;]{0,80}")


# A duration counted forward from your start date is an onboarding
# deadline, not experience you must already have: "must obtain ACLS within
# six (6) months of hire" disqualifies nobody. Left in the veto it made
# every Vibra posting UNCLEAR on a certification clause. Matching this is
# what keeps the veto blunt about experience without being blunt about
# everything that mentions a number and a month.
_ONBOARDING = re.compile(
    r"(?i)\bof\s+(hire|employment|date\s+of\s+hire|start(ing)?\s+date)"
    r"|\bfrom\s+(hire|date\s+of\s+hire|start\s+date)"
    r"|\b(within|after|following)\s+(hire|employment|orientation)"
    r"|\bof\s+(the\s+)?(hire|appointment)\s+date")


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
    for m in _HARD_DURATION.finditer(text or ""):
        span = m.group(0).strip()
        if _ONBOARDING.search(span):
            continue          # a deadline after you start, not a prerequisite
        return span
    return None


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

# A graded Level II+ title, anchored to the nurse noun itself.
#
# At Sutter and the systems that copy its ladder, the "II" in "Registered
# Nurse II" is the job grade, not a description of the unit: it is the
# rung above Level I and it is what HR screens on. No amount of reading
# the requirement text changes that, and the text often understates it —
# "Registered Nurse II, Medical Acute" in Roseville asks for six months of
# acute experience and marks it *Preferred*, so every clause reads as
# optional and the posting arrives looking open to anyone.
#
# Measured against a live scan, graded titles were 66 of 122 open rows —
# 54% of the list — and not one of them landed in NO_EXPERIENCE or
# STAFF_NURSE_I. The grade never coincides with the buckets a new grad can
# actually use, which is why suppressing on it costs nothing and halves
# the noise.
#
# Anchored to "nurse"/"rn" immediately before the numeral on purpose. A
# bare \b(ii|2)\b anywhere in the title matches "RN, 2 West Medical",
# "Unit 4 South" and "12 Hour Nights", and would have hidden three staff
# postings that carry no grade at all.
TITLE_LEVEL_II_GRADED = re.compile(
    r"(?i)\b(?:nurse|rn)\s*(ii|iii|iv|2|3|4)\b")

ACUTE = re.compile(
    r"(?i)\b(acute care|acute[- ]care|inpatient|hospital|med[- ]?surg"
    r"|telemetry|critical care|icu|intensive care|emergency (?:room|department|dept)"
    r"|er experience|ed experience|bedside)\b")

REQUIRED_WORD = re.compile(r"(?i)\b(required|must have|minimum of|at least)\b")
PREFERRED_ONLY = re.compile(r"(?i)\b(preferred|desirable|a plus|nice to have)\b")

# "2 years", "1 year", "six months", "One (1) year"
DURATION = re.compile(
    r"(?i)\b(" + _NUM_WORD + r")" + _PAREN_NUM +
    r"\s*(\+|plus)?\s*(year|yr|month)s?\b")

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

def _classify_requirements(title: str, description: str) -> Verdict:
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
        # A clause naming acute-care experience inside a requirements
        # section is a requirement even when it states no duration and
        # never says "required". Sutter writes
        #   "AS TYPICALLY ACQUIRED IN: Acute Care Previous experience as an
        #    RN in an acute care hospital setting."
        # which has neither, so no clause qualified, and the posting
        # reached the recommendations labelled "no experience required"
        # while quoting that exact sentence as its evidence. A clause that
        # hedges itself as preferred is still optional and still skipped.
        # The clause must be about experience, not merely contain a word
        # that also appears in a hospital's marketing copy. Vibra's section
        # runs on into its benefits blurb, where "fulfilling responsibilities
        # of the role of the hospital" and a PPO plan description both match
        # ACUTE and would otherwise suppress four postings whose one real
        # requirement sentence says "strongly preferred".
        for c in _clauses(exp):
            if (ACUTE.search(c) and re.search(r"(?i)\bexperience\b", c)
                    and not PREFERRED_ONLY.search(c)):
                return Verdict("ACUTE_REQUIRED", c[:200],
                               "names acute-care experience in a requirements "
                               "section without hedging it as preferred")
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


def classify(title: str, description: str) -> Verdict:
    """
    Read the requirements first, then apply the title grade.

    Order matters, and getting it wrong is how this rule went in the first
    time. Applying the grade up front short-circuited the acute-care check,
    so "Staff Nurse II, Pre-Registration" came back labelled by its title
    instead of by the sentence that actually disqualifies it. Both verdicts
    hide the posting, so nothing looked wrong on the page — but the rule
    that guards the most expensive bug in this codebase had stopped being
    reached, and its regression test was the only thing that noticed.

    So: let the posting earn a verdict on its own evidence, and only fall
    back to the grade when the verdict would otherwise have been shown.

      - STAFF_NURSE_I survives the grade. A posting that says "new grads
        welcome" in its body, or offers a Level I rung as "RN I/II", is a
        job you can take whatever the title says on it.
      - ACUTE_REQUIRED survives it too, and keeps the requirement sentence
        as its evidence, which a title-only verdict cannot give you.
    """
    v = _classify_requirements(title, description)
    if v.bucket == "STAFF_NURSE_I" or v.bucket in HIDE:
        return v
    if TITLE_LEVEL_II_GRADED.search(title or ""):
        return Verdict("LEVEL_II_TITLE", title or "",
                       "title is a graded Level II+ role, the rung above the "
                       "one a new graduate is hired into")
    return v


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
