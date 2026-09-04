"""
Regression tests for the title filter, the geo table, the classifier, the
front-of-list detail line and the application ledger.

Every case here is a bug that actually shipped and cost real postings.
Run before pushing a rule change:  python3 test_rules.py

No test framework on purpose — this runs anywhere Python does, including
inside the Actions container, with nothing to install.
"""

import sys

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


if __name__ == "__main__":
    failed = [(n, d) for n, ok, d in CASES if not ok]
    for name, ok, detail in CASES:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"  ({detail})"))
    print(f"\n{len(CASES) - len(failed)}/{len(CASES)} passed")
    sys.exit(1 if failed else 0)
