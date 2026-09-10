# Working on this repo

A job scanner for a new-graduate RN looking for staff nurse work within two
hours of Oakland. It runs on GitHub Actions three times a day and commits
its own results back. `README.md` is written for the person using it and
explains what it does; this file is what an agent needs before changing it.

## The thing that matters

A false **"no experience required"** is the most expensive bug this
codebase can produce. It spends a job-seeker's time on an application they
were never eligible for. Every verdict therefore carries the requirement
sentence it rests on, and that quote is the contract: **if the evidence
doesn't support the label, the rule is wrong.** Four bugs of exactly this
shape were found by reading labels against their own evidence — see the
"Read the evidence" section of the README.

## Who this is for, in the user's own words

Restated by the user on 2026-09-09, after an audit found the list drifting
away from it. A posting belongs on the list when it is one of:

- **no experience required**
- **staff nurse I** / Level I
- **new graduate nurse** or an RN residency
- experience stated as **basic RN experience that is not acute care**

And it does **not** belong when the title is a rung above a new graduate:
charge, lead, supervisor, manager, director, coordinator, navigator,
consultant, specialist, educator, preceptor. `EXCLUDE_TITLE` in
`adapters.py` enforces this and the list there is deliberately narrower
than the rest of that filter — 36 of 197 shown rows were these roles
before it was tightened. Do not loosen it back on the general "bias toward
showing too much" principle below; the user asked for this specifically,
the same way he asked for the graded Level II rule.

The two principles are not in tension. Show too much *within* the roles a
new graduate can be hired into; show nothing from the roles above them.

Bias toward showing too much. `UNCLEAR` reaches the user. Two buckets are
suppressed: `ACUTE_REQUIRED`, and `LEVEL_II_TITLE` — a title carrying a
graded Level II+ rung, which is the grade above the one a new graduate is
hired into. Do not widen suppression past those two to tidy the list.

**The Level II rule is an exception the user asked for on 2026-09-04**, so
don't "fix" it back. Graded roles were 66 of 122 open rows and were
crowding out the usable ones; a live scan showed the grade never once
coincided with `NO_EXPERIENCE` or `STAFF_NURSE_I`, which is what makes
hiding it cheap. If that stops being true, this is the first rule to
re-examine. It is applied *after* the requirement rules in `classify()`,
never before — see the docstring there for why, and keep it that way.
The user also chose to show no count of what was hidden, so the digest
says nothing about it; the scan's stdout line still reports it honestly.

**An employer whose name contains "Hospital" is not stating a requirement.**
`ACUTE` lists `hospital` as a marker, so every sentence naming such an
employer matched it. Central Valley Specialty Hospital — a long-term acute
care hospital in Modesto whose posting says "We encourage new RNs to
apply" and asks only for a licence, BLS and ACLS — was suppressed as
`ACUTE_REQUIRED`, because its benefits paragraph ("wages determined based
on ... qualifications and experience") contained both an `ACUTE` marker
(its own name) and the word "experience". Requiring the clause to also say
"experience" was not enough; benefits copy says "experience" too. Use
`ACUTE_EXPERIENCE`, which requires the two to be about each other. This
was systematic against LTAC, which is the one setting whose employers all
have "acute" or "hospital" in their names.

**Evidence must come from the field that actually said it, and must be the
tightest clause that says it.** Three ways this broke on 2026-09-09, all
found by reading labels against their own quotes:

- Stripping every HTML tag to a space merges a bulleted requirements list
  into one run-on. Adventist's two bullets became "...(BSN): Preferred
  Acute care facility experience: Preferred", and the digest quoted
  "(BSN): Preferred Acute care facility" — a truncated claim about a
  degree — as grounds for "no experience required". Use `_html_to_text`,
  which turns block tags into statement boundaries.
- The evidence for a hedged verdict was the first 200 characters of the
  experience section, which on a bulleted posting is whatever bullet came
  first. Quote the tightest clause that mentions experience and hedges it.
- `_snippet` falls back to the opening of the text when its pattern does
  not match, so a signal that lived only in the *title* was evidenced by
  hospital marketing copy. Quote whichever field matched.

**A verdict's label has to be supported by the sentence it quotes, and
five ways that broke were found on 2026-09-09 by reading live verdicts
after the adapters stopped flattening HTML.** Finer clauses are better
evidence and they also expose every place the classifier was picking the
wrong sentence:

- A posting that writes both `EXPERIENCE` and `MINIMUM QUALIFICATIONS`
  means the first. San Francisco puts the licence list and the
  recruitment process under Qualifications and the actual gate under
  Experience, and declared order took the wrong one.
- Sentences about the application are not requirements. "Applicants may
  be required to submit verification of qualifying education and
  experience" carries a required-word and the word experience and asks
  nothing of the nurse. `PROCESS_CLAUSE` skips these unless the clause
  states both a duration and a required-word, so a real gate worded as an
  instruction survives.
- `GENERAL_EXPERIENCE` must rest on a clause that names experience. When
  a generic QUALIFICATIONS heading swallows a whole page the surviving
  clause was "Performs other related duties as assigned/required"; that
  is `UNCLEAR`. A section the posting headed `EXPERIENCE` is exempt,
  because John Muir puts the word in the heading and never in the clause.
- Acute care offered as one setting among several is not an acute-care
  gate — "in an acute hospital, primary care facility, home health
  agency" is satisfied by clinic experience, which the user's criteria
  call eligible. `_acute_only` guards every clause-based suppression.
- **A Level I title is a signal, not a promise.** La Clínica posts
  "Registered Nurse I/II" and then asks for "two to three years clinical
  experience"; the title rule ran first and labelled it new-graduate.
  It now yields to an unhedged duration in the body, the same way
  `NO_EXPERIENCE` already does. Explicit new-grad language in the
  posting's own words still beats everything.

**A heading may end in a full stop, and that full stop is ours.**
`_html_to_text` terminates a block that ends without punctuation, which
is what a heading in its own `<p>` looks like, so "Minimum Job
Requirements." stopped matching the moment the adapters started keeping
statement boundaries — and La Clínica's RN I/II parsed to no requirements
section at all. Distinctive labels accept `[:.]?`; the prose labels still
require a colon, because "Experience." ending a sentence is a sentence.

**The section splitter treats "experience" as a heading wherever it
appears**, including mid-sentence. "Acute care experience: 2 years
Required" therefore yields a section body of "2 years Required" with the
words "Acute care" outside it, and the posting read as
`GENERAL_EXPERIENCE` — shown to a new graduate who could not apply.
`_classify_requirements` re-reads each required clause in its full
sentence before concluding a requirement is not acute. Widening the
*section* rule instead would risk missing requirements, which is the one
direction that costs a job.

`highlights.py` puts a line of detail under each title in the digest —
facility, setting, full-time or per diem, shift, pay — and it is under the
same contract. **Every field on that line is a span the posting states.**
Nothing is inferred from the facility type, the employer or the title's
tone; a shift the posting never named stays blank. Care setting is the one
field not read from body text at all, because "skilled nursing experience
preferred" in a hospital posting would otherwise relabel an ED job as a
nursing home — it comes from the adapter, which knows what it is reading.

## Before you push a rule change

```bash
python3 test_rules.py     # 332 cases, no dependencies, ~instant
```

Every case is a bug that already shipped once. The workflow runs this
before each scan, so a regression fails the run rather than quietly
narrowing what the user sees. Add a case for anything you fix.

## Don't run the scanner locally without meaning to

`python3 run_scan.py` rewrites `applications.csv`, `DIGEST.md`,
`digest.html` and `state/seen.json` — all four are committed, and the
Action commits them too, so a casual local run creates a conflict with the
next scheduled scan. To test changes, copy the repo to a temp directory and
run there:

```bash
T=$(mktemp -d); cp *.py *.json applications.csv $T/; mkdir -p $T/state
cp state/seen.json $T/state/; (cd $T && python3 run_scan.py)
```

`--quick` skips detail fetches and runs in about a minute, but classifies
nothing, so it can't tell you whether a classifier change worked.

A full run takes 8-10 minutes, most of it the deliberate one-second pause
between requests. Keep that pause.

## Invariants

- **A posting is "closed" when the source stops listing it, never when we
  stop showing it, and never when we could not read it.** Two halves,
  both learned the hard way. `live` in `run_scan.py` is built from every
  posting the scan *classified*, not from `shown` — those sets differ the
  moment anything is suppressed, and reading it off `shown` would have
  written `closed` onto 66 still-open Level II rows the first time that
  rule ran. The second half: a row is only closed when its employer is in
  `covered`, the set of employers whose listing this scan actually read
  end to end, which `scan()` records per source in `state/sources.json`.
  On 2026-09-08 and 09-09 governmentjobs.com timed out for five of six
  agencies; the adapter still returned the sixth and so still counted as
  a healthy source, and eleven live postings were marked closed — six of
  them Contra Costa Regional Medical Center RN roles verified open the
  next day. An adapter that fans out over several employers (NeoGov over
  agencies, Radancy over city pages) must report which it truly reached
  via `covered_employers()`; a partial read reports nothing.

- **A posting that comes back reopens.** `closed` is the only archived
  status the scanner sets, so it is the only one it may clear. Every
  status you set yourself — applied, rejected, withdrawn — survives the
  posting being relisted. Without this, one outage hid a job forever.
- **`applications.csv` is the user's file.** The scanner may add rows and
  refresh employer-controlled columns. It must never write `Status`,
  `Applied On` or `Notes`. A posting that disappears is marked `closed`,
  never deleted, and only when the row is still open, so an application
  already sent keeps its record. `Marked active` is the one status-adjacent
  column the scanner owns: it records when a row was first seen in an
  active state, because `Applied On` is the user's and is usually left
  blank when the edit is made on a phone.
- **A job the user has marked never reappears as a job to apply to.**
  `is_open()` in `run_scan.py` is the single gate; the digest, the HTML and
  the email alert all read through it, so they cannot disagree about what
  "already handled" means. An unrecognised status is treated as open on
  purpose — a typo should show a job again, never swallow one.
- **Applications in progress are rendered from the ledger, never from the
  scan's results.** A posting you applied to is among the likeliest to be
  taken down, and building that section from `shown` meant the application
  disappeared from the dashboard the moment the employer pulled the
  listing. The row was always in the CSV; nothing surfaced it.
- **A posting whose location names another state is out of range, and
  that is decided before the city table is consulted.** Only the last
  comma-separated segment is tested and only against a whole state name
  or code, because testing the whole string puts "Nevada City, CA" and
  "Kansas City" out on a substring. This exists because 30 of one scan's
  33 review rows were the same CommonSpirit posting in Lufkin and
  Livingston, Texas, arriving without coordinates — a review bucket is
  only useful while it is short enough to read.
- **Never tokenize the city table in `geo.py`.** Match whole phrases,
  longest first. Splitting on whitespace once put "creek" (from Sutter
  Creek) in the out-of-range set and silently rejected every Walnut Creek
  job.
- **Kaiser Permanente and Stanford are excluded by request.** Don't add
  them, including through any aggregator.
- Adapters fail independently. One broken source prints a line and the scan
  continues; it never aborts the run.

## Adding a source

Twenty adapters live in `adapters.py`, each a class with
`fetch_listings()` and `fetch_detail()` returning `Posting`. Register it
in `ADAPTERS`. If it reads more than one employer, give it
`covered_employers()` too — see the closing invariant above.

Set `Posting.setting` if the adapter knows what kind of nursing its
employer does (PACS is skilled nursing, Kindred is LTAC). Leave it `None`
for a mixed employer rather than guessing per posting.

What the last round of work established, which is worth knowing before
concluding a source needs a headless browser — two of three "blocked"
sources didn't:

- **NEOGOV** (`governmentjobs.com`): looks entirely client-rendered. It
  isn't — the agency root renders server-side for a caller sending
  `X-Requested-With: XMLHttpRequest`. Paging needs an explicit `sort`, or
  the server reorders between requests and drops the tail silently.
- **JIBE** (Vibra): `/api/jobs` is open, filters by state server-side and
  returns full descriptions inline.
- **SmartRecruiters** (San Francisco): open API. A wrong company
  identifier returns HTTP 200 with `totalFound: 0`, which looks like an
  empty board rather than a mistake.
- **Smart Hires** (St. Rose): pages, filters and sorts through DWR calls
  made after load, so it reads as an app. The table is server-rendered
  complete in the first response — every requisition, no paging. Each row
  carries hidden inputs holding the fields the visible cell truncates.
  Do not glue an invented section heading onto a description to give the
  classifier something to anchor on: prefixing St. Rose's qualifications
  with `Required Qualification:` turned that phrase into a requirement
  clause and produced a verdict quoting a heading as its evidence.
- **Oracle Recruiting Cloud** (Fusion) turned out to be the single
  highest-yield platform here — UCSF, Tenet, Providence, Adventist and
  NorthBay all run it, and none were covered before. Unauthenticated REST:
  `/hcmRestApi/resources/latest/recruitingCEJobRequisitions?...finder=findReqs;siteNumber=CX_1,limit=200,offset=N`.
  Two traps. `expand=requisitionList.secondaryLocations` is mandatory —
  without it the response carries counts and facets but an empty
  `requisitionList`, which reads exactly like an employer with no jobs.
  And `limit` is capped at 200 server-side: asking for 500 returns 200
  and no error. Link to the Oracle-hosted candidate page, never a branded
  careers domain composed from the Oracle id — Tenet's own site keys on an
  unrelated Radancy id, so the composed URL 404s.
- **Radancy TalentBrew** (CommonSpirit/Dignity, and the front end on
  Tenet) accepts `Keywords` and `Location` on `/search-jobs/results` and
  ignores both: a nurse search near San Ramon returns page 1 of every job
  in the company, starting in San Antonio. Latitude/longitude returns
  `{"results": ""}`. What works is the per-city page linked from the
  site's own `/sitemap.xml`. Two row templates are in the wild; key the
  parser on `data-job-id`, because keying on the outer `<li>` silently
  loses facility and location on the newer one, whose job-info fields are
  themselves nested `<li>` elements.
- **HRMDirect** (La Clínica de La Raza) serves its whole board in one GET
  and needs two things right. Key rows on `data-req-id`, because the
  title cell's anchor is never closed — `<a href=...>Registered Nurse
  I/II</td>` — so keying on `<a>...</a>` swallows every row up to the
  next closing tag and reports one posting where there are 155. And take
  the detail URL from the row: the same requisition at two clinics has
  two `req_loc` values, and the wrong one returns a page with no job text
  in it, with no error. The board is cp1252; decoded as UTF-8 the
  employer's own name comes out "La Cl\ufffdnica" and gets quoted back as
  evidence, which is why `_request` takes an encoding.
- **Paylocity Recruiting** (`recruiting.paylocity.com`) is what small
  independent employers use, and it reaches Central Valley Specialty
  Hospital in Modesto. The board is one GET: the page embeds its whole job
  list as JSON under `"Jobs"`, with the real city in a nested
  `JobLocation` object — `LocationName` is "On Site" or "Main Office",
  which geo cannot rank. Do not classify from the listing: the
  `Description` there is a 110-character teaser, the same trap that
  produced 40 false "no experience required" verdicts when Sutter was read
  through Phenom. The full text is on `/recruiting/jobs/Details/{JobId}`
  inside `job-preview-details`.
- **JobAps** (`jobapscloud.com`) is the third CA-government platform after
  NEOGOV and SmartRecruiters. San Joaquin County is on it. Its landing
  page is the whole listing — no paging, no JSON. Strip unclosed trailing
  tags from its cells: it writes `<td class="Locs">French Camp<br </td>`,
  and a well-formed-tags-only strip leaves `French Camp<br`, which geo
  cannot match.
- **CalCareers** genuinely is blocked: ASP.NET WebForms rendering through
  DevExpress AJAX callbacks. A `__VIEWSTATE` POST returns a page with no
  jobs in it.
- **HCA Healthcare** (Good Samaritan San Jose, Regional Medical Center) is
  genuinely blocked too, and differently: a Cloudflare interstitial on
  every path, `robots.txt` included, with full browser headers. Needs a
  browser. This one is a known live gap — Good Samaritan is a hospital the
  user named as one he missed.
- **Do not guess a government slug from the county name.** Searching
  NEOGOV for San Joaquin County finds `sjcounty`, which is a real,
  populated, entirely plausible board — for San Juan County, Utah.
  `alamedaca` is the City of Alameda, not the county. Verify every slug
  against a posting's own `addressLocality` before adding it.

- **iCIMS** (Sonoma Valley Hospital, Seton Medical Center) reads as an
  app and is not:
  `/jobs/search?ss=1` renders the whole listing server-side, twenty cards
  to a page. Follow the portal's own `<link rel="next">` rather than
  guessing `pr=N` — a guessed parameter set silently re-serves page one,
  which looks like the end of the board. The detail page carries a JSON-LD
  JobPosting **only** with `in_iframe=1`; the plain URL is a 268 KB
  marketing wrapper with no structured data at all.
- **UKG Pro Recruiting** (`recruiting*.ultipro.com`, Telecare) POSTs to
  `JobBoardView/LoadSearchResults` and answers an unauthenticated caller —
  but only to a small payload. The full filter block the browser sends
  returns HTTP 500; `{"opportunitySearch":{"Top","Skip","QueryString",
  "OrderBy":[],"Filters":[]}}` works. The detail page embeds the whole
  opportunity as JSON, description included; there is no separate JSON
  endpoint that answers without a session.
- **JobAps** writes `<th class="JobTitle">` on an agency's main table and
  a bare `<th scope="row">` on its departmental ones. Keying on the class
  read 88 of San Joaquin's 98 rows and none at all of Alameda's, whose
  whole board uses the bare form. Key on the anchors inside the cell. The
  listing is not always at the agency root either — Alameda's root is a
  splash page and the board is at `jobboard.asp`, so reading the root
  returns a populated-looking page with no jobs in it. The bulletin lives
  in `class="JobBulletinBody"`, and taking it is not tidying: without it
  the description opens with the site's navigation menu and the classifier
  reads from the front of what it is given.
- **Paycom** (`paycomonline.net`, Dameron Hospital in Stockton, Community
  Medical Centers) is genuinely blocked. The board is a React app, no
  `/api/*` path answers, the loader bundle carries no endpoint, and there
  is no RSS or feed. Needs a browser, same as HCA and CalCareers.

Check the careers subdomain, not the marketing site. Check whether the
listing endpoint reports its own total, and compare that to what you
actually collect — three separate silent truncations were found that way
(Workday's page cap, NEOGOV's unstable sort, El Camino's zeroed total).

## Long-term acute care

The user asked for LTAC specifically on 2026-09-09. There are **five**
inside the ring — the fifth was found the same day by asking which
hospitals are in Sonoma County rather than which systems were missing —
and all five are now read:

- **Kindred Hospital San Francisco Bay Area**, San Leandro (<30) —
  ScionHealth. Routinely has zero open staff RN roles; a scan showing
  nothing from it is usually correct, not broken.
- **Kentfield Hospital**, Kentfield (30-60) — Vibra, via the same JIBE
  board. Also frequently at zero.
- **Vibra Hospital of Sacramento**, Folsom (90-120) — Vibra. Usually the
  only LTAC with anything open, and it is at the far edge of the ring.
- **Central Valley Specialty Hospital**, Modesto (60-90) — Paylocity,
  added 2026-09-09. Was reached by nothing before.
- **Sonoma Specialty Hospital**, Sebastopol (90-120) — Paylocity, added
  2026-09-09. Sonoma County's only LTAC, 37 beds, belongs to no system,
  and had two RN roles open on the day it was added. It was missing
  because nothing had ever enumerated Sonoma County's hospitals; the
  adapter it needed was already in the file.

That distribution is why the list can look like it has no LTAC in it at
all: the two nearest are usually empty, and the rest are 60-120 minutes
out, so they sort to the bottom and fall outside an alert that leads with
the nearest postings.

## Outstanding

- **The obvious gap was an employer nobody had listed.** St. Rose sat
  outside every scan for months because the coverage question had been
  asked as "which of these systems are we missing?" rather than "which
  hospitals are within range?" It is independent, so no system-level
  adapter reached it. Worth re-asking the second question before adding
  depth to a source already covered. This repeated on 2026-09-09: a San
  Ramon Regional posting reached the user from outside every source, and
  the sweep that followed found UCSF, Tenet, Providence, Adventist,
  NorthBay, MarinHealth, Salinas Valley, CommonSpirit, San Joaquin and
  five more county agencies all unlisted. Ask the second question first.
- **Still genuinely missing, and worth the next push**: HCA (Good
  Samaritan San Jose, Regional Medical Center San Jose) and Washington
  Hospital Healthcare System in Fremont — both bot-blocked, both named by
  the user as places he has seen postings. Chinese Hospital in San
  Francisco runs no recognisable ATS. All three, plus CalCareers, are the
  remaining browser-shaped work.
- **Settled 2026-09-09: UCSF does carry the two former Dignity hospitals
  in San Francisco.** The purchase completed in August 2024 and Saint
  Francis Memorial is now UCSF Health Stanyan Hospital. Their postings are
  on the UCSF Oracle board filed under a generic "San Francisco, CA" with
  no facility field on the listing or in the description, which is why
  looking for the names finds nothing. SF has no hole; don't re-open this.
- **The gap that is left is not hospitals.** `COVERAGE.md` enumerates
  every RN employer inside the ring and says which are read, which are
  blocked and why, and which are not read yet. The short version: hospital
  coverage is now essentially complete, and the uncovered tier is skilled
  nursing beyond PACS, dialysis, hospice, community clinics and the rest
  of behavioural health — which is precisely the tier the user's own
  criteria describe as "basic RN experience that is not acute care". An
  Indeed cross-check near Santa Rosa put five of ten RN postings in that
  tier, in a county where every hospital is read.
- **USAJOBS / VA is the only adapter not returning.** It needs
  `USAJOBS_KEY` and `USAJOBS_EMAIL` as repo secrets; the key must be
  requested by the repo owner at https://developer.usajobs.gov/apirequest/.
  The adapter was written from the documented schema and has never run
  against live data, so its field mapping is unverified — check its first
  run's output carefully rather than trusting it.
- **CalCareers / CDCR** would need Playwright in the workflow. Not started;
  it is the only remaining source that actually requires a browser.
- **Known soft spots in the current output**, all visible in the digest:
  multi-site Workday postings are filed under their *nearest* site and
  labelled `City (+N more)`, which reads optimistically if the job is only
  fillable at the far one; a couple of PACS rows quote generic text because
  those postings state no requirement at all; and one Vibra posting is
  geo-located in Sacramento but is a "relocate to Fargo ND" role.
- **St. Rose's structured `Experience:` field can contradict its own
  prose.** The ED posting says "Minimum two-years Emergency Department
  experience preferred" and then states `Experience: Minimum 2 Years`. The
  adapter keeps both, so the duration veto fires and the posting reaches
  the user as `GENERAL_EXPERIENCE` or `UNCLEAR` rather than as
  no-experience-required. Check the first few runs of any new source for
  this shape — a hedged sentence over a hard field.

<!-- BEGIN claude-batch-kit -->
## Continuous batch working agreement

You are running a long, mostly-unattended session. Jayde is watching from his
phone through Claude Code Remote Control and will reply there. Work in batches,
checkpoint between them, and never burn the session idling.

### Batch shape
- A batch is 4–6 related tasks, roughly 20–40 minutes of work.
- Pick the batch yourself from the repo's own state (open TODOs, failing or
  missing tests, the roadmap/backlog file if one exists, obvious debt) unless
  Jayde has named the work.
- Finish the whole batch before stopping. Do not stop to ask about small
  judgement calls inside a batch — make the call, note it in the checkpoint,
  and keep going. If a call is genuinely irreversible or ambiguous in a way
  that would waste the batch, stop early and say so.

### Checkpoint (every batch, no exceptions)
End every batch with exactly this, as your final message:

```
# Batch <n> — <short title>
## Done
- <what changed, file-level, one line each>
- tests: <command> → <result>
## Notes
- <anything surprising, any judgement call made, any new debt found>
## Proposed next batch
1. <task>
2. <task>
3. <task>
## Reply
"go" · "go, but <change>" · "instead: <other work>" · "stop"
```

Then stop and wait. Never start the proposed batch on your own.

### Rules
- Commit at the end of each batch on a working branch, never directly on the
  default branch, never force-push, never merge. Merging is Jayde's.
- Keep the tests green. A batch that leaves the suite red is not done — fix it
  or revert within the same batch.
- If a usage limit interrupts you, stay in the session; Claude Code resumes
  automatically at the reset. Resume mid-batch, then checkpoint as normal.
- If Jayde replies mid-batch, finish the current task, then treat his message
  as the next instruction — don't abandon work half-applied.
- Keep the checkpoint short. It is read on a phone.
<!-- END claude-batch-kit -->
