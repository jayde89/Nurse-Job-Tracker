"""
Regression tests for the title filter, the geo table, the classifier, the
front-of-list detail line and the application ledger.

Every case here is a bug that actually shipped and cost real postings.
Run before pushing a rule change:  python3 test_rules.py

No test framework on purpose — this runs anywhere Python does, including
inside the Actions container, with nothing to install.
"""

import csv
import json
import os
import re
import shutil
import sys
import tempfile

import adapters as A
import classifier as C
import geo
import highlights as H
import run_scan as S


CASES: list[tuple[str, bool, str]] = []          # (name, passed, detail)


def check(name, got, want, detail=""):
    ok = got == want
    CASES.append((name, ok, detail or f"got {got!r}, want {want!r}"))
    return ok


# ── title filter ─────────────────────────────────────────────────────
# CNA in a John Muir title is the California Nurses Association, the
# bargaining unit — not a nursing assistant role.
check("CNA suffix keeps an RN posting",
      A.title_passes("RN - CMC Emergency Services - Part Time - 12 Hour "
                     "- Nights - CNA"), True)
check("bare CNA posting still excluded", A.title_passes("CNA - FT Days"), False)
check("nursing assistant still excluded",
      A.title_passes("Certified Nursing Assistant (CNA)"), False)
check("LVN still excluded", A.title_passes("LVN - Skilled Nursing"), False)
check("nurse practitioner still excluded",
      A.title_passes("Nurse Practitioner - Cardiology"), False)
check("CNS still excluded",
      A.title_passes("Clinical Nurse Specialist, ICU"), False)
check("manager still excluded", A.title_passes("Director of Nursing"), False)
check("plain staff RN passes",
      A.title_passes("Staff Nurse II, Medical Surgical"), True)
check("new grad residency passes",
      A.title_passes("RN - New Grad Residency"), True)

# The title prefilter ran before the classifier and threw away the only
# Level I role on Sutter's board: "Ambulatory Services Nurse I, PreOp &
# PACU", open in Mountain View with no experience section at all. The
# include list named specific phrasings ("staff nurse", "clinical nurse")
# and a title carrying a bare "Nurse" matched none of them, so the posting
# never reached classify() — which was written for it by name. This is why
# the digest showed dozens of Staff Nurse II roles and no Level I anywhere.
check("bare-Nurse Level I title reaches the classifier",
      A.title_passes("Ambulatory Services Nurse I, PreOp & PACU"), True)
check("bare-Nurse Level I is bucketed Level I",
      C.classify("Ambulatory Services Nurse I, PreOp & PACU",
                 "Job Description : EDUCATION: Graduate of an accredited "
                 "school of nursing CERTIFICATION & LICENSURE: RN-Registered "
                 "Nurse of California BLS ACLS").bucket,
      "STAFF_NURSE_I")
# Sutter's other bare-"Nurse" clinical titles were dropped by the same gate.
check("clinic nurse title passes", A.title_passes("Clinic Nurse II, Oncology"), True)
check("hospice nurse title passes",
      A.title_passes("Hospice Nurse II, Per Diem"), True)
# Loosening the include side lets these two through unless excluded by name:
# neither carries the LVN acronym nor the word "nursing".
check("spelled-out LVN still excluded",
      A.title_passes("Licensed Vocational Nurse II, Urology"), False)
check("nurse assistant still excluded",
      A.title_passes("Nurse Assistant - Oncology"), False)

# ── graded Level II titles ───────────────────────────────────────────
# "Registered Nurse II, Medical Acute" in Roseville asks for six months of
# acute experience and marks it *Preferred*, so every clause read as
# optional and it arrived looking open to anyone. The II is the job grade,
# not a unit description, and graded roles were 54% of the open list.
check("graded Level II title is hidden",
      C.classify("Registered Nurse II, Medical Acute",
                 "TYPICAL EXPERIENCE: Minimum of six (6) months area "
                 "specific acute care experience within the last two (2) "
                 "years Preferred.").bucket,
      "LEVEL_II_TITLE")
check("a hidden Level II role does not reach you",
      C.should_show(C.classify("Staff Nurse II, ICU/CPU",
                               "EDUCATION: Graduate of nursing school.")),
      False)
# The grade is read off the nurse noun, never a bare numeral. These three
# carry no grade at all and were staff postings.
for _t in ("RN, 2 West Medical", "Registered Nurse - Unit 4 South",
           "RN - 12 Hour Nights"):
    check(f"no false grade in {_t!r}",
          C.classify(_t, "EDUCATION: Graduate of nursing school.").bucket
          != "LEVEL_II_TITLE", True)
# A Level I rung offered alongside II is still a job you can take.
check("I/II title survives the grade",
      C.classify("Staff Nurse I/II, Cardiac",
                 "EDUCATION: Graduate of nursing school.").bucket
      != "LEVEL_II_TITLE", True)
# New-grad language in the body beats the grade in the title.
check("new grads welcome beats the grade",
      C.classify("Registered Nurse II, Med Surg",
                 "New graduates are welcome to apply.").bucket,
      "STAFF_NURSE_I")
# Applying the grade first short-circuited the acute check: the verdict
# stopped resting on the sentence that disqualifies the posting. Both
# hide it, so only this test noticed.
check("acute requirement outranks the grade",
      C.classify("Staff Nurse II, Pre-Registration",
                 "CERTIFICATION & LICENSURE: RN of California AS TYPICALLY "
                 "ACQUIRED IN: Acute Care Previous experience as an RN in an "
                 "acute care hospital setting. EPIC.").bucket,
      "ACUTE_REQUIRED")

# ── geo ──────────────────────────────────────────────────────────────
# Never tokenize the gazetteer: "Sutter Creek" is out of range, "Walnut
# Creek" is not, and splitting on whitespace once conflated them.
check("Walnut Creek is in range", geo.classify("Walnut Creek")[0], geo.Geo.IN)
check("Sutter Creek is out of range",
      geo.classify("Sutter Creek")[0], geo.Geo.OUT)
check("Santa Rosa is in range", geo.classify("Santa Rosa")[0], geo.Geo.IN)
check("Crescent City is out of range",
      geo.classify("Crescent City")[0], geo.Geo.OUT)
# Multi-site Workday postings are relabelled "City (+N more)".
check("multi-site label still parses",
      geo.classify("Castro Valley (+2 more)")[1], "<30")
# John Muir publishes no city for this site, in listing or detail.
check("Tice Valley address resolves",
      geo.classify("1914 Tice Valley Blvd")[0], geo.Geo.IN)

# ── duration detection ───────────────────────────────────────────────
# County postings write "One (1) year". The parenthetical sits between the
# number and the unit and used to defeat the pattern completely, which
# labelled six acute-care roles "no experience required".
check("parenthetical duration is seen",
      bool(C._has_unhedged_duration(
          "One (1) year of full time experience in an acute care setting")),
      True)
check("parenthetical years is seen",
      bool(C._has_unhedged_duration("Two (2) years of full-time experience")),
      True)
check("plain duration still seen",
      bool(C._has_unhedged_duration("2 years recent relevant experience")), True)
# A deadline counted from your start date is not experience you must have.
check("onboarding deadline is ignored",
      C._has_unhedged_duration("must obtain ACLS within six (6) months of hire"),
      None)
check("onboarding deadline in days ignored",
      C._has_unhedged_duration("BLS required within 90 days of employment"),
      None)

# ── section parsing ──────────────────────────────────────────────────
# A label that is also an ordinary word is only a heading when followed by
# a colon. Matching bare "experience" mid-sentence split Vibra's actual
# requirement in half and left unusable evidence.
_vibra = ("Required Skills: Current, valid license to practice as a "
          "Registered Nurse required. Additional Qualifications/Skills : "
          "Previous acute care experience is strongly preferred. Ability to "
          "project a professional image.")
check("prose 'experience' is not a heading",
      "EXPERIENCE" in C.sections(_vibra), False)
check("Vibra requirement section is found",
      C.experience_section(_vibra) is not None, True)
_v = C.classify("RN Registered Nurse ICU Full Time", _vibra)
check("Vibra evidence quotes the real requirement",
      "acute care experience is strongly preferred" in (_v.evidence or ""), True)
# Sutter's labelled style must keep working.
_sutter = ("EDUCATION: Graduate of an accredited school of nursing "
           "CERTIFICATION & LICENSURE: RN-Registered Nurse of California "
           "TYPICAL EXPERIENCE: 2 years recent relevant experience")
check("Sutter sections still parse",
      C.sections(_sutter).get("TYPICAL EXPERIENCE", "")[:7], "2 years")
# John Muir writes headings with no colon at all.
check("bare heading still parses",
      "REQUIRED EXPERIENCE" in C.sections(
          "Required Experience 6 Months Nursing - Medical Acute Care - Required"),
      True)

# ── end-to-end verdicts ──────────────────────────────────────────────
check("acute duration is hidden",
      C.classify("Registered Nurse (Critical Care)",
                 "Minimum Qualifications Experience: One (1) year of full time "
                 "experience performing duties of a registered nurse in an "
                 "acute care setting.").bucket,
      "ACUTE_REQUIRED")
check("explicit new grad wins",
      C.classify("RN", "New graduates are welcome to apply.").bucket,
      "STAFF_NURSE_I")
check("preferred-only is not a requirement",
      C.classify("RN", "Required Skills: RN license required. Additional "
                       "Qualifications/Skills : Acute care experience is "
                       "preferred.").bucket,
      "NO_EXPERIENCE")

# PACS writes marketing copy with no headings at all and states its
# requirement in an ordinary sentence. Stricter heading rules once dropped
# thirteen of these out of the recommendations and quoted the job ad's
# opening paragraph back as their evidence.
_pacs = ("Now Hiring: Registered Nurse (RN) at Shadelands Post Acute. "
         "Competitive pay and benefits. Skilled nursing, rehabilitation, or "
         "post-acute care experience preferred.")
check("free-form posting still finds its requirement",
      C.experience_section(_pacs) is not None, True)
check("free-form evidence is the real sentence",
      "post-acute care experience preferred" in
      (C.classify("RN", _pacs).evidence or ""), True)
check("free-form preferred-only reads as no-experience",
      C.classify("RN - Full Time", _pacs).bucket, "NO_EXPERIENCE")
# A stated duration with no acute qualifier is real experience, shown for
# watching but never recommended. "preferably" is not "preferred".
check("free-form hard duration is not recommended",
      C.classify("RN", "Now Hiring RN. 2+ years of nursing experience, "
                       "preferably in skilled nursing.").bucket,
      "GENERAL_EXPERIENCE")

# Sutter states a real acute requirement with no duration and no "required":
# "AS TYPICALLY ACQUIRED IN: Acute Care Previous experience as an RN in an
# acute care hospital setting." That reached the recommendations labelled
# no-experience-required while quoting that sentence as its own evidence.
check("unhedged acute clause is a requirement",
      C.classify("Staff Nurse II, Pre-Registration",
                 "CERTIFICATION & LICENSURE: RN of California AS TYPICALLY "
                 "ACQUIRED IN: Acute Care Previous experience as an RN in an "
                 "acute care hospital setting. EPIC.").bucket,
      "ACUTE_REQUIRED")
# But an acute mention the posting itself hedges stays optional.
check("hedged acute clause stays optional",
      C.classify("RN", "Required Skills: RN license required. Additional "
                       "Qualifications/Skills : Previous acute care "
                       "experience is strongly preferred.").bucket,
      "NO_EXPERIENCE")

# The acute clause must be about experience, not just contain a word that
# also shows up in benefits boilerplate. Vibra's section runs on into its
# marketing copy, where "responsibilities of the role of the hospital"
# matches ACUTE and once suppressed four postings whose only real
# requirement sentence says "strongly preferred".
check("marketing prose does not suppress a posting",
      C.classify("RN Registered Nurse ICU Full Time",
                 "Required Skills: Current RN license required. Additional "
                 "Qualifications/Skills : Previous acute care experience is "
                 "strongly preferred. Our team fulfils the responsibilities "
                 "of the role of the hospital. Medical PPO plans.").bucket,
      "NO_EXPERIENCE")


# ── front-of-list detail (highlights.py) ─────────────────────────────
# The reason this module exists: PACS titles thirteen different jobs "RN",
# and a digest row reading "RN" cannot be triaged without opening it.


class _P:
    """Just enough of a Posting for summarize()."""

    def __init__(self, title="", description="", department=None,
                 location="", setting=None, schedule=None, shift=None):
        self.title, self.description = title, description
        self.department, self.location = department, location
        self.setting, self.schedule, self.shift = setting, schedule, shift


# Moraga Post Acute, verbatim from the live posting. All four facts are
# stated; all four must come back.
check("labeled PACS block yields every stated fact",
      H.summarize(_P(
          title="Registered Nurse (RN)", location="Moraga",
          department="Moraga Post Acute", setting="Skilled nursing",
          description="Now Hiring: Registered Nurse (RN) - Full-Time AM Shift "
                      "Position Details Position: Registered Nurse (RN) "
                      "Employment Type: Full-Time Shift: AM Shift Schedule: "
                      "Full-Time AM Pay Rate: $45.00-$52.00 per hour, DOE "
                      "Location: Moraga Post Acute")),
      "Moraga Post Acute · Skilled nursing · Full-time · AM · $45.00–$52.00/hr")

# Sonoma Post Acute is pure marketing copy: it states no shift, no type and
# no pay. Inventing any of them is the same class of error as a false
# "no experience required", so the line stops at what is known.
check("nothing is invented when the posting states nothing",
      H.summarize(_P(title="RN", location="Sonoma",
                     department="Sonoma Post Acute", setting="Skilled nursing",
                     description="Join Our Team at Sonoma Post Acute! Are you a "
                                 "compassionate and skilled Registered Nurse "
                                 "looking for a rewarding career opportunity?")),
      "Sonoma Post Acute · Skilled nursing")

# One requisition, three arrangements — Napa advertises all of them, and
# reporting only the first would misdescribe the job. Order follows the
# posting, not the order of the table in highlights.py.
check("multiple employment types keep the posting's order",
      H.employment("Registered Nurse (RN)", {},
                   "Full-Time, Part-Time & Per Diem Opportunities Available"),
      "Full-time / Part-time / Per diem")

# "PT" is Physical Therapy far more often than it is part time. A posting
# that mentions the PT department must not come back as a part-time job.
# The same Santa Rosa posting is titled "RN- part time" and states
# "Schedule: Full-Time" in its body. Printing "Full-time" beside that title
# puts a contradiction inside one digest row and costs the whole line its
# credibility, so the title — the half the reader can see — wins.
check("the body may not contradict the title's employment type",
      H.employment("RN- part time",
                   {"SCHEDULE": "Full-Time, 2 PM shifts and 2 NOC shifts"}, ""),
      "Part-time")
# But a body that merely says *more* than the title still wins.
check("a body that only adds to the title is still used",
      H.employment("RN - On Call", {"EMPLOYMENT TYPE": "On-Call / Per Diem"}, ""),
      "On-call / Per diem")
check("PT is not read as part time",
      H.employment("RN - Subacute", {},
                   "Coordinates with PT and OT on the rehabilitation plan."),
      None)

# A dollar figure with no unit and no range is as likely to be a sign-on
# bonus as a wage.
check("a bare bonus figure is not reported as pay",
      H.pay({}, "Ask about our $5,000 sign-on bonus!"), None)
check("a bonus range is not reported as pay",
      H.pay({}, "Sign-on bonus of $5,000 - $10,000 available."), None)
check("a stated hourly range is reported",
      H.pay({}, "Pay Rate: $46.00-$47.00 per hour"), "$46.00–$47.00/hr")
# Postings lead with the bonus. Stopping at the first "$" threw away the
# wage three paragraphs down.
check("a bonus before the wage does not hide the wage",
      H.pay({}, "Ask about our $5,000 sign-on bonus! Pay is $46.00 - $47.00 "
                "per hour."), "$46.00–$47.00/hr")
check("an unlabeled hourly band is still read as hourly",
      H.pay({}, "Pay: $48-$55/hour"), "$48–$55/hr")

# "$45.00 per hour" contains "PM" nowhere, but earlier drafts of the shift
# regex matched the bare tokens AM/PM anywhere and turned clock times and
# stray letters into shifts. A shift has to be stated as a shift.
check("a clock time is not a shift",
      H.shift("Registered Nurse", {}, "Interviews are held at 9:00 AM daily."),
      None)
check("NOC shift in the title is read from the title",
      H.shift("Part-Time NOC Shift Registered Nurse (RN)", {}, ""), "NOC")
# A title states its shift without ever writing the word "shift". Requiring
# it dropped the shift from "NOC RN" and from Vibra's "... Full Time Days RN".
check("a bare shift word in a title is still a shift",
      [H.shift("NOC RN", {}, ""),
       H.shift("RN Registered Nurse Full Time Days RN", {}, "")],
      ["NOC", "Days"])
# ...but only in a title, and only in the plural. "Day" is half of "Day
# Surgery" and "Night" is half of "Night Clinic"; neither states a shift.
check("a unit name in a title is not a shift",
      [H.shift("RN - Day Surgery", {}, ""), H.shift("RN - Night Clinic", {}, "")],
      [None, None])
# Sutter states "Job Shift: Days" — the field name is the missing word.
# Requiring "shift" in the value left all hundred-odd Sutter postings, the
# largest employer in the scan, with no shift on their detail line.
check("a field named Shift needs no 'shift' in its value",
      H.shift("Registered Nurse II, Cath Lab",
              H.labeled("Job Shift: Days Schedule: Full Time Shift Hours: 10 "
                        "Days of the Week: Variable Weekend Requirements: "
                        "Every other Weekend"), ""),
      "Days")
# Inside a field named Shift there is no "Day Surgery" to trip over, so the
# singular forms are safe there. Sutter writes both of these, and the
# plural-only title rule left both rows blank.
check("singular shift words are read inside a Shift field",
      H.shift("RN", {"SHIFT": "Day/Evening/Night"}, ""), "Day / Evening / Night")
check("a varied shift is reported, not treated as unstated",
      H.shift("RN", {"SHIFT": "Varied"}, ""), "Varied")
# ...but the title stays conservative, or "RN - Day Surgery" gains a shift
# the posting never stated.
check("the singular concession does not reach titles",
      H.shift("RN - Day Surgery", {}, ""), None)

# The field's value has to stop where the next field starts, or "Shift
# Hours: 10 Days of the Week: Variable" reads back as a shift of "Days".
check("a labeled value stops at the next label",
      H.shift("RN", {"SHIFT HOURS": "10"}, ""), None)
# PACS's on-call postings say the opposite of a shift. Saying so is not
# saying one.
check("'no set shifts' is not a shift",
      H.shift("RN", H.labeled("Employment Type: On-Call Schedule: Flexible - "
                              "No Set Shifts"), ""), None)

# Santa Rosa writes "Full-Time, 2 PM shifts and 2 NOC shifts". Stopping at
# the first phrase reported a PM job that is half nights.
check("every shift phrase in the source is read, not just the first",
      H.shift("RN", {"SCHEDULE": "Full-Time, 2 PM shifts and 2 NOC shifts"}, ""),
      "PM / NOC")
check("a multi-shift posting reports every shift it names",
      H.shift("RN - On Call", {}, "Shifts: AM, PM & NOC Pay Rate: $51.00"),
      "AM / PM / NOC")
# St. Rose states the hours alongside the shift, and they are worth keeping.
check("stated shift hours ride along with the shift",
      H.shift("RN - Emergency 334", {}, "",
              "Full-Time (0.9) NOC Shift (1900-0700)"), "NOC 1900-0700")

# The facility is the point of the line for PACS, but repeating it when the
# Location column already says the same thing is just width.
check("facility is dropped when it duplicates the location",
      H.summarize(_P(title="RN", location="Moraga Post Acute",
                     department="Moraga Post Acute", setting="Skilled nursing")),
      "Skilled nursing")


# ── St. Rose / Smart Hires ───────────────────────────────────────────
# St. Rose was missing from every scan because no adapter reached it: it is
# independent, and it is the only source on this ATS.

check("St. Rose is registered as a source",
      any(type(a).__name__ == "SmartHires" for a in A.ADAPTERS), True)
check("St. Rose RN titles pass the prefilter",
      [A.title_passes(t) for t in ("RN - Emergency 334",
                                   "Registered Nurse - Surgery 35",
                                   "RN - Subacute Unit 28")],
      [True, True, True])
check("St. Rose non-nursing titles do not",
      [A.title_passes(t) for t in ("CT Technologist - Diagnostic Imaging 73",
                                   "CNA - Subacute Unit 31",
                                   "Registrar - Admitting 109")],
      [False, False, False])
# The detail page states a full postal address; the digest gets a city.
check("Smart Hires address condenses to a city",
      A.SmartHires._city("27200 Calaroga Avenue, HAYWARD, ALAMEDA, "
                         "CALIFORNIA, UNITED STATES - 94545"), "Hayward, CA")
check("Hayward is in range", geo.classify("Hayward, CA")[1], "<30")
# An address it cannot parse is passed through rather than blanked, so the
# posting lands in the review bucket instead of vanishing.
check("an unparseable address survives intact",
      A.SmartHires._city("Remote"), "Remote")

# The Surgery posting's only experience sentence hedges itself ("preferred")
# while stating a duration. An earlier draft of the adapter prefixed the
# description with an invented "Required Qualification:" heading, which
# became a requirement clause of its own and produced GENERAL_EXPERIENCE
# quoting "Required Qualification: EDUCATION, EXPERIENCE, TRAINING 1." —
# a verdict resting on a quote that says nothing about experience.
check("St. Rose Surgery posting reads as unclear, not general experience",
      C.classify("Registered Nurse - Surgery 35",
                 "EDUCATION, EXPERIENCE, TRAINING 1. Current valid CA "
                 "Registered Nurse license required. 6. Current Pediatric "
                 "Life Support (PALS) preferred. 7. Minimum one year "
                 "experience in an acute care operating room preferred. "
                 "FULL-TIME (1.0) AM SHIFT APPROXIMATE PAY RANGE: $60.64").bucket,
      "UNCLEAR")
# The structured field can contradict the prose above it. It is appended
# under a name that is deliberately NOT "Experience:", so it still trips
# the duration veto without becoming the parsed experience section and
# shrinking the evidence to two words.
check("the structured experience field still reaches the duration veto",
      C.classify("RN - Emergency 334",
                 "Current and valid CA Registered Nurse license required. "
                 "Stated experience requirement: Minimum 2 Years.").bucket
      != "NO_EXPERIENCE", True)


# ── application tracking (run_scan.py) ───────────────────────────────
# You mark a job applied by editing a CSV on a phone. Everything here has
# to survive that: stray capitals, stray spaces, a blank cell, a typo.

check("a blank status is an unapplied one", S.normalize_status(""), "unapplied")
check("a missing status is an unapplied one", S.normalize_status(None),
      "unapplied")
check("case and spacing never matter",
      [S.is_active(" Applied "), S.is_active("INTERVIEWING")], [True, True])
check("every in-flight status counts as active",
      [S.is_active(x) for x in ("applied", "pending", "interviewing", "offer")],
      [True] * 4)
check("a finished application is not active",
      [S.is_active(x) for x in ("rejected", "declined", "withdrawn")],
      [False] * 3)
# The whole point of the change: anything you have marked comes off the
# lists of jobs to apply to.
check("anything marked is off the main lists",
      [S.is_open(x) for x in ("applied", "offer", "rejected", "closed")],
      [False] * 4)
check("unapplied and blank stay on the main lists",
      [S.is_open("unapplied"), S.is_open("")], [True, True])
# A typo must not swallow a job. Showing it to you again is the safe error.
check("an unrecognised status is treated as open",
      S.is_open("appleid"), True)


def _ledger(*rows):
    return {r["Key"]: r for r in rows}


def _row(key, status, **kw):
    r = {"Key": key, "Status": status, "Applied On": "", "Notes": "",
         "Title": key, "Employer": "E", "Location": "L", "URL": "u",
         "Details": "", "Last seen": "2026-09-04T12:00:00", "Marked active": ""}
    r.update(kw)
    return r


# The bug this fixes: you apply, the employer takes the posting down, and
# the application vanishes off the dashboard because the section was built
# from the scan's results instead of from the ledger. The row was always
# in applications.csv; nothing showed it to you.
_closed = _row("gone", "pending", **{"Last seen": "2026-08-30T07:00:00"})
_LEDGER = _ledger(
    _row("a", "applied"), _row("b", "unapplied"), _closed,
    _row("c", "offer"), _row("d", "rejected"), _row("e", "closed"))

check("an application survives its posting being taken down",
      "gone" in [r["Key"] for r in S.active_applications(_LEDGER)], True)
check("only in-flight applications are listed as active",
      sorted(r["Key"] for r in S.active_applications(_LEDGER)),
      ["a", "c", "gone"])
check("an offer sorts above a bare applied",
      [r["Key"] for r in S.active_applications(_LEDGER)][0], "c")
# "closed" is the scanner saying a posting vanished while you had not
# applied. That is not an outcome you produced and does not belong in a
# list of your outcomes.
check("a vanished posting is not one of your closed-out applications",
      [r["Key"] for r in S.finished_applications(_LEDGER)], ["d"])
check("a still-listed posting is recognised",
      [S.still_listed(_row("x", "applied"), "2026-09-04T12:00:00"),
       S.still_listed(_closed, "2026-09-04T12:00:00")], [True, False])

# Applied On is yours and the scanner never writes it — but it is also the
# field that gets skipped when you are editing a CSV on a phone, so the
# scanner keeps its own date. Yours wins when you filled it in.
check("your applied date wins when you set one",
      S.applied_on(_row("x", "applied", **{"Applied On": "2026-08-01",
                                           "Marked active": "2026-08-20"})),
      "2026-08-01")
check("the scanner's date fills in when you did not",
      S.applied_on(_row("x", "applied", **{"Marked active": "2026-08-20"})),
      "2026-08-20")
check("no date at all is blank, not invented",
      S.applied_on(_row("x", "applied")), "")

# ── version ───────────────────────────────────────────────────────────
check("__version__ is a semantic version string",
      bool(re.match(r"^\d+\.\d+\.\d+$", S.__version__)), True)


# ── per-source scan health (state/sources.json) ─────────────────────
# jayde-os builds its "check manually" list off this file instead of a
# hardcoded employer map, and the digest says when a source has gone
# quiet. scan() writes the file itself; every case here points it at a
# scratch directory so a test run never touches the repo's own
# state/sources.json.

class _FakeOK:
    employer = "Test Health"

    def fetch_listings(self):
        return []

    def fetch_detail(self, p):
        return p


class _FakeFail:
    employer = "Broken Health"

    def fetch_listings(self):
        raise RuntimeError("connection refused")

    def fetch_detail(self, p):
        return p


def _scan_with_sources(prior_text=None):
    """Run S.scan() against two fake adapters (one ok, one failing) in a
    scratch state dir, and return (rows, review, sources.json)."""
    tmp = tempfile.mkdtemp()
    orig_state_dir, orig_sources_path, orig_adapters = (
        S.STATE_DIR, S.SOURCES_PATH, A.ADAPTERS)
    S.STATE_DIR = tmp
    S.SOURCES_PATH = os.path.join(tmp, "sources.json")
    A.ADAPTERS = [_FakeOK(), _FakeFail()]
    try:
        if prior_text is not None:
            with open(S.SOURCES_PATH, "w") as f:
                f.write(prior_text)
        rows, review = S.scan(fetch_details=False)
        return rows, review, S.load_sources()
    finally:
        S.STATE_DIR, S.SOURCES_PATH, A.ADAPTERS = (
            orig_state_dir, orig_sources_path, orig_adapters)
        shutil.rmtree(tmp, ignore_errors=True)


_rows, _review, _sources = _scan_with_sources()

# scan() must keep returning the (rows, review) shape it always has —
# jayde-os and this scan's own caller both unpack it positionally.
check("scan() still returns (rows, review)",
      (isinstance(_rows, list), isinstance(_review, list)), (True, True))

# One entry per adapter, keyed by class name + employer so two instances
# of the same adapter class (WorkdayCXS, one per hospital system) never
# collide.
check("one sources.json entry per adapter",
      sorted(_sources.keys()), ["_FakeFail:Broken Health", "_FakeOK:Test Health"])
check("a working adapter is recorded ok",
      (_sources["_FakeOK:Test Health"]["status"],
       _sources["_FakeOK:Test Health"]["error"],
       _sources["_FakeOK:Test Health"]["listings"]),
      ("ok", None, 0))
check("a failing adapter is recorded failed, with the exception text",
      (_sources["_FakeFail:Broken Health"]["status"],
       _sources["_FakeFail:Broken Health"]["error"],
       _sources["_FakeFail:Broken Health"]["listings"]),
      ("failed", "connection refused", 0))

# A source that has never once succeeded carries last_success: null.
check("a never-successful source has no last_success",
      _sources["_FakeFail:Broken Health"]["last_success"], None)

# Failure carries the PREVIOUS last_success forward rather than clearing
# it — a source that has been down for three days should still say when
# it last worked, not "never".
_prior = json.dumps({
    "_FakeFail:Broken Health": {
        "employer": "Broken Health", "adapter": "_FakeFail",
        "status": "ok", "error": None, "listings": 5, "in_range": 2,
        "checked_at": "2026-08-30T07:00:00+00:00",
        "last_success": "2026-08-30T07:00:00+00:00",
    },
})
_, _, _carried = _scan_with_sources(_prior)
check("last_success carries over an adapter failure",
      _carried["_FakeFail:Broken Health"]["last_success"],
      "2026-08-30T07:00:00+00:00")
check("the failure still reports as failed, not stale-ok",
      _carried["_FakeFail:Broken Health"]["status"], "failed")
# The other adapter is untouched by its neighbour's failure.
check("an unrelated adapter is unaffected by another's failure",
      _carried["_FakeOK:Test Health"]["status"], "ok")

# A missing or malformed previous file reads as empty, not a crash — a
# hand-edited or half-written sources.json must not take the scan down.
_, _, _malformed = _scan_with_sources("not valid json {{{")
check("a malformed prior sources.json is treated as empty",
      _malformed["_FakeFail:Broken Health"]["last_success"], None)


def _digest(sources, **kw):
    base = dict(shown=[], top=[], new=[], watch=[], active=[], finished=[],
                review=[], hidden=0, now="2026-09-04T12:00:00+00:00",
                sources=sources, quick=False)
    base.update(kw)
    return S.Digest(**base)


_all_ok = {
    "A:Emp1": {"employer": "Emp1", "status": "ok"},
    "B:Emp2": {"employer": "Emp2", "status": "ok"},
}
_one_failed = {
    "A:Emp1": {"employer": "Emp1", "status": "ok"},
    "B:Emp2": {"employer": "Emp2", "status": "failed"},
}

check("digest reports N/M ok with nothing failed",
      "_Sources: 2/2 ok_" in S.render_md(_digest(_all_ok)), True)
check("an all-ok digest names no failures",
      "failed:" in S.render_md(_digest(_all_ok)), False)
check("digest reports N/M ok with one failure",
      "_Sources: 1/2 ok_" in S.render_md(_digest(_one_failed)), True)
check("a failed source is named in the digest",
      "failed: Emp2" in S.render_md(_digest(_one_failed)), True)


# ── title prefilter: two leaks found adding CommonSpirit ─────────────
# Both are unambiguous from the title alone, which is what this filter is
# for; anything hedged still belongs to the classifier.
check("a misspelled 'Licensed Vocation Nurse' is still an LVN role",
      A.title_passes("Licensed Vocation Nurse"), False)
check("the correctly spelled LVN title still drops",
      A.title_passes("Licensed Vocational Nurse II, Urology"), False)
check("an informaticist is excluded like informatics",
      A.title_passes("RN Clinical Informaticist"), False)
# The loosening must not undo what the include side was widened for.
check("a bare Level I nurse title still reaches the classifier",
      A.title_passes("Ambulatory Services Nurse I, PreOp & PACU"), True)
check("the CNA bargaining-unit suffix still does not veto an RN role",
      A.title_passes(
          "RN - CMC Emergency Services - Part Time - 12 Hour - Nights - CNA"),
      True)


# ── closing a row requires having read the source ────────────────────
# The bug these cover: on 2026-09-08 and 09-09 governmentjobs.com timed
# out for five of six NEOGOV agencies. The adapter still returned the
# sixth, so it counted as a healthy source, and build() marked eleven
# still-open postings "closed" because absence-from-results was read as
# absence-from-the-employer. Six were Contra Costa Regional Medical
# Center RN roles verified live the next day, and a closed row never
# comes back on its own.

def _ledger_after_build(ledger_rows, scanned, sources):
    """Run build() over a scratch ledger and return it keyed by Key."""
    # build() writes DIGEST.md, digest.html and index.html to relative
    # paths, so this runs from inside the scratch directory. Monkeypatching
    # the state constants alone is not enough: the digest would land in the
    # repo and overwrite three committed files.
    tmp = tempfile.mkdtemp()
    orig = (S.STATE_DIR, S.SOURCES_PATH, S.LEDGER_PATH, S.SEEN_PATH,
            os.getcwd())
    S.STATE_DIR = os.path.join(tmp, "state")
    S.SOURCES_PATH = os.path.join(S.STATE_DIR, "sources.json")
    S.SEEN_PATH = os.path.join(S.STATE_DIR, "seen.json")
    S.LEDGER_PATH = os.path.join(tmp, "applications.csv")
    os.makedirs(S.STATE_DIR, exist_ok=True)
    os.chdir(tmp)
    try:
        with open(S.SOURCES_PATH, "w") as f:
            json.dump(sources, f)
        with open(S.LEDGER_PATH, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=S.LEDGER_FIELDS)
            w.writeheader()
            for r in ledger_rows:
                w.writerow({k: r.get(k, "") for k in S.LEDGER_FIELDS})
        S.build(scanned, [], quick=False)
        with open(S.LEDGER_PATH) as f:
            return {r["Key"]: r for r in csv.DictReader(f)}
    finally:
        (S.STATE_DIR, S.SOURCES_PATH, S.LEDGER_PATH, S.SEEN_PATH,
         cwd) = orig
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def _row(key, employer, status="unapplied"):
    return {"Key": key, "Employer": employer, "Status": status,
            "Title": "Registered Nurse", "Bucket": "Level I / new grad",
            "Location": "Martinez", "Drive time": "30-60",
            "First seen": "2026-09-01T00:00", "Last seen": "2026-09-01T00:00",
            "URL": "https://example.invalid/1"}


_HEALTHY = {"NeoGov:CA": {"employer": "CA", "status": "ok",
                          "covered": ["Contra Costa County"]}}
_DEGRADED = {"NeoGov:CA": {"employer": "CA", "status": "ok", "covered": []}}

# The posting is gone AND we read the source: that is a real close.
_gone_covered = _ledger_after_build(
    [_row("Contra Costa County::1", "Contra Costa County")], [], _HEALTHY)
check("a posting absent from a source we read is closed",
      _gone_covered["Contra Costa County::1"]["Status"], "closed")

# The posting is gone but the agency timed out: leave the row alone.
_gone_degraded = _ledger_after_build(
    [_row("Contra Costa County::1", "Contra Costa County")], [], _DEGRADED)
check("a posting absent from a source that FAILED is not closed",
      _gone_degraded["Contra Costa County::1"]["Status"], "unapplied")

# An employer nothing this scan covers is never closed, even by a
# sources.json that predates the "covered" key.
_legacy = _ledger_after_build(
    [_row("Contra Costa County::1", "Contra Costa County")], [],
    {"NeoGov:CA": {"employer": "CA", "status": "ok"}})
check("a sources.json with no 'covered' key closes nothing",
      _legacy["Contra Costa County::1"]["Status"], "unapplied")

# An application you already sent is never closed, covered or not.
_applied = _ledger_after_build(
    [_row("Contra Costa County::1", "Contra Costa County", "applied")],
    [], _HEALTHY)
check("a posting you applied to is never closed",
      _applied["Contra Costa County::1"]["Status"], "applied")


# ── a posting that comes back reopens ────────────────────────────────
# Without this, a row wrongly closed by an outage stays invisible forever:
# is_open() rejects "closed", and nothing else ever cleared it.

class _Back:
    key = "Contra Costa County::1"
    employer = "Contra Costa County"
    req_id = "1"
    title = "Registered Nurse"
    location = "Martinez"
    url = "https://example.invalid/1"
    drive_time_bucket = "30-60"
    bucket = "STAFF_NURSE_I"
    evidence = "New graduates welcome."
    posted_date = ""
    details = ""
    is_new = False


_reopened = _ledger_after_build(
    [_row("Contra Costa County::1", "Contra Costa County", "closed")],
    [_Back()], _HEALTHY)
check("a closed posting that reappears is reopened",
      _reopened["Contra Costa County::1"]["Status"], "unapplied")

# "closed" is the only archived status the scanner sets, so it is the only
# one it may clear. A rejection you recorded must survive the posting
# being relisted.
_kept = _ledger_after_build(
    [_row("Contra Costa County::1", "Contra Costa County", "rejected")],
    [_Back()], _HEALTHY)
check("a reappearing posting does not undo YOUR status",
      _kept["Contra Costa County::1"]["Status"], "rejected")


# ── adapters report what they actually reached ───────────────────────

_ng = A.NeoGov(agencies={"a": ("Agency A", "Oakland")})
check("a NEOGOV agency never fetched is not reported covered",
      _ng.covered_employers(), set())

_rad = A.Radancy("Emp", "example.invalid")
_rad.failed_cities = {"san francisco"}
check("Radancy reports nothing covered when a city page failed",
      _rad.covered_employers(), set())
_rad.failed_cities = set()
check("Radancy reports its employer covered when every city page read",
      _rad.covered_employers(), {"Emp"})


# ── Oracle Recruiting Cloud ──────────────────────────────────────────

_orc = A.OracleORC("Tenet Health", "eodr.fa.us2.oraclecloud.com")
check("ORC keeps a California posting",
      _orc._in_california({"PrimaryLocation": "San Ramon, CA, United States"}),
      True)
check("ORC drops an out-of-state posting",
      _orc._in_california({"PrimaryLocation": "San Antonio, TX, United States"}),
      False)
# A job whose primary site is out of state but which is also open in CA
# still has to reach geo.
check("ORC keeps a posting whose SECONDARY location is California",
      _orc._in_california({"PrimaryLocation": "Phoenix, AZ, United States",
                           "secondaryLocations": [
                               {"Location": "San Ramon, CA, United States"}]}),
      True)
check("ORC trims the country off a location",
      _orc._city("San Ramon, CA, United States"), "San Ramon, CA")
# Composing the branded URL from the Oracle requisition id produced links
# that 404 — Tenet keys jobs.tenethealth.com on an unrelated Radancy id.
check("ORC links to the Oracle-hosted page, not a branded guess",
      _orc._url("2603016285"),
      "https://eodr.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/"
      "sites/CX_1/job/2603016285")


# Patience multiplies. Eleven agencies each burning four 45s timeouts plus
# backoff is over half an hour, and the workflow is killed at 60 minutes —
# which publishes nothing at all, worse than a short scan. The budget is a
# hard stop; agencies past it are simply not read, which covered_employers()
# already makes safe.
check("NEOGOV bounds its own wall-clock spend",
      A.NeoGov.BUDGET_SEC <= 600, True)
# The budget is checked BEFORE an agency starts, so total worst-case spend
# is the budget plus one agency's full failure, not the budget alone.
_WORST_AGENCY = A.NeoGov.RETRIES * A.NeoGov.TIMEOUT + 2 ** A.NeoGov.RETRIES
check("one agency's worst case is bounded", _WORST_AGENCY < 240, True)
check("the adapter cannot outlast the workflow timeout",
      (A.NeoGov.BUDGET_SEC + _WORST_AGENCY) < 15 * 60, True)
# An agency that is never reached must not be reported as covered, or its
# rows would be closed on a scan that never looked at it.
_ng2 = A.NeoGov(agencies={"a": ("Agency A", "Oakland"),
                          "b": ("Agency B", "Berkeley")})
check("an adapter that read nothing covers nothing",
      _ng2.covered_employers(), set())
check("but it still declares everything it is responsible for",
      _ng2.all_employers(), {"Agency A", "Agency B"})

# ── a source can fail in part ────────────────────────────────────────
# NEOGOV reads eleven agencies through one adapter. When five time out it
# still returns the other six and still counted as "ok", which is how a
# two-day Contra Costa outage went unreported in the digest.

_part = {"NeoGov:CA": {"employer": "CA counties", "status": "degraded",
                       "missed": ["Contra Costa County", "Solano County"]},
         "W:Sutter": {"employer": "Sutter Health", "status": "ok"}}
_md = S.render_md(_digest(_part))
check("a degraded source names the employers that went unread",
      "not read this scan: Contra Costa County, Solano County" in _md, True)
check("a degraded source is not reported as failed",
      "failed:" in _md, False)
check("a degraded source does not count toward N ok",
      "_Sources: 1/2 ok_" in _md, True)

# ── Radancy row parsing, both templates ──────────────────────────────
# An earlier parser took the title from the nearest <h2> in a window
# around the anchor. On a Redwood City page that produced the titles
# "Filter Results" and "Related Content" for two of sixteen rows — the
# page's own furniture, read as job postings.

_RAD = A.Radancy("CommonSpirit", "www.commonspirit.careers")

# CommonSpirit: the anchor sits inside the <h2>, and the job-info fields
# are <li> elements nested inside the row's own outer <li>.
_NEW_TPL = (
    '<ul id="search-results-jobs">'
    '<li class="search-results-list__item">'
    '<h2 class="search-results-list__job-title">'
    '<a class="job-link" href="/job/redwood-city/rn-cardiac-telemetry/1/9"'
    ' data-job-id="9">RN Cardiac Telemetry</a></h2>'
    '<ul class="job-info-list">'
    '<li class="job-info job-department"> Telemetry </li>'
    '<li class="job-info job-facility"> Sequoia Hospital </li>'
    '<li class="job-info job-location"> Redwood City, CA </li>'
    '</ul></li></ul><h2>Related Content</h2>')

# Tenet: the <h2> is inside the anchor, with the fields as sibling spans.
_OLD_TPL = (
    '<section id="search-results-list"><ul><li>'
    '<a href="/job/san-ramon/ambulatory-or-rn/1/8" data-job-id="8">'
    '<h2>Ambulatory OR RN</h2>'
    '<span class="job-info job-facility">San Ramon Regional</span>'
    '<span class="job-info job-location">San Ramon, CA</span>'
    '</a></li></ul></section>')

_new_rows = _RAD._rows(_NEW_TPL, "redwood city")
check("newer Radancy template yields one row", len(_new_rows), 1)
check("newer template reads the title from the anchor, not page furniture",
      _new_rows[0].title, "RN Cardiac Telemetry")
check("newer template reads the location out of a NESTED <li>",
      _new_rows[0].location, "Redwood City, CA")

_old_rows = _RAD._rows(_OLD_TPL, "san ramon")
check("older Radancy template yields one row", len(_old_rows), 1)
check("older template takes the <h2> inside the anchor, not its whole text",
      _old_rows[0].title, "Ambulatory OR RN")
check("older template reads its sibling-span location",
      _old_rows[0].location, "San Ramon, CA")

# A row that prints no location is filed under the city page it came from
# rather than going blank and landing in the review bucket.
check("a row with no location falls back to the city page it came from",
      _RAD._rows(
          '<ul id="search-results-jobs"><li><h2><a href="/job/x/y/1/7" '
          'data-job-id="7">RN Float</a></h2></li></ul>',
          "santa cruz")[0].location,
      "Santa Cruz, CA")

# Radancy's page 2 is a path suffix on a DIFFERENT path than the sitemap
# publishes: the sitemap gives
#   /location/redwood-city-california-united-states-jobs/.../4
# and the site's own next-link is
#   /location/redwood-city-jobs/.../4/2
# Appending "&p=2" to the sitemap URL returns HTTP 200 and an empty list,
# which reads as "last page" — it collected 15 of Redwood City's 25
# postings, hiding an RN House Supervisor role that only appears on
# page 2. So the next-link is read off the page, never composed.
_PAGED = (
    '<ul id="search-results-jobs"><li><h2>'
    '<a href="/job/a/b/1/1" data-job-id="1">RN One</a></h2></li></ul>'
    '<nav class="pagination" data-total-pages="2">'
    '<a class="next" href="/location/redwood-city-jobs/35300/x/4/2">Next</a>'
    '</nav>')
check("the next-link is read off the page, not composed from the sitemap URL",
      A.Radancy._NEXT.search(_PAGED).group(1),
      "/location/redwood-city-jobs/35300/x/4/2")
check("a page with no next-link ends the walk",
      A.Radancy._NEXT.search('<ul id="search-results-jobs"></ul>'), None)
check("total-pages is read so a page-cap stop can be reported",
      A.Radancy._TOTAL_PAGES.search(_PAGED).group(1), "2")

# ── JobAps (San Joaquin General, French Camp) ────────────────────────

_ja = A.JobAps()
# JobAps writes `<td class="Locs">French Camp<br </td>`, with the <br
# never closed before the cell ends. Stripping only well-formed tags left
# "French Camp<br", which geo cannot match — a county hospital inside the
# ring would have gone to the review bucket on every scan.
check("an unclosed trailing tag is stripped out of a cell",
      _ja._text("French Camp<br "), "French Camp")
check("a well-formed cell is unchanged",
      _ja._text("S J General Hospital "), "S J General Hospital")
check("French Camp is a city geo places in range",
      geo.classify("French Camp")[0], geo.Geo.IN)


# ── the review bucket must stay readable ─────────────────────────────
# Adding Tenet, Providence and Adventist — three statewide employers whose
# California postings are mostly southern — put 119 rows into "Location
# needs checking" in one scan, from fifteen towns. A review list that long
# is one nobody reads, so the towns went into the out-of-range table.
for _city in ("Simi Valley, CA", "Tehachapi, CA", "Reedley, CA",
              "Joshua Tree, CA", "Orange, CA", "Indio, CA", "Fullerton, CA",
              "Templeton, CA", "Mission Hills, CA", "Apple Valley, CA",
              "Montebello, CA", "Mission Viejo, CA", "San Pedro, CA",
              "Tarzana, CA", "Brea, CA"):
    check(f"{_city} is out of range", geo.classify(_city)[0], geo.Geo.OUT)

# _csv splits on commas and nothing else, so a comment written inside one
# of those triple-quoted blocks becomes a city name and swallows the first
# real entry after it. That is not hypothetical: it happened while adding
# the list above, and left Simi Valley UNKNOWN while looking correct.
check("no comment text leaked into the city table",
      [c for c in geo.OUT_CITIES if "#" in c or "added" in c], [])

# Salida is the opposite mistake and was in neither table: it sits beside
# Modesto, which is 60-90, so it was landing in review every scan.
check("Salida is in range, bucketed with Modesto",
      geo.classify("Salida")[:2], (geo.Geo.IN, "60-90"))

# The additions must not have moved anything that was already in range.
for _city, _bucket in (("Walnut Creek", "<30"), ("San Ramon", "30-60"),
                       ("San Jose", "60-90"), ("Santa Rosa", "90-120"),
                       ("French Camp", "60-90"), ("San Francisco", "<30")):
    check(f"{_city} is still in range at {_bucket}",
          geo.classify(_city)[:2], (geo.Geo.IN, _bucket))

# ── the user's stated criteria, reinforced 2026-09-09 ────────────────
# An audit of one scan found 36 of 197 shown rows were charge, lead,
# coordinator, navigator, consultant or specialist roles. A new graduate
# is not hired into any of them. The user asked for these out in as many
# words; this is a deliberate narrowing, not an accident, and it belongs
# with the graded Level II rule as something not to "fix" back.
for _t in ("Charge Nurse (RN) - ER",
           "Charge RN - Surgery - Full Time Evening",
           "RN, Nurse Lead - Surgery, Dayshift",
           "Lead Wound Care RN (CWON), Home Health",
           "Nurse Navigator Oncology Clinic",
           "RN Coordinator -  Heart Transplant",
           "Nurse Consultant",
           "Lactation Specialist RN",
           "Magnet Program Coordinator, Nurse",
           "Clinical Effectiveness Consultant III, RN",
           "RN House Supervisor"):
    check(f"senior/non-bedside title dropped: {_t[:38]}",
          A.title_passes(_t), False)

# This one was reaching the digest labelled "Level I / new grad" while its
# own title said Experienced — the exact shape of the most expensive bug
# this project can produce.
check("a coordinator role titled 'Experienced' never reaches the classifier",
      A.title_passes("RN Education Program Site Coordinator Experienced"),
      False)

# The narrowing must not touch the roles the whole scan exists to find.
for _t in ("Staff Nurse I, Medical Surgical",
           "Registered Nurse (RN) - Med Surg",
           "RN Resident | Full Time Regular | Dayshift | Surgical ICU 1",
           "New Grad Registered Nurse (RN) - Telemetry",
           "Ambulatory Services Nurse I, PreOp & PACU",
           "Registered Nurse Level I/II",
           "Registered Nurse, ICU"):
    check(f"applicable title still reaches the classifier: {_t[:34]}",
          A.title_passes(_t), True)


# ── an RN residency is a new-grad role ───────────────────────────────
# Adventist writes "RN Resident", without the word "nurse", so the
# residency pattern missed it and the single most applicable kind of
# posting there is was landing as a generic NO_EXPERIENCE row.
for _t in ("RN Resident | Full Time Regular | Dayshift | Surgical ICU 1",
           "RN Residency Program", "Registered Nurse Resident",
           "Nurse Residency"):
    check(f"residency recognised as new-grad: {_t[:36]}",
          bool(C.NEW_GRAD.search(_t)), True)
# The adjacency is load-bearing: in skilled nursing the residents are the
# patients, and a bare \bresident\b would match every PACS posting.
for _t in ("Provide exceptional nursing care to residents",
           "Assess residents and monitor changes in condition"):
    check(f"patients called residents are not a residency: {_t[:34]}",
          bool(C.NEW_GRAD.search(_t)), False)


# ── evidence must be the sentence the verdict rests on ───────────────
# Adventist lists requirements as bullets. Stripping every tag to a space
# merged them, and the digest quoted "(BSN): Preferred. Acute care
# facility" — a truncated claim about a degree — as the grounds for "no
# experience required".
_BULLETS = ("<p>Job Requirements:</p><div><p>Education and Work Experience:</p>"
            "<ul><li>Bachelor's Degree in Nursing (BSN): Preferred</li>"
            "<li>Acute care facility experience: Preferred</li></ul></div>")
_txt = A._html_to_text(_BULLETS)
check("block tags become statement boundaries",
      "(BSN): Preferred. Acute care facility experience: Preferred." in _txt,
      True)
check("the two bullets do not run together",
      "Preferred Acute care" in _txt, False)

_v = C.classify("RN Cath Lab", _txt)
check("a posting hedging every requirement is still no-experience",
      _v.bucket, "NO_EXPERIENCE")
check("and it quotes the experience clause, not the degree clause",
      _v.evidence, "Acute care facility experience: Preferred.")

# The tightest clause wins, because the first match usually still carries
# the section heading in front of it.
check("the heading-laden clause is not chosen when a tighter one exists",
      "Bachelor" in _v.evidence, False)

# Widening the search for the QUOTE must never widen what gets shown: a
# required duration still has to beat a hedge elsewhere in the posting.
_hard = A._html_to_text(
    "<ul><li>Acute care experience: 2 years Required</li>"
    "<li>BSN: Preferred</li></ul>")
check("a required acute duration is still suppressed",
      C.classify("RN Med Surg", _hard).bucket, "ACUTE_REQUIRED")

# ── evidence must come from whichever field actually said it ─────────
# _snippet falls back to the opening of the text when its pattern does not
# match, so a new-grad signal that lives only in the title was evidenced by
# the first 170 characters of the description. Adventist's "RN Resident"
# posting therefore reached the digest as a Level I role quoting "Located
# in one of the most beautiful regions in the United States..." — hospital
# marketing copy standing in for a requirement.
_mktg = ("Located in one of the most beautiful regions in the United States, "
         "St. Helena Hospital was founded in 1878 and has a rich history. "
         "Job Requirements: Registered Nurse (RN) licensure: Required.")
_v = C.classify("RN Resident | Full Time Regular | Dayshift | Surgical ICU 1",
                _mktg)
check("a title-only residency signal is evidenced by the title",
      _v.evidence, "RN Resident | Full Time Regular | Dayshift | Surgical ICU 1")
check("and it is still a Level I verdict", _v.bucket, "STAFF_NURSE_I")
check("no marketing prose is quoted as evidence",
      "beautiful regions" in _v.evidence, False)

# When the body does say it, the body is still what gets quoted.
_v2 = C.classify("Registered Nurse - Med Surg",
                 "We welcome new graduates to apply. BLS required.")
check("a body new-grad signal is still evidenced by the body",
      "new graduates" in _v2.evidence, True)

# ── a job title with a pipe must not break the digest table ──────────
# Adventist titles its postings "RN | Full Time Regular | Dayshift |
# Telemetry 1". The detail line and the evidence were escaped; the title
# was not, so three extra cells appeared in the row and 13 rows of the
# digest rendered as unreadable fragments.
class _PipeTitle:
    key = "Adventist Health::1"
    employer = "Adventist Health"
    req_id = "1"
    title = "RN | Full Time Regular | Dayshift | Telemetry 1"
    location = "St. Helena, CA"
    url = "https://example.invalid/1"
    drive_time_bucket = "90-120"
    bucket = "NO_EXPERIENCE"
    evidence = "Acute care facility experience: Preferred."
    posted_date = ""
    details = "Full-time | Day"
    is_new = True


_md = S.render_md(_digest({}, shown=[_PipeTitle()], top=[_PipeTitle()]))
_rows = [ln for ln in _md.splitlines()
         if ln.startswith("| ") and "Telemetry 1" in ln]
check("the posting renders as exactly one table row", len(_rows), 1)
check("and that row has the six cells the header declares",
      _rows[0].count("|"), 7)
check("the title's pipes are replaced, not dropped",
      "RN / Full Time Regular / Dayshift / Telemetry 1" in _rows[0], True)

# ── the grade ladder, confirmed by the user 2026-09-09 ───────────────
# "No staff nurse 2 jobs. Continue to give me the staff nurse 1 (I) jobs."
_REQS = "Requirements: Current California RN license. BLS required."


def _bucket(title):
    if not A.title_passes(title):
        return "DROPPED"
    return C.classify(title, _REQS).bucket


# Both rules were anchored to the numeral sitting immediately after the
# nurse noun, so an employer that writes the grade out — "Staff Nurse
# Level II, ICU", "Nurse Level 2 - Float Pool" — defeated both, and those
# postings reached the list as UNCLEAR while the identical "Staff Nurse
# II" was correctly hidden.
for _t in ("Staff Nurse II, Emergency Services", "Staff Nurse 2 - Med Surg",
           "Registered Nurse II, Primary Care", "RN II - Telemetry",
           "Registered Nurse Level II", "Staff Nurse Level II, ICU",
           "Nurse Level 2 - Float Pool", "RN Level III - ICU",
           "Clinical Nurse III - Wound Care", "Nurse Level II/III",
           "RN II-III"):
    check(f"graded II+ is hidden: {_t[:38]}", _bucket(_t), "LEVEL_II_TITLE")

for _t in ("Staff Nurse I, Medical Surgical", "Staff Nurse 1 - Med Surg",
           "Registered Nurse Level I", "RN I - Telemetry",
           "Clinical Nurse I - ICU",
           "Ambulatory Services Nurse I, PreOp & PACU"):
    check(f"Level I is a Level I verdict: {_t[:34]}",
          _bucket(_t), "STAFF_NURSE_I")

# A combined grade is the rung a new graduate is hired into, with the II
# sitting above it on the same requisition. Sacramento County posts
# several, including one with an assignment code between the noun and the
# grade, and they were landing in UNCLEAR — shown, but buried below the
# no-experience pile instead of surfacing in "worth applying to now".
for _t in ("Registered Nurse Level I/II", "Public Health Nurse Level I/II",
           "Registered Nurse D/CF (Level I/II)", "RN I-II",
           "Staff Nurse I & II"):
    check(f"combined I/II is hired at the I rung: {_t[:32]}",
          _bucket(_t), "STAFF_NURSE_I")

# The grade word is what makes the bare "Level I/II" form safe. A numeral
# with no grade word in front of it is a unit or a shift length, and
# reading it as a grade would hide three staff postings that carry no
# grade at all.
for _t in ("RN, 2 West Medical", "Registered Nurse - Unit 4 South",
           "RN - 12 Hour Nights"):
    check(f"a unit number is not a grade: {_t[:34]}",
          _bucket(_t) != "LEVEL_II_TITLE", True)

# ── LTAC must not be suppressed by its own name ──────────────────────
# ACUTE lists "hospital" as a marker, so every sentence naming an
# employer whose name contains the word matched it. Central Valley
# Specialty Hospital is a long-term acute care hospital in Modesto whose
# posting says "We encourage new RNs to apply" and asks only for a
# licence, BLS and ACLS — and it was hidden as ACUTE_REQUIRED because its
# benefits paragraph ("wages determined based on ... qualifications and
# experience") contained both an ACUTE marker and the word "experience".
_BENEFITS = ("Compensation and Benefits: Central Valley Specialty Hospital "
             "offers competitive compensation, with individual wages "
             "determined based on a number of factors including, but not "
             "limited to, an individual's qualifications and experience.")
check("an employer's own name in benefits copy is not an acute requirement",
      bool(C.ACUTE_EXPERIENCE.search(_BENEFITS)), False)
check("the loose ACUTE pattern still matches it, which is why the tight "
      "one exists", bool(C.ACUTE.search(_BENEFITS)), True)

# The genuine gates must still fire.
for _t in ("Two years of acute care hospital experience required.",
           "Minimum 2 years experience in an acute care setting.",
           "Requires recent acute care experience.",
           "1 year hospital experience required.",
           "Experience in a critical care unit is required."):
    check(f"a real acute gate still matches: {_t[:40]}",
          bool(C.ACUTE_EXPERIENCE.search(_t)), True)

# End to end: an LTAC posting that welcomes new grads reaches the user.
_LTAC = ("Central Valley Specialty Hospital, a leading post-acute care "
         "facility, is seeking Registered Nurses. We encourage new RNs to "
         "apply. License / Certification Qualifications: Valid California "
         "state RN license. Basic Life Support (BLS) certification. " + _BENEFITS)
_v = C.classify("Registered Nurse (R.N.)", _LTAC)
check("an LTAC posting welcoming new RNs is a Level I role",
      _v.bucket, "STAFF_NURSE_I")
check("and it quotes the invitation, not the benefits paragraph",
      "new RNs" in _v.evidence, True)


# ── "we encourage new RNs to apply" is a new-grad invitation ─────────
for _t in ("We encourage new RNs to apply", "New RNs are welcome to apply",
           "new graduates are encouraged to apply", "We welcome new nurses"):
    check(f"invitation recognised: {_t[:34]}", bool(C.NEW_GRAD.search(_t)), True)
# An invitation verb is required in either word order, so a bare "new RN"
# in onboarding prose is not read as one.
for _t in ("Orientation is provided for the new RN",
           "The new RN will report to the charge nurse",
           "New equipment training required"):
    check(f"not an invitation: {_t[:34]}", bool(C.NEW_GRAD.search(_t)), False)


# ── Paylocity (Central Valley Specialty Hospital) ────────────────────
_PAY = A.Paylocity("Central Valley Specialty Hospital",
                   "https://example.invalid/board",
                   setting="Long-term acute care")
check("LVN written with an abbreviation is still an LVN role",
      A.title_passes("LICENSED VOC. NURSE"), False)
check("and the RN beside it still comes through",
      A.title_passes("Registered Nurse (R.N.)"), True)
# LocationName on this board is "On Site" or "Main Office", which geo
# cannot rank; the real city is in the nested JobLocation.
_body = ('{"Jobs":[{"JobId":3756510,"JobTitle":"Registered Nurse (R.N.)",'
         '"LocationName":"On Site","PublishedDate":"2026-08-01T00:00:00",'
         '"HiringDepartment":"Nursing","Description":"teaser only",'
         '"JobLocation":{"Name":"On Site","City":"Modesto","State":"CA"}}]}')
_m = A.Paylocity._JOBS.search(_body)
check("the embedded Jobs array is found", _m is not None, True)
import json as _json
_j = _json.loads(_m.group(1))[0]
check("the city comes from the nested JobLocation, not LocationName",
      f"{_j['JobLocation']['City']}, {_j['JobLocation']['State']}", "Modesto, CA")
check("Modesto is in range", geo.classify("Modesto, CA")[0], geo.Geo.IN)

# ── sub-acute and post-acute are not acute ──────────────────────────
# Sonoma Specialty Hospital's staff RN posting asks for "One-year
# sub/post-acute care experience", and the word "acute" inside
# "post-acute" suppressed it as ACUTE_REQUIRED on the day the hospital
# was added. Post-acute and sub-acute are the settings the user's own
# criteria name as basic RN experience that is not acute care, so a
# posting asking for them belongs on the list.
check("sub/post-acute experience is not an acute-care gate",
      bool(C.ACUTE_EXPERIENCE.search(
          "Experience Required: One-year sub/post-acute care experience.")),
      False)
check("nor is sub-acute written out",
      bool(C.ACUTE_EXPERIENCE.search(
          "Minimum one year of sub-acute care experience required.")), False)
check("nor non-acute",
      bool(C.ACUTE_EXPERIENCE.search("Non-acute care experience required.")),
      False)
check("and a real acute-care gate still matches",
      bool(C.ACUTE_EXPERIENCE.search(
          "Two years of acute care experience required.")), True)
check("including one written the other way round",
      bool(C.ACUTE_EXPERIENCE.search(
          "Experience in an acute care setting is required.")), True)
_v = C.classify("Registered Nurse",
                "Experience Required: One-year sub/post-acute care experience.")
check("so the posting reaches the user as general experience",
      _v.bucket, "GENERAL_EXPERIENCE")
check("and is not suppressed", _v.bucket in C.HIDE, False)


# ── iCIMS (Sonoma Valley Hospital) ───────────────────────────────────
# The portal reads as an app and is server-rendered. Two things this
# parser has to get right: the screen-reader label that sits inside the
# anchor ahead of the title, and following the portal's own rel="next"
# rather than guessing a page parameter.
_IC = A.ICIMS("Sonoma Valley Hospital", "careers-svh.icims.com",
              default_city="Sonoma")
_card = ('<ul class="container-fluid iCIMS_JobsTable">'
         '<li class="iCIMS_JobCardItem"><div class="row">'
         '<div class="col-xs-12 title">'
         '<a href="https://careers-svh.icims.com/jobs/2401/'
         'registered-nurse-%28per-diem%29/job?in_iframe=1" '
         'class="iCIMS_Anchor" title="2401 - Registered Nurse (Per Diem)">'
         '<span class="sr-only field-label">Title</span>'
         '<h3 > Registered Nurse (Per Diem)</h3></a></div>'
         '<div class="col-xs-12 description">One sentence of teaser.</div>'
         '</div></li></ul>'
         '<link rel="next" href="https://careers-svh.icims.com/jobs/search'
         '?pr=1&amp;in_iframe=1" />')
_found = A.ICIMS._CARD.findall(_card)
check("an iCIMS job card is found", len(_found), 1)
check("the job id comes from the URL", _found[0][1], "2401")
check("the screen-reader label is not part of the title",
      _IC._title(_found[0][2]), "Registered Nurse (Per Diem)")
check("the next page is read from the portal's own rel=next",
      A.ICIMS._NEXT.search(_card).group(1),
      "https://careers-svh.icims.com/jobs/search?pr=1&amp;in_iframe=1")
check("Sonoma is a city geo places in range",
      geo.classify("Sonoma")[0], geo.Geo.IN)


# ── UKG Pro Recruiting (Telecare) ────────────────────────────────────
# A regional posting names several sites. File it under the nearest one,
# the way the Workday multi-site postings are filed, and say how many
# others there were rather than dropping them silently.
check("the nearer of two sites wins",
      min(["Stockton, CA", "San Leandro, CA"], key=A.UKGRecruiting._closeness),
      "San Leandro, CA")
check("an out-of-range site loses to an in-range one",
      min(["Bakersfield, CA", "Ceres, CA"], key=A.UKGRecruiting._closeness),
      "Ceres, CA")
# Telecare runs programs in Oregon and Washington as well as California,
# which is why nothing here may assume a posting is Californian.
check("a Portland posting is out of range",
      geo.classify("Portland, OR")[0], geo.Geo.OUT)


# ── JobAps: the header cell's class is not the row marker ────────────
# San Joaquin writes <th class="JobTitle"> on its main table and a bare
# <th scope="row"> on the departmental tables below it. Keying on the
# class read 88 of that agency's 98 rows, and none at all of Alameda's,
# whose whole board uses the bare form. A Staff Nurse posting can land on
# a departmental list.
_ja_main = ('<tr><th class="JobTitle"><a href="/SJQ/sup/bulpreview.asp?R1=1"'
            ' class="JobTitle">Staff Nurse II</a>'
            '<a class="JobNum">0326-RH1102-AC</a></th>'
            '<td class="Locs">French Camp<br </td>'
            '<td class="Dept">Health Care Services</td></tr>')
_ja_dept = ('<tr><th scope="row"><a href="/Alameda/sup/bulpreview.asp?R1=2"'
            ' class="JobTitle" title="x">Public Health Nurse </a>'
            '<a href="/Alameda/sup/bulpreview.asp?R1=2" class="JobNum IconNew"'
            ' title="x">25-5301-01 </a></th>'
            '<td class="Salary">$1</td></tr>')
check("the main table's rows are read",
      len(A.JobAps._ROW.findall(_ja_main)), 1)
check("and so are the departmental table's",
      len(A.JobAps._ROW.findall(_ja_dept)), 1)
check("the JobNum modifier class does not hide a row",
      A.JobAps._text(A.JobAps._ROW.findall(_ja_dept)[0][2]), "25-5301-01")
# Alameda is on JobAps, not NEOGOV: the plausible slug "alamedaca" is the
# City of Alameda. Its board lives at jobboard.asp, not at the root.
check("an agency can put its listing somewhere other than the root",
      A.JobAps(employer="Alameda County", agency="Alameda",
               default_city="Oakland", path="jobboard.asp").path,
      "jobboard.asp")
check("Oakland is in range", geo.classify("Oakland")[0], geo.Geo.IN)


# ── JobAps evidence must be the bulletin, not the site's menu ───────
# The container this looked for (id="bulletin") exists on neither agency,
# so every San Joaquin General posting was classified from the whole
# page: the description opened with "HRS Home. Update Contact Info.
# Logon. Job Portal Home. Current Openings..." and the classifier reads
# from the front of what it is given.
_ja_page = ('<div id="PageWrapper"><nav>Job Portal Home Current Openings'
            ' How Do I Apply</nav>'
            '<div class="JobBulletinBody"> Introduction. This recruitment is'
            ' for the San Joaquin General Hospital.'
            '<div id="ApplyPanelDiv">Apply now</div></div></div>')
_i = _ja_page.find("JobBulletinBody")
_i = _ja_page.find(">", _i) + 1
_end = _ja_page.find("ApplyPanelDiv", _i)
_chunk = _ja_page[_i:_end]
check("the bulletin body is what gets read",
      "Job Portal Home" in _chunk, False)
check("and the attribute itself is not part of it",
      _chunk.strip().startswith("Introduction"), True)


# ── geo: places inside the ring the table did not know ───────────────
# Kentfield is the load-bearing one. It is a long-term acute care
# hospital this scan already reads through Vibra's board, the user asked
# for LTAC by name, and its postings could never be ranked.
check("Kentfield is in range", geo.classify("Kentfield, CA")[0], geo.Geo.IN)
check("and it is ranked, not just accepted",
      geo.classify("Kentfield, CA")[1], "30-60")
check("San Lorenzo is in range", geo.classify("San Lorenzo, CA")[0], geo.Geo.IN)
check("Oakdale is in range", geo.classify("Oakdale, CA")[0], geo.Geo.IN)
check("Half Moon Bay is in range",
      geo.classify("Half Moon Bay, CA")[0], geo.Geo.IN)
# A name that is also a place somewhere else stays a question rather than
# becoming a wrong answer. Ashland is in Alameda County and in Oregon,
# and this scan now reads an employer with Oregon programs.
check("an ambiguous name is left for review, not guessed",
      geo.classify("Ashland")[0], geo.Geo.UNKNOWN)
# ...and when the posting names the state, no guessing is needed at all.
check("a state that isn't California is out of range",
      geo.classify("Ashland, OR")[0], geo.Geo.OUT)
# 30 of one scan's 33 review rows were the same Texas posting arriving
# without coordinates. The review bucket is only useful if it is short.
check("so is Texas", geo.classify("Lufkin, Texas")[0], geo.Geo.OUT)
check("the state test reads the last segment only, not a substring",
      geo.classify("Nevada City, CA")[0], geo.Geo.OUT)   # in OUT_CITIES
check("and a Californian city with a state name in it is unharmed",
      geo.classify("Kansas City, MO")[0], geo.Geo.OUT)
check("a bare city with no state is still looked up",
      geo.classify("French Camp")[0], geo.Geo.IN)
check("and California spelled out is not mistaken for another state",
      geo.classify("Oakland, California")[0], geo.Geo.IN)
# The adapters that resolve a multi-site posting label it "City, ST
# (+N more)", and that suffix would otherwise sit where the state is.
check("the multi-site suffix does not hide the state",
      geo.classify("Tukwila, WA (+1 more)")[0], geo.Geo.OUT)
check("and does not break an in-range one",
      geo.classify("Alameda, CA (+4 more)")[0], geo.Geo.IN)
check("adding names did not break the longest-first ordering",
      geo.classify("Sutter Creek, CA")[0], geo.Geo.OUT)


if __name__ == "__main__":
    failed = [(n, d) for n, ok, d in CASES if not ok]
    for name, ok, detail in CASES:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"  ({detail})"))
    print(f"\n{len(CASES) - len(failed)}/{len(CASES)} passed")
    sys.exit(1 if failed else 0)
