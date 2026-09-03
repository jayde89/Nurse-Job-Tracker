"""
Regression tests for the title filter, the geo table and the classifier.

Every case here is a bug that actually shipped and cost real postings.
Run before pushing a rule change:  python3 test_rules.py

No test framework on purpose — this runs anywhere Python does, including
inside the Actions container, with nothing to install.
"""

import sys

import adapters as A
import classifier as C
import geo


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


if __name__ == "__main__":
    failed = [(n, d) for n, ok, d in CASES if not ok]
    for name, ok, detail in CASES:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"  ({detail})"))
    print(f"\n{len(CASES) - len(failed)}/{len(CASES)} passed")
    sys.exit(1 if failed else 0)
