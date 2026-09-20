"""
The page she reads on her phone.

Published to GitHub Pages by every scan, so it is never older than the
last run. That is the whole point: the good interface used to be a file
on her Mac, generated from a frozen snapshot, and it went stale the moment
she walked away from the desk.

Why the tier is computed HERE and not downstream
------------------------------------------------
Reading order — "apply to this today" vs "not yet" — is the judgement the
page exists to deliver. It was computed once by a throwaway script and
then guessed at from the ledger's `Bucket` column, which does not carry
enough information: tried against real data that guess agreed only 264
times in 419, promoting a Nurse Midwife and an RN Float Pool (Leadership)
role into "apply now" while hiding nine ED jobs she is eligible for.

At this point in the scan the Posting object is still in scope, with its
bucket, its evidence sentence and its setting. Nothing has been thrown
away yet, so the judgement is cheap and correct. Anything that needs the
tier later reads it from the ledger's Tier column, which this fills.

Self-contained output: no CDN, no web font, no framework, no build step.
It is opened on a phone on hospital wifi, and it is installable to the
home screen, so it must work as one file.
"""
from __future__ import annotations

import html
import re

# Tier order is reading order. A/B/C she can act on now; D/E/F are real
# jobs she is not yet eligible for, kept behind a toggle rather than
# dropped — "why did that job vanish" costs more than a hidden section.
TIER_HEADING = {
    "A_open":       "You meet the requirement",
    "A_newgrad":    "Open to new grads",
    "A_pref":       "Acute preferred, not required",
    "A_spec_entry": "Entry grade, specialty unit",
    "C_unclear":    "Unclear — worth a look",
    "B_soon":       "Reachable soon (1 yr RN in 2027)",
    "B_bridge":     "Skilled nursing — lateral, but it counts",
    "D_specialty":  "Specialty unit, wants experience",
    "E_acute_req":  "Acute experience required",
    "F_tenure":     "Wants multiple years",
}
TIER_ORDER = list(TIER_HEADING)
TIER_RANK = {t: i for i, t in enumerate(TIER_ORDER)}
ELIGIBLE = {"A_open", "A_newgrad", "A_pref", "A_spec_entry", "C_unclear",
            "B_soon", "B_bridge"}
DRIVE_RANK = {"<30": 0, "30-60": 1, "60-90": 2, "90-120": 3}

# A posting that says in so many words that it wants no experience. The
# top tier is a promise — "you already meet this" — so it is only earned
# by the posting's own sentence, never by a bucket label alone.
_NO_BAR = re.compile(
    r"no (?:prior |previous )?(?:experience|rn experience)[^.]{0,40}"
    r"(?:required|necessary)"
    r"|experience:?\s*(?:none|not required)"
    r"|new grad(?:uate)?s? (?:are )?(?:welcome|encouraged|accepted)"
    r"|open to new grad", re.I)

# Named specialty units. An entry grade in one of these is a training
# role, not an open door; it reads differently and sorts lower.
_SPECIALTY = re.compile(
    r"\bicu\b|intensive care|critical care|\bccu\b|\bcvicu\b|\bsicu\b"
    r"|emergency|trauma|\bed\b|\ber\b|l&d|labor and delivery|\bnicu\b"
    r"|\bpicu\b|pediatric|oncolog|cath lab|operating room|periop|\bpacu\b"
    r"|endoscopy|dialysis|infusion|telemetry|step.?down|\bpcu\b|psych"
    r"|wound", re.I)

# How much experience the posting asks for, in months.
#
# This is the difference between "needs one year, she has it in May 2027"
# and "needs five years, which is not this job hunt" — and it is the one
# thing the Bucket column cannot carry. Both land in
# "Experience required, not acute", so a tier derived from the bucket
# alone put 22 multi-year postings into her reachable-soon list.
_YEARS = re.compile(
    r"(?:(\d+)\s*(?:\+|plus)?\s*(?:-|to|\u2013)?\s*(\d+)?)\s*"
    r"(year|yr|month|mo)", re.I)
_WORD_YEARS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
               "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_WORD_RE = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten)\s*"
    r"(?:\(\d+\)\s*)?(year|yr|month|mo)", re.I)


def required_months(evidence: str) -> int | None:
    """
    Smallest experience requirement the evidence states, in months.

    Returns None when the posting only *prefers* experience. "Prefer two
    years pre/post-op" is not a bar — treating it as one moved a job she
    can apply to today into "wants multiple years", which is the opposite
    of helping.
    """
    text = _strip_non_requirements(evidence or "")
    if _PREFERRED_ONLY.search(text) and not _states_a_hard_requirement(text):
        return None
    found = []
    for m in _YEARS.finditer(text):
        lo = m.group(1)
        if not lo:
            continue
        n = int(lo)
        found.append(n if m.group(3).lower().startswith("mo") else n * 12)
    for m in _WORD_RE.finditer(text):
        n = _WORD_YEARS[m.group(1).lower()]
        found.append(n if m.group(2).lower().startswith("mo") else n * 12)
    # The smallest number wins: a posting saying "1 year required, 3
    # preferred" is open to her at one year, and reading the larger figure
    # would hide a job she can take.
    return min(found) if found else None


# Durations that are not experience requirements. "Six months from hire to
# obtain ACLS" is a deadline the employer gives *her*, and reading it as a
# requirement hid a job whose only real bar was a certificate she can sit
# for. Likewise a grace period or a probationary term.
#
# Evidence sentences are often clipped mid-flight by the scanner, so the
# terminator has to be "a period or the end of the text" — requiring a
# period made this silently do nothing on exactly the rows that matter.
_NOT_A_REQUIREMENT = re.compile(
    r"[^.]*?\b(?:from|within|after|of)\s+(?:date of\s+)?hire[^.]*(?:\.|$)"
    r"|[^.]*?\bto obtain\b[^.]*(?:\.|$)"
    r"|[^.]*?\bprobation(?:ary)?\b[^.]*(?:\.|$)"
    r"|[^.]*?\beligible lists?\b[^.]*(?:\.|$)", re.I)


def _strip_non_requirements(text: str) -> str:
    return _NOT_A_REQUIREMENT.sub(" ", text or "")


# "Preferred" is not a requirement, and the distinction is the entire
# premise of this search — most postings she can actually get say the
# experience is preferred.
_PREFERRED_ONLY = re.compile(r"prefer", re.I)
# A negated requirement is the opposite of a requirement. "Preferred, not
# required" contains the word "required" and must not read as one — that
# sentence is the single most common way a posting says she qualifies.
_NEGATED = re.compile(r"\bnot\s+(?:strictly\s+|necessarily\s+)?"
                      r"(?:required|necessary|mandatory)\b", re.I)
_HARD_REQUIRED = re.compile(r"\brequire|\bmust have|\bminimum\b", re.I)


def _states_a_hard_requirement(text: str) -> bool:
    return bool(_HARD_REQUIRED.search(_NEGATED.sub(" ", text or "")))


# She reaches one year of RN experience in May 2027. A posting wanting
# meaningfully more than that is not reachable soon, whatever its bucket.
_REACHABLE_MONTHS = 12

# Experience she cannot accrue where she works. A year of general nursing
# arrives on its own in May 2027; a year of ICU does not, because the
# subacute floor is not an ICU. The classifier's ACUTE_REQUIRED bucket
# catches most of these, but a Level I title with an acute requirement in
# the body slips through as "open to new grads".
_ACUTE_UNIT = re.compile(
    r"\bicu\b|intensive care|critical care|\bccu\b|\bcvicu\b|\bsicu\b"
    r"|\bpicu\b|\bnicu\b|emergency (?:department|room|nursing|experience)"
    r"|\bed\b experience|telemetry experience|step.?down experience"
    r"|acute care (?:hospital )?experience|hospital experience", re.I)


def _acute_unit_experience(evidence: str) -> bool:
    return bool(_ACUTE_UNIT.search(evidence or ""))


# Experience *in a named unit*, as opposed to nursing in general. The
# distinction the size of the number cannot make: "six months of OR RN
# experience" is smaller than a year and still a door she cannot open,
# while "12 months of general nursing" is larger and opens by itself in
# May 2027.
#
# Matched only next to an experience word, so a job TITLED "Infusion RN"
# whose posting asks for general nursing is not demoted by its own name.
_SPECIALTY_EXP = re.compile(
    r"(?:\bicu\b|intensive care|critical care|\bccu\b|\bcvicu\b|\bsicu\b"
    r"|\bpicu\b|\bnicu\b|\bor\b|operating room|periop|\bpacu\b|cath ?lab"
    r"|emergency|trauma|l&d|labor and delivery|telemetry|step.?down"
    r"|\bpcu\b|dialysis|infusion|oncolog|hematolog|endoscop|psych"
    r"|acute care|hospital)"
    r"[^.]{0,40}?\bexperience\b"
    r"|\bexperience\b[^.]{0,30}?"
    r"(?:\bicu\b|intensive care|critical care|operating room|\bor\b rn"
    r"|emergency department|acute care)", re.I)


def _specialty_experience(evidence: str) -> bool:
    """Does the posting want experience in a unit she does not work in?"""
    text = _strip_non_requirements(evidence or "")
    # "Preferred" is not a bar, here as everywhere else.
    if _PREFERRED_ONLY.search(text) and not _states_a_hard_requirement(text):
        return False
    # Subacute and post-acute are the floor she works on now. A posting
    # asking for "one year sub/post-acute experience" is asking for what
    # she already has — it is the closest thing to a sure bet in the whole
    # list, and the word "acute" inside "sub-acute" was hiding it.
    if _HER_OWN_SETTING.search(text):
        return False
    return bool(_SPECIALTY_EXP.search(text))


# The setting she works in today. "sub-acute", "post-acute", "skilled
# nursing", "long-term care" — experience she has, not experience she
# lacks.
_HER_OWN_SETTING = re.compile(
    r"sub.?acute|post.?acute|skilled nursing|long.?term care|\bSNF\b|\bLTC\b",
    re.I)

# A move sideways: same kind of floor, same kind of experience. Worth
# listing (it may pay more) but never worth ranking above a hospital job.
_LATERAL_SETTING = re.compile(
    r"skilled nursing|\bSNF\b|sub.?acute|post.?acute|long.?term care"
    r"|\bLTC\b|nursing home|rehabilitation (?:center|facility)", re.I)

# Long-term ACUTE care is a hospital, not a nursing home — the one
# "long-term" phrase that must never be demoted. Kentfield is an LTACH,
# and it is the single best bridge job in the list.
_LTAC = re.compile(r"long.?term acute|\bLTACH?\b|acute (?:care )?hospital", re.I)

# A promoted grade in the title. II is one step up, III and IV are two and
# three; none are open to a nurse in her first year, whatever a long
# posting says elsewhere about new graduates.
#
# Matched on the title only, and anchored to the nurse noun so that a unit
# number ("Emergency 338", "RN - CATH LAB 31") is not read as a grade.
_SENIOR_GRADE = re.compile(
    r"\b(?:nurse|rn|clinical nurse|staff nurse)\s*"
    r"(?:I{2,3}|IV|2|3|4)\b"
    r"|\blevel\s*(?:I{2,3}|IV|2|3|4)\b"
    r"|\bsenior\b|\bcharge nurse\b", re.I)


def tier_for(bucket: str, *, setting: str = "", evidence: str = "",
             title: str = "") -> str:
    """Which section of her list this posting belongs in."""
    bucket = (bucket or "").strip()
    setting = (setting or "").lower()
    title = title or ""
    ev = evidence or ""

    # SNF outranks the bucket. A skilled-nursing posting with no
    # experience bar is still a lateral move from the job she has, and the
    # entire point of the search is acute-care experience. It stays in the
    # list because it pays more than her current role, but it must never
    # sit above a hospital posting she can actually apply to.
    #
    # LTAC is deliberately NOT demoted: a long-term acute care hospital is
    # an acute setting and counts as acute experience on a résumé. It is
    # the bridge role this whole search exists to find, so the check below
    # must not confuse "post acute" with "long-term ACUTE care".
    if _LATERAL_SETTING.search(setting) and not _LTAC.search(setting):
        return "B_bridge"

    months = required_months(ev)
    # Experience she cannot accrue on a subacute floor. "Six months of OR
    # RN experience" is a smaller number than a year and still a closed
    # door, so the size of the requirement is irrelevant once it names a
    # unit she does not work in.
    specialist = _specialty_experience(ev)

    if bucket == "No experience required":
        if _NO_BAR.search(ev):
            return "A_open"
        if _SPECIALTY.search(title):
            return "A_spec_entry"
        return "A_pref"
    if bucket == "Level I / new grad":
        # A senior grade in the title beats a new-grad hint in the body.
        # "Clinical Nurse III - Operating Room" mentions new graduates
        # somewhere in a long posting, but a III is two promotions above
        # her; the grade is the requirement.
        if _SENIOR_GRADE.search(title):
            return "E_acute_req"
        # A stated requirement beats the grade in the title. "Registered
        # Nurse (RN) - Neuro ICU" is a Level I title whose posting demands
        # ICU experience; offering it as open to new grads sends her to a
        # wall.
        if months or specialist:
            if specialist or (months or 0) > _REACHABLE_MONTHS:
                return "E_acute_req"
            return "B_soon"
        if _NO_BAR.search(ev):
            return "A_open"
        # An entry-grade post on a specialty floor is a real chance but a
        # longer shot than an open general one, so it sits below it rather
        # than crowding the top of the list.
        return "A_spec_entry" if _SPECIALTY.search(title) else "A_newgrad"
    if bucket == "Requirements unclear":
        return "C_unclear"
    if bucket == "Experience required, not acute":
        if specialist:
            return "D_specialty"
        if months is not None and months > _REACHABLE_MONTHS:
            return "F_tenure"
        # A reachable general requirement outranks the unit in the title.
        # "RN - Emergency, needs 12 months general nursing" is a job she
        # can hold in May 2027; demoting it for the word "Emergency"
        # buried six of them.
        if months is not None:
            return "B_soon"
        return "D_specialty" if _SPECIALTY.search(title) else "B_soon"
    if bucket in ("Acute care required", "Level II role"):
        return "E_acute_req"
    return "C_unclear"


def esc(s) -> str:
    return html.escape(str(s or ""), quote=True)


_CSS = """
*{box-sizing:border-box}
body{font:15px/1.5 -apple-system,BlinkMacSystemFont,system-ui,sans-serif;
  margin:0;background:#f4f5f7;color:#16202a;-webkit-text-size-adjust:100%}
header{background:#fff;border-bottom:1px solid #dde2e6;padding:12px 16px;
  position:sticky;top:0;z-index:9}
h1{margin:0;font-size:18px;letter-spacing:-.2px}
.sub{color:#63727e;font-size:12.5px;margin-top:2px}
.f{margin-top:10px;display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.f button{border:1px solid #ccd3d9;background:#fff;border-radius:99px;
  padding:6px 12px;font-size:13px;cursor:pointer;color:#16202a;
  -webkit-tap-highlight-color:transparent}
.f button.on{background:#0a7;color:#fff;border-color:#0a7}
input{border:1px solid #ccd3d9;border-radius:8px;padding:8px 10px;
  font-size:16px;flex:1;min-width:140px}
main{padding:12px 16px;max-width:880px;margin:0 auto}
.gh{font-size:12px;font-weight:700;color:#4b5a66;margin:22px 0 8px;
  text-transform:uppercase;letter-spacing:.6px}
.gh.hid{display:none}
.c{background:#fff;border:1px solid #e3e7ea;border-radius:11px;
  padding:13px 15px;margin-bottom:9px}
.c.hid{display:none}
.c.done{opacity:.42}
.h a{font-weight:640;color:#0b5fbe;text-decoration:none;font-size:15.5px}
.rw{font-size:12px;color:#7fd4a8;margin:6px 0 0;line-height:1.45}
.gh.best{color:#ffd479;border-color:#3a3f2a}
.c.pin{border-left:3px solid #ffd479}
.ref{font-size:11px;color:#6b7c8f;font-weight:400}
.v{background:#0a7;color:#fff;font-size:10px;padding:2px 6px;
  border-radius:4px;margin-left:7px;vertical-align:2px;letter-spacing:.4px}
.m{color:#526270;font-size:13px;margin-top:4px}
.det{color:#7a8894;font-size:12px;margin-top:3px}
.w{font-size:13px;margin-top:7px;color:#2b3a47;border-left:3px solid #e3e7ea;
  padding-left:9px}
.b{margin-top:10px;display:flex;gap:7px}
.b button{border:1px solid #ccd3d9;background:#fafbfc;border-radius:7px;
  padding:6px 12px;font-size:12.5px;cursor:pointer;color:#16202a;
  -webkit-tap-highlight-color:transparent}
.b button:active{background:#eef1f3}
.empty{color:#63727e;font-size:14px;padding:22px 0;text-align:center}
footer{color:#8996a2;font-size:11.5px;padding:24px 16px;text-align:center}
@media(prefers-color-scheme:dark){
  body{background:#11171d;color:#e6ecf1}
  header,.c{background:#1a222b;border-color:#2b3945}
  .f button,.b button{background:#222c37;border-color:#33434f;color:#e6ecf1}
  input{background:#222c37;border-color:#33434f;color:#e6ecf1}
  .h a{color:#6cb0ff} .w{color:#c3ced8;border-left-color:#2b3945}
  .m{color:#9dabb8} .det,.sub,.gh{color:#8996a2}
}
"""

# Marks are keyed on the posting key and kept in localStorage, so they
# survive every rebuild. Changing this key silently wipes them.
_JS = """
const K='rnjobs.marks.v2';
let M={},D='',H=true,L=false;
try{M=JSON.parse(localStorage.getItem(K)||'{}')}catch(e){M={}}
function sv(){try{localStorage.setItem(K,JSON.stringify(M))}catch(e){}}
function mk(k,s){M[k]=M[k]===s?undefined:s;if(!M[k])delete M[k];sv();ap()}
function fd(b,d){D=d;document.querySelectorAll('.fd').forEach(x=>
  x.classList.remove('on'));b.classList.add('on');ap()}
function th(b){H=!H;b.classList.toggle('on',H);ap()}
function tl(b){L=!L;b.classList.toggle('on',L);
  b.textContent=L?'Hide not-yet-eligible':'Show not-yet-eligible';ap()}
function rs(){if(confirm('Clear all Applied/Pass marks?')){M={};sv();ap()}}
function ap(){
  const q=(document.getElementById('q').value||'').toLowerCase();
  let shown=0;
  document.querySelectorAll('.c').forEach(c=>{
    const mark=M[c.dataset.k];
    let vis=true;
    if(c.dataset.elig==='0'&&!L)vis=false;
    if(D&&c.dataset.d!==D)vis=false;
    if(q&&c.dataset.s.indexOf(q)<0)vis=false;
    if(H&&mark)vis=false;
    c.classList.toggle('hid',!vis);
    c.classList.toggle('done',!!mark);
    // A Best-bets card is a second copy of a job that also appears in its
    // own tier below. Counting both would report 119 jobs where there are
    // 111, so the pinned copy is shown but never counted.
    if(vis&&c.dataset.elig!=='0'&&!c.classList.contains('pin'))shown++;
  });
  document.querySelectorAll('.gh').forEach(h=>{
    const g=h.dataset.gh;
    h.classList.toggle('hid',![...document.querySelectorAll('.c')].some(
      c=>c.dataset.g===g&&!c.classList.contains('hid')));
  });
  const e=document.getElementById('empty');
  if(e)e.style.display=shown?'none':'block';
  const n=document.getElementById('cnt');
  if(n)n.textContent=shown;
}
ap();
"""


def _card(j: dict, pinned: bool = False) -> str:
    elig = "1" if j["tier"] in ELIGIBLE else "0"
    blob = f"{j['title']} {j['employer']} {j['location']} {j.get('key','')}".lower()
    badge = '<span class="v">VERIFIED</span>' if j["tier"] == "A_open" else ""
    det = f'<div class="det">{esc(j["details"])}</div>' if j.get("details") else ""
    # A posting that states no requirement says so. The evidence ships with
    # the verdict, always — if the quote does not support the label, the
    # rule is wrong, and she can see that at a glance.
    why = j.get("evidence") or "This posting states no requirement."
    # Big employers post the same title six times for six different units,
    # and the cards are then indistinguishable: she cannot tell which one
    # she already opened. The posting number is the only thing that
    # differs, so it goes on the card.
    ref = j["key"].rsplit("::", 1)[-1] if "::" in j.get("key", "") else ""
    ref_html = f' <span class="ref">#{esc(ref)}</span>' if ref else ""
    # Why this ranks where it does. A ranking she cannot interrogate is
    # one she has to take on trust, and nothing else in this repo asks
    # that of her — every verdict already ships with its evidence.
    rank_why = ""
    if j.get("why_ranked"):
        rank_why = (f'<div class="rw">{esc(" &middot; ".join(j["why_ranked"]))}</div>'
                    .replace("&amp;middot;", "&middot;"))
    # A pinned copy appears in Best bets AND in its own tier; the key must
    # stay identical so that marking either one marks both, but the DOM id
    # must not collide.
    cls = "c pin" if pinned else "c"
    return (
        f'<div class="{cls}" data-g="{esc("__best" if pinned else j["tier"])}"'
        f' data-d="{esc(j["drive"])}"'
        f' data-k="{esc(j["key"])}" data-elig="{elig}" data-s="{esc(blob)}">'
        f'<div class="h"><a href="{esc(j["url"])}" target="_blank"'
        f' rel="noopener">{esc(j["title"])}</a>{badge}{ref_html}</div>'
        f'<div class="m">{esc(j["employer"])} &middot; {esc(j["location"])}'
        + (f' &middot; <b>{esc(j["drive"])} min</b>' if j.get("drive") else "")
        + f'</div>{det}{rank_why}<div class="w">{esc(why)}</div>'
        f'<div class="b">'
        f'<button onclick="mk(\'{esc(j["key"])}\',\'a\')">Applied</button>'
        f'<button onclick="mk(\'{esc(j["key"])}\',\'p\')">Pass</button>'
        f'</div></div>')


def render_page(jobs: list, scanned_at: str) -> str:
    """`jobs` is a list of plain dicts — see build_rows() in run_scan."""
    jobs = sorted(jobs, key=lambda j: (TIER_RANK.get(j["tier"], 99),
                                       -float(j.get("score") or 0),
                                       DRIVE_RANK.get(j.get("drive", ""), 9),
                                       j.get("employer", "")))
    elig = [j for j in jobs if j["tier"] in ELIGIBLE]
    near = sum(1 for j in elig if DRIVE_RANK.get(j.get("drive", ""), 9) <= 2)

    out = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1,'
        'viewport-fit=cover">',
        '<title>RN Jobs</title>',
        # Installable to the iOS home screen with no app, no store, no
        # provisioning profile: this is the whole "app" story.
        '<meta name="apple-mobile-web-app-capable" content="yes">',
        '<meta name="apple-mobile-web-app-title" content="RN Jobs">',
        '<meta name="theme-color" content="#0a7">',
        f'<style>{_CSS}</style></head><body>',
        '<header><h1>RN Jobs</h1>',
        f'<div class="sub"><b id="cnt">{len(elig)}</b> to apply to &middot; '
        f'{near} within 90 min &middot; scanned {esc(scanned_at)}</div>',
        '<div class="f">',
        '<button class="fd on" onclick="fd(this,\'\')">All</button>',
        '<button class="fd" onclick="fd(this,\'&lt;30\')">&lt;30 min</button>',
        '<button class="fd" onclick="fd(this,\'30-60\')">30-60</button>',
        '<button class="fd" onclick="fd(this,\'60-90\')">60-90</button>',
        '<button class="on" onclick="th(this)">Hide handled</button>',
        '<button onclick="tl(this)">Show not-yet-eligible</button>',
        '<input id="q" placeholder="search title, employer, city" '
        'oninput="ap()">',
        '<button onclick="rs()">Reset</button>',
        '</div></header><main>',
    ]

    # Best bets: the highest-scoring eligible jobs regardless of tier.
    #
    # Tiers answer "may she apply"; they cannot answer "which of these 111
    # is worth her Saturday". An ED post at $90/hr twenty minutes away and
    # a per-diem SNF job at $46 an hour and a half away sit in different
    # tiers, and the one she should open first was not necessarily near
    # the top of any of them.
    #
    # Capped at eight. A shortlist of thirty is just the list again.
    best = [j for j in sorted(elig, key=lambda x: -float(x.get("score") or 0))
            if float(j.get("score") or 0) > 0][:8]
    if best:
        out.append('<div class="gh best" data-gh="__best">'
                   '&#9733; Best bets &mdash; worth opening first</div>')
        for j in best:
            out.append(_card(j, pinned=True))

    seen = None
    for j in jobs:
        if j["tier"] != seen:
            out.append(f'<div class="gh" data-gh="{esc(j["tier"])}">'
                       f'{esc(TIER_HEADING.get(j["tier"], j["tier"]))}</div>')
            seen = j["tier"]
        out.append(_card(j))
    out.append('<div class="empty" id="empty" style="display:none">'
               'Nothing matches those filters.</div></main>')
    out.append(f'<footer>{len(jobs)} postings &middot; '
               f'Applied/Pass marks are saved on this device only</footer>')
    out.append(f'<script>{_JS}</script></body></html>')
    return "".join(out)
