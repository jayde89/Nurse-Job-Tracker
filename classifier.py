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

SHOW = {"STAFF_NURSE_I", "NO_EXPERIENCE", "GENERAL_EXPERIENCE", "UNCLEAR",
        "ACUTE_REQUIRED", "LEVEL_II_TITLE"}
HIDE = set()

# Rank for sorting the digest — lower is more interesting to you.
#
# 2026-09-19: the user is no longer a new graduate. He is a BSN RN working
# at San Francisco Post Acute since June 2026 and is hunting for an ACUTE
# CARE hospital role. The old ranking optimised for the opposite person, so
# the two buckets that now matter most — a posting that requires acute
# experience, and a graded Level II title — are ranked first rather than
# suppressed. Nothing is hidden any more: he has experience to argue with,
# so a requirement he doesn't perfectly meet is a judgement call for him to
# make, not one for the scanner to make silently.
RANK = {"ACUTE_REQUIRED": 0, "LEVEL_II_TITLE": 1, "GENERAL_EXPERIENCE": 2,
        "UNCLEAR": 3, "STAFF_NURSE_I": 4, "NO_EXPERIENCE": 5}


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
    # La Clínica's headings. Its Registered Nurse I/II posting states
    # "Possession of a valid RN license ... supplemented by two to three
    # years clinical experience" under these, and with neither of them
    # known the posting parsed to no requirements section at all — so the
    # title rule decided it and a job asking for three years was labelled
    # new-graduate. That is the most expensive verdict this file can get
    # wrong, and a community clinic is exactly where a new graduate looks.
    "MINIMUM JOB REQUIREMENTS", "JOB REQUIREMENTS",
    "EXPERIENCE AND OTHER CERTIFICATIONS",
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

# A distinctive heading may be followed by a colon, a full stop or
# neither. The full stop is not the posting's: _html_to_text puts one
# there when a block element ends without punctuation, which is exactly
# what a heading in its own <p> or <strong> looks like. Before this,
# "Minimum Job Requirements." stopped being a heading the moment the
# adapters started keeping statement boundaries, and La Clínica's
# Registered Nurse I/II — which asks for "two to three years clinical
# experience" under it — parsed to no requirements section at all and was
# labelled new-graduate by its title. The prose labels still require a
# colon, because "Experience." at the end of a sentence is a sentence.
_LABEL_RE = re.compile(
    r"(?i)\b(" + "|".join(re.escape(s) for s in _DISTINCT_LABELS) + r")\s*[:.]?\s"
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
    # A section the posting labelled EXPERIENCE outranks one it labelled
    # QUALIFICATIONS. Both of San Francisco's staff RN sections state a
    # duration, so the substance test below cannot separate them, and the
    # declared order decided it — in favour of the section holding the
    # recruitment process. The more specific heading is the better answer
    # whenever a posting writes both.
    keys = ("TYPICAL EXPERIENCE", "MINIMUM EXPERIENCE",
            "REQUIRED EXPERIENCE", "EXPERIENCE AND OTHER CERTIFICATIONS",
            "EXPERIENCE", "MINIMUM JOB REQUIREMENTS", "JOB REQUIREMENTS",
            "AS TYPICALLY ACQUIRED IN", "MINIMUM QUALIFICATIONS",
            # Vibra states its experience requirement here and nowhere
            # else, so without this the posting reads as having no
            # requirements at all.
            "ADDITIONAL QUALIFICATIONS/SKILLS", "ADDITIONAL QUALIFICATIONS",
            "REQUIRED SKILLS")
    present = [sec[k] for k in keys if k in sec]

    # When a posting has more than one of these, the order above is not
    # enough. San Francisco writes both, and its MINIMUM QUALIFICATIONS
    # section holds a licence list and the recruitment process ("Applicants
    # may be required to submit verification of qualifying education and
    # experience") while the requirement that actually gates the job —
    # "One (1) year ... of verifiable experience as a Registered Nurse" —
    # sits in the EXPERIENCE section below it. Reading them in declared
    # order took the first and threw the second away, so a Public Health
    # Nurse posting was labelled from the evidence "(Required)." and a
    # staff RN posting from a sentence about submitting paperwork.
    #
    # So: prefer a section that states a duration *and* speaks of
    # experience, then one that speaks of experience at all, then the
    # declared order. This only chooses which sentence the verdict is read
    # from; it does not change what any sentence means.
    for body in present:
        if DURATION.search(body) and re.search(r"(?i)\bexperien", body):
            return body, False
    for body in present:
        if re.search(r"(?i)\bexperien", body):
            return body, False
    if present:
        return present[0], False

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
    # Detection stays blunt. Only the quote is chosen with care: the bare
    # span is "40 years", and La Clínica's marketing paragraph — "40 years
    # advocating for and creating a health home" — is where the first one
    # on the page lives. A verdict evidenced by that supports nothing, so
    # quote the sentence the duration sits in, and prefer a sentence that
    # is about experience when the posting has one.
    fallback = None
    for m in _HARD_DURATION.finditer(text or ""):
        span = m.group(0).strip()
        if _ONBOARDING.search(span):
            continue          # a deadline after you start, not a prerequisite
        start = max(text.rfind(".", 0, m.start()),
                    text.rfind(";", 0, m.start())) + 1
        end = text.find(".", m.end())
        sentence = text[start:end + 1 if end > 0 else len(text)].strip()
        sentence = sentence or span
        if re.search(r"(?i)\bexperien", sentence):
            return sentence[:200]
        if fallback is None:
            fallback = sentence[:200]
    return fallback


def _hedged_experience_clause(text: str) -> str | None:
    """
    The clause a "no experience required" verdict actually rests on: one
    that talks about experience AND hedges it as preferred.

    Searched in the experience section first and then across the whole
    posting, because the section itself can be cut short: "Acute care
    facility experience: Preferred" contains the word the section splitter
    treats as a heading, so the section ends mid-bullet at "Acute care
    facility". Widening the *section* rule to fix that risks missing a real
    requirement, which is the one direction that costs a job; widening the
    search for the *quote* costs nothing, because the verdict is already
    decided by the time this runs.

    Without this the evidence was the first 200 characters of the whole
    experience section, which on a bulleted posting is whatever bullet
    happens to come first. Adventist lists

        Bachelor's Degree in Nursing (BSN): Preferred
        Acute care facility experience: Preferred

    and the digest quoted "(BSN): Preferred. Acute care facility" as the
    grounds for "no experience required" — a quote about a degree, cut off
    mid-phrase, standing in for a claim about experience. The label was
    right and the evidence did not show it, which is the same failure as a
    wrong label: CLAUDE.md's contract is that the reader can check the
    verdict against the sentence under it.
    """
    hits = [c.strip() for c in _clauses(text)
            if re.search(r"(?i)\bexperience\b", c) and PREFERRED_ONLY.search(c)]
    # The shortest match, not the first. The first is usually the clause
    # that still carries the section heading in front of it — "Job
    # Requirements: Education and Work Experience: Bachelor's Degree in
    # Nursing (BSN): Preferred." matches on the heading's own word
    # "Experience" and on a hedge about a degree. The clause that actually
    # says what this verdict claims is the tight one underneath it,
    # "Acute care facility experience: Preferred."
    return min(hits, key=len) if hits else None


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

# The optional "level"/"lvl"/"grade" matters: Sacramento County writes
# "Registered Nurse Level I/II" and "Public Health Nurse Level I/II", and
# without it the grade word sat between the noun and the numeral so
# neither this pattern nor the Level II one below saw the grade at all.
_GRADE_WORD = r"(?:\s*(?:level|lvl|grade))?"

TITLE_LEVEL_I = re.compile(
    r"(?i)\b(staff nurse|clinical nurse|registered nurse|ambulatory services nurse"
    r"|nurse|rn)" + _GRADE_WORD + r"\s*(i|1)\b(?!\s*[iv])")

# "Nurse II" must never match Level I. Guard explicitly.
TITLE_LEVEL_2PLUS = re.compile(r"(?i)\b(ii|iii|iv|v|2|3|4|5)\b")

# A combined grade — "Registered Nurse Level I/II", "Public Health Nurse
# Level I/II", "RN I-II" — is a Level I role: it is the rung a new
# graduate is hired into, with the II sitting above it on the same
# requisition. Sacramento County posts several of these and they were
# landing in UNCLEAR, which shows them but buries them below the
# no-experience pile instead of putting them in "worth applying to now".
#
# It has to be checked before the guard above, because that guard sees the
# "II" in "I/II" and refuses the Level I reading.
# Two forms. The first needs the nurse noun in front of the grade; the
# second matches a bare "Level I/II" anywhere, because Sacramento County
# writes "Registered Nurse D/CF (Level I/II)" with the assignment code
# between the noun and the grade. The word "level" is what makes the
# second form safe — it marks the numerals as a job grade rather than a
# unit number, so it cannot match "RN, 2 West Medical" or "Unit 4 South".
TITLE_LEVEL_I_COMBINED = re.compile(
    r"(?i)\b(?:nurse|rn)(?:\s*(?:level|lvl|grade))?\s*"
    r"(?:i|1)\s*[/&-]\s*(?:ii|2)\b"
    r"|\b(?:level|lvl|grade)\s*(?:i|1)\s*[/&-]\s*(?:ii|2)\b")

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
# The same grade word, and for the same reason in reverse: "Staff Nurse
# Level II, ICU" and "Nurse Level 2 - Float Pool" were reaching the list
# as UNCLEAR because "Level" sat between the noun and the numeral, while
# the identical "Staff Nurse II" was correctly hidden. The user confirmed
# on 2026-09-09 that no Staff Nurse II role should reach him.
#
# A combined "Level I/II" must still come through: it is a posting a new
# graduate is hired into at the I rung. It survives because the numeral
# has to follow the noun (with at most a grade word between), and in
# "Nurse Level I/II" what follows is "I", not "II" — the "II" after the
# slash has no nurse noun in front of it.
TITLE_LEVEL_II_GRADED = re.compile(
    r"(?i)\b(?:nurse|rn)" + _GRADE_WORD + r"\s*(ii|iii|iv|2|3|4)\b")

# "Sub-acute", "post-acute" and "non-acute" are not acute care — they are
# the settings the user's own criteria call basic RN experience that is
# not acute care, and a posting asking for that experience belongs on the
# list. Without this guard the word "acute" inside them matched, and
# Sonoma Specialty Hospital's staff RN posting — a long-term acute care
# hospital in Sebastopol asking for "One-year sub/post-acute care
# experience" — was suppressed as ACUTE_REQUIRED on the day the hospital
# was added. Suppression is for the grade above a new graduate and for
# acute-care gates, not for the settings next door to them.
_ACUTE = r"(?<!sub)(?<!sub[- ])(?<!post)(?<!post[- ])(?<!non)(?<!non[- ])acute"

ACUTE = re.compile(
    r"(?i)\b(" + _ACUTE + r" care|" + _ACUTE + r"[- ]care|inpatient|hospital"
    r"|med[- ]?surg"
    r"|telemetry|critical care|icu|intensive care|emergency (?:room|department|dept)"
    r"|er experience|ed experience|bedside)\b")

# ACUTE alone is far too loose to prove a requirement, because "hospital"
# is one of its markers and every employer whose name contains the word
# matches it. Central Valley Specialty Hospital is a long-term acute care
# hospital in Modesto whose posting says "We encourage new RNs to apply"
# and asks only for a licence, BLS and ACLS — and it was suppressed as
# ACUTE_REQUIRED, because its own benefits paragraph ("wages determined
# based on ... qualifications and experience") contains both an ACUTE
# marker (the hospital's name) and the word "experience".
#
# That is the shape CLAUDE.md already warns about with Vibra's benefits
# blurb; requiring the clause to also say "experience" was not enough,
# because benefits copy says "experience" too. So the acute marker and the
# experience word have to be *about each other*: "acute care experience",
# "experience in an acute care setting", "hospital experience". A sentence
# that merely contains both words somewhere no longer counts.
#
# This only ever moves a posting from hidden to shown, which is the safe
# direction — the genuine gates ("two years of acute care experience
# required", John Muir's "6 Months Nursing - Medical Acute Care -
# Required") still match, and have tests.
_ACUTE_WORD = (_ACUTE + r"[- ]care|" + _ACUTE + r"|inpatient|hospital"
               r"|med[- ]?surg|telemetry"
               r"|critical care|icu|intensive care|emergency (?:room|department|dept)"
               r"|bedside")
ACUTE_EXPERIENCE = re.compile(
    r"(?i)(?:(?:" + _ACUTE_WORD + r")[\w ,/()-]{0,40}\bexperience\b"
    r"|\bexperience\b[\w ,/()-]{0,40}(?:" + _ACUTE_WORD + r"))")

# A clause that offers acute care as one acceptable setting among several
# is not an acute-care gate. San Francisco's Public Health Nurse posting
# asks for "One (1) year of verifiable experience as a Registered Nurse in
# an acute hospital, primary care facility, home health agency, ..." — a
# nurse whose year was spent in a clinic qualifies, and the user's criteria
# name exactly that kind of experience as one that belongs on the list.
# Reading the acute word alone would suppress the posting on evidence that
# does not support suppression.
NON_ACUTE_SETTING = re.compile(
    r"(?i)\b(primary care|home health|clinic|ambulatory|outpatient|school"
    r"|public health|skilled nursing|long[- ]term care|sub[- ]?acute"
    r"|post[- ]?acute|community health|correctional|hospice|urgent care"
    r"|physician(?:'s)? office|doctor(?:'s)? office)\b")


def _acute_only(clause: str) -> bool:
    """True when the clause demands acute care rather than allowing it."""
    return not NON_ACUTE_SETTING.search(clause or "")


# Sentences about the hiring process, not about the nurse. San Francisco
# puts "Applicants may be required to submit verification of qualifying
# education and experience at any point during the recruitment and
# selection process" inside the section this file reads as requirements,
# and it contains both a required-word and the word experience — so a per
# diem posting whose only real gate is a licence was labelled "requires
# nursing experience" and evidenced with a sentence about paperwork.
# A clause matching this is a requirement only if it states both a
# duration and a required-word — "Applicants must have at least two years
# of ICU experience" is a real gate however it is worded, while "in order
# to place you at the appropriate salary step, please include your
# complete and verifiable registered nursing employment history" mentions
# years and requires nothing.
PROCESS_CLAUSE = re.compile(
    r"(?i)(applicants? (?:may|must|will) be required to"
    r"|submit(?:ted)? verification|verification of (?:qualifying )?"
    r"(?:education|experience)|(?:education|experience) verification"
    r"|how to verify|verifying (?:foreign|qualifying)"
    r"|every application is reviewed|please (?:include|submit|attach|note)"
    r"|in order to place you|salary step"
    r"|recruitment and selection process|misrepresentation|falsif)")

REQUIRED_WORD = re.compile(r"(?i)\b(required|must have|minimum of|at least)\b")
PREFERRED_ONLY = re.compile(r"(?i)\b(preferred|desirable|a plus|nice to have)\b")

# "2 years", "1 year", "six months", "One (1) year"
DURATION = re.compile(
    r"(?i)\b(" + _NUM_WORD + r")" + _PAREN_NUM +
    r"\s*(\+|plus)?\s*(year|yr|month)s?\b")

# "RN Resident" / "RN Residency" are spelled without the word "nurse" by
# Adventist, whose "RN Resident | Full Time Regular | Dayshift | Surgical
# ICU 1" is a residency posting — the single most applicable kind of
# posting there is — and was reaching the digest as a generic
# NO_EXPERIENCE row instead of a Level I one.
#
# The adjacency is load-bearing. A bare \bresiden(t|cy)\b would match the
# skilled-nursing postings that say "provide exceptional nursing care to
# residents", where the residents are the patients.
# "We encourage new RNs to apply" is an explicit invitation to new
# graduates and was not matching, because the pattern only knew the phrase
# "new grad". Central Valley Specialty Hospital — the LTAC in Modesto —
# writes it that way, and its posting was landing as a generic
# NO_EXPERIENCE row evidenced by its own benefits paragraph.
#
# Both word orders appear in the wild ("new RNs are encouraged",
# "we encourage new RNs"), and an invitation verb is required in either
# direction so that a bare "new RN" — which shows up in sentences about
# onboarding and orientation — is not read as an invitation.
NEW_GRAD = re.compile(
    r"(?i)\b(new grad(uate)?s?( are)?( welcome| encouraged| eligible)?"
    r"|(nurse|rn|registered nurse) residen(cy|t)"
    r"|graduate nurse program|no experience (is )?required"
    r"|new graduate rn"
    r"|new (?:grad\w*|RNs?|nurses?)\s+(?:are\s+)?"
    r"(?:welcome|encouraged|eligible|invited)"
    r"|(?:encourage|welcome|invite)\w*\s+(?:all\s+)?"
    r"new\s+(?:grad\w*|RNs?|nurses?))\b")


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
                and _acute_only(line) \
                and DURATION.search(line) and not PREFERRED_ONLY.search(line):
            return Verdict("ACUTE_REQUIRED", line[:200],
                           "states a required acute-care duration")

    # 1. Explicit new-grad language beats everything.
    #
    # Quote whichever of the two actually said it. _snippet falls back to
    # the opening of the text when its pattern does not match, so a
    # title-only signal — Adventist's "RN Resident | Full Time Regular |
    # Dayshift | Surgical ICU 1", whose body never says "resident" again —
    # produced a verdict evidenced by "Located in one of the most
    # beautiful regions in the United States, St. Helena Hospital was
    # founded in 1878...". That is marketing copy standing in for a
    # requirement, which is the failure this file exists to prevent.
    if NEW_GRAD.search(desc):
        return Verdict("STAFF_NURSE_I", _snippet(desc, NEW_GRAD),
                       "posting explicitly names new grads or a residency")
    if NEW_GRAD.search(t):
        return Verdict("STAFF_NURSE_I", t,
                       "title names a residency or new-graduate role")

    # 2. Level I in the title — but only if no higher level is also present.
    #    "Clinical Nurse II" contains no Level-I match; "RN I/II" does, and
    #    should not count as Level I.
    #    A Level I title is a strong signal and it is not a promise. La
    #    Clínica posts "Registered Nurse I/II" and then asks, in the body,
    #    for "a valid RN license ... supplemented by two to three years
    #    clinical experience". Answering that with "Level I / new grad",
    #    evidenced by the title, is the false new-graduate call this file
    #    exists to prevent — and a community clinic is exactly where a new
    #    graduate looks. So the title decides only when the posting does
    #    not contradict it with a time requirement of its own. UNCLEAR
    #    still reaches the user; it just stops promising something the
    #    posting never said. Explicit new-grad language above is left
    #    alone: that is the employer saying it in its own words.
    if TITLE_LEVEL_I_COMBINED.search(t):
        hard = _has_unhedged_duration(desc)
        if hard:
            return Verdict("UNCLEAR", hard,
                           "title offers a Level I rung, but the posting "
                           "states a time requirement — read it yourself")
        return Verdict("STAFF_NURSE_I", t,
                       "title is a combined Level I/II role, which is hired "
                       "at the Level I rung")
    head = re.split(r"[-–—,(]", t)[0]
    if TITLE_LEVEL_I.search(t) and not TITLE_LEVEL_2PLUS.search(head):
        hard = _has_unhedged_duration(desc)
        if hard:
            return Verdict("UNCLEAR", hard,
                           "title is a Level I role, but the posting states a "
                           "time requirement — read it yourself")
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
        return Verdict("NO_EXPERIENCE",
                       (_hedged_experience_clause(exp)
                        or _hedged_experience_clause(desc) or exp)[:200],
                       "experience appears only under a PREFERRED heading")

    # 4. Judge each clause on its own. A clause is a hard requirement if it
    #    states a duration or a required-word AND does not itself say
    #    "preferred".
    required_clauses = []
    for c in _clauses(exp):
        if PREFERRED_ONLY.search(c):
            continue                       # this clause is optional
        if PROCESS_CLAUSE.search(c) and not (
                DURATION.search(c) and REQUIRED_WORD.search(c)):
            continue                       # about the application, not the job
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
            if (ACUTE_EXPERIENCE.search(c) and _acute_only(c)
                    and not PREFERRED_ONLY.search(c)):
                return Verdict("ACUTE_REQUIRED", c[:200],
                               "names acute-care experience in a requirements "
                               "section without hedging it as preferred")
        hard = _has_unhedged_duration(desc)
        if hard:
            return Verdict("UNCLEAR", hard,
                           "clauses read as preferred, but a time requirement "
                           "appears elsewhere in the posting")
        return Verdict("NO_EXPERIENCE",
                       (_hedged_experience_clause(exp)
                        or _hedged_experience_clause(desc) or exp)[:200],
                       "experience mentioned, but every clause is preferred "
                       "rather than required")

    # Acute care only counts if it appears in a clause that is required.
    for c in required_clauses:
        if ACUTE.search(c) and _acute_only(c):
            return Verdict("ACUTE_REQUIRED", c[:200],
                           "requires acute-care or hospital experience")

    # ...and the clause may have lost the words that make it acute. The
    # section splitter treats "experience" as a heading wherever it finds
    # it, so a posting whose requirement reads
    #     Acute care experience: 2 years Required
    # yields a section body that starts *after* the colon — "2 years
    # Required" — with "Acute care" left outside it. That clause has a
    # duration and a required-word and no acute marker, so it read as
    # GENERAL_EXPERIENCE and the posting was shown as one a new graduate
    # could apply to. Re-read each required clause in its full sentence
    # before concluding the requirement is not acute.
    #
    # This only ever moves a posting from shown to hidden, so it is the
    # one widening of suppression this file allows without the user
    # asking: the posting states a required acute duration in its own
    # words, and an application against it was never possible.
    for c in required_clauses:
        stem = c.strip()[:40]
        for full in _clauses(desc):
            if (stem and stem in full and ACUTE_EXPERIENCE.search(full)
                    and _acute_only(full)):
                return Verdict("ACUTE_REQUIRED", full[:200],
                               "requires acute-care experience; the section "
                               "split had separated it from its own clause")

    # Quote the clause that carries the requirement, not whichever one
    # came first. Document order was fine while a whole bulleted list
    # arrived as one run-on clause; once the adapters started keeping
    # statement boundaries, the first required clause on a San Francisco
    # posting became the bare heading "(Required)." and, on another, the
    # recruitment boilerplate "Applicants may be required to submit
    # verification of qualifying education and experience" — while the
    # sentence that actually gates the job, "one (1) year of experience
    # working as a Registered Nurse", sat further down. Evidence that does
    # not support its own label is the thing this codebase treats as a
    # bug, so prefer a clause that says what is required and for how long.
    def _evidence_rank(c: str) -> int:
        has_dur = bool(DURATION.search(c))
        says_exp = bool(re.search(r"(?i)\bexperien", c))
        if has_dur and says_exp:
            return 0
        if says_exp:
            return 1
        if has_dur:
            return 2
        return 3

    best = min(required_clauses, key=_evidence_rank)

    # "Requires nursing experience" has to rest on a clause that says so.
    # When the section came from a generic QUALIFICATIONS heading — which
    # on a San Francisco posting swallows the whole page, duties and
    # application instructions included — the surviving required clause
    # can be "Performs other related duties as assigned/required", which
    # supports no verdict at all. Say so instead of inventing one. A
    # section the posting itself headed EXPERIENCE is exempt: John Muir
    # writes "Experience: Nursing - Psychiatry - Required" and never
    # repeats the word inside the clause, and that is a real requirement.
    if not any(re.search(r"(?i)\bexperien", c) for c in required_clauses):
        generic = {sections(desc).get(k) for k in
                   ("MINIMUM QUALIFICATIONS", "QUALIFICATIONS")} - {None}
        if exp in generic:
            return Verdict("UNCLEAR", best[:200],
                           "the section read as requirements names no "
                           "experience requirement — read it yourself")

    return Verdict("GENERAL_EXPERIENCE", best[:200],
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

    2026-09-19: this used to test ``v.bucket in HIDE``. That set is now
    empty (nothing is suppressed any more), which silently stopped the
    acute verdict from surviving the grade — precisely the short-circuit
    described above. The condition names the buckets it means instead, so
    it no longer depends on what happens to be suppressed.
    """
    v = _classify_requirements(title, description)
    if v.bucket in ("STAFF_NURSE_I", "ACUTE_REQUIRED"):
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
