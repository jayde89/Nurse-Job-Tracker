# Working on this repo

A job scanner for a new-graduate RN looking for staff nurse work within two
hours of Oakland. It runs on GitHub Actions three times a day and commits
its own results back. `README.md` is written for the person using it and
explains what it does; this file is what an agent needs before changing it.

The output the user reads is `board.html`, published as a Claude artifact:

    https://claude.ai/code/artifact/6155f927-5fcc-4299-a1e7-fc82e164383d

Republish it to that URL after a scan (`Artifact` with `url=` from any
other conversation, or the same file path in the one that created it) —
publishing without the URL makes a second board and strands the marks on
the first.

## The thing that matters

A false **"no experience required"** is the most expensive bug this
codebase can produce. It spends a job-seeker's time on an application they
were never eligible for. Every verdict therefore carries the requirement
sentence it rests on, and that quote is the contract: **if the evidence
doesn't support the label, the rule is wrong.** Four bugs of exactly this
shape were found by reading labels against their own evidence — see the
"Read the evidence" section of the README.

Bias toward showing too much. `UNCLEAR` reaches the user; only
`ACUTE_REQUIRED` is suppressed. Never widen suppression to tidy the list.

The second-most expensive bug is **showing a job once**. That one already
shipped: the scan used to open a GitHub issue holding only postings that
were both new and applicable, so a job not acted on the morning it appeared
was never surfaced again. The board is the fix and its rule is absolute —
**a posting leaves the board only because the user marked it**, never
because it aged, never because a later scan had something fresher. Any change that
makes the board show a subset of what is open is that bug returning.

`highlights.py` puts a line of detail under each title in the digest —
facility, setting, full-time or per diem, shift, pay — and it is under the
same contract. **Every field on that line is a span the posting states.**
Nothing is inferred from the facility type, the employer or the title's
tone; a shift the posting never named stays blank. Care setting is the one
field not read from body text at all, because "skilled nursing experience
preferred" in a hospital posting would otherwise relabel an ED job as a
nursing home — it comes from the adapter, which knows what it is reading.

## The board, and the loop that keeps it true

`board.py` writes `board.html` from `applications.csv` — every row
`is_open()` accepts, in three eligibility tiers. The page is published as
an artifact with the `db` capability, and the **Applied** / **Not relevant**
buttons write one document per posting into that store:

    marks/<slug>-<hash>   {key, mark, at, title, employer, url}

The document id is a slug of the ledger `Key`, because the store's id
grammar has no room for a space. **Match on the `key` field, never on the
id** — `board.doc_id()` could change, and a mark reattached to the wrong
posting is the same class of quiet error as a wrong label.

To fold marks back into the ledger:

1. `Artifact` `action: "read_db"`, `db_op: "list"`, `collection: "marks"`,
   with `out_dir` pointing at a scratch directory.
2. `python3 sync_board.py <that directory>` — writes `Status` only, only
   for rows that are still open, and rebuilds `board.html`.
3. Republish `board.html` to the URL above.

`sync_board.py` will not overwrite a status set by hand in the ledger. The
ledger is newer than a page that may have been open for days, so the ledger
wins, and the skipped mark is printed rather than swallowed.

There is no email and no GitHub issue. Don't add one back: an alert can
only carry what is new, which is exactly the failure the board exists to
fix. `notify.py` was deleted, not disabled.

## Before you push a rule change

```bash
python3 test_rules.py     # 92 cases, no dependencies, ~instant
```

Every case is a bug that already shipped once. The workflow runs this
before each scan, so a regression fails the run rather than quietly
narrowing what the user sees. Add a case for anything you fix.

## Don't run the scanner locally without meaning to

`python3 run_scan.py` rewrites `applications.csv`, `DIGEST.md`,
`digest.html`, `board.html` and `state/seen.json` — all five are committed,
and the Action commits them too, so a casual local run creates a conflict
with the next scheduled scan. To test changes, copy the repo to a temp directory and
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
  the board all read through it, so they cannot disagree about what
  "already handled" means. An unrecognised status is treated as open on
  purpose — a typo should show a job again, never swallow one.
  `normalize_status()` folds case, spacing and `-`/`_`, so the several
  spellings of "not relevant" are one status.
- **Applications in progress are rendered from the ledger, never from the
  scan's results.** A posting you applied to is among the likeliest to be
  taken down, and building that section from `shown` meant the application
  disappeared from the dashboard the moment the employer pulled the
  listing. The row was always in the CSV; nothing surfaced it.
- **Never tokenize the city table in `geo.py`.** Match whole phrases,
  longest first. Splitting on whitespace once put "creek" (from Sutter
  Creek) in the out-of-range set and silently rejected every Walnut Creek
  job.
- **Kaiser Permanente and Stanford are excluded by request.** Don't add
  them, including through any aggregator.
- Adapters fail independently. One broken source prints a line and the scan
  continues; it never aborts the run.

## Adding a source

Ten adapters live in `adapters.py`, each a class with `fetch_listings()`
and `fetch_detail()` returning `Posting`. Register it in `ADAPTERS`.

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
- **CalCareers** genuinely is blocked: ASP.NET WebForms rendering through
  DevExpress AJAX callbacks. A `__VIEWSTATE` POST returns a page with no
  jobs in it.

Check the careers subdomain, not the marketing site. Check whether the
listing endpoint reports its own total, and compare that to what you
actually collect — three separate silent truncations were found that way
(Workday's page cap, NEOGOV's unstable sort, El Camino's zeroed total).

## Outstanding

- **The obvious gap was an employer nobody had listed.** St. Rose sat
  outside every scan for months because the coverage question had been
  asked as "which of these systems are we missing?" rather than "which
  hospitals are within range?" It is independent, so no system-level
  adapter reached it. Worth re-asking the second question before adding
  depth to a source already covered.
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
