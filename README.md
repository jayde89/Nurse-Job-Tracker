# RN job scanner

Scans sixteen employer and public-agency career systems three times a day
for staff RN openings within two hours of Oakland, reads each posting's
actual requirements, and hides the ones that require acute-care experience.

What it gives you is a **standing list**, read in Claude: every open
posting, every scan, staying there until you tick it **Applied** or **Not
relevant**. Nothing drops off because it stopped being new.

Runs on GitHub's servers. You never run anything after setup.

---

## Setup (about 20 minutes, once, at a computer)

### 1. Create the repository

github.com → **New repository**.

- Name: anything. `rn-scanner` is fine.
- **Private.** The ledger will contain your application history.
- Do **not** add a README — you already have one.

### 2. Put the files in

Two ways. Use whichever you're comfortable with.

**Web upload.** On the empty repo page, click **uploading an existing file**.
Drag in everything *except* the `.github` folder, then commit.

Then add the workflow separately, because drag-and-drop won't create hidden
folders: **Add file → Create new file**, and type this as the filename —

```
.github/workflows/rn-scan.yml
```

Typing the slashes creates the folders. Paste in the contents of
`rn-scan.yml`, then commit.

**Or git**, if you'd rather:

```bash
git init
git add .
git commit -m "initial"
git remote add origin git@github.com:YOURNAME/rn-scanner.git
git push -u origin main
```

When you're done the repo should look like this:

```
.github/workflows/rn-scan.yml
adapters.py
classifier.py
geo.py
highlights.py
board.py
sync_board.py
run_scan.py
test_rules.py
pacs_facilities.json
applications.csv
state/seen.json
README.md
```

Every `.py` file has to be there — the scan imports all of them, and a
missing one stops the run on the first line rather than halfway through.

`applications.csv` and `state/seen.json` carry the current scan. Uploading
them means your first automated run reports only genuinely new postings
instead of all of them.

### 3. Let the workflow write back

**Settings → Actions → General → Workflow permissions** →
select **Read and write permissions** → Save.

Without this the scan runs but can't commit results, so nothing updates and
every run reports everything as new.

### 4. Run it once by hand

**Actions** tab. If prompted, click the button to enable workflows.

Select **rn-scan** in the left sidebar → **Run workflow** → **Run workflow**.

It takes 8–10 minutes, most of which is a one-second pause between requests
so the scan stays polite. Green check means it worked.

### 5. Open the board

`board.html` is the list you actually work from, and it is meant to be read
in Claude rather than on GitHub. Ask Claude to publish it and you get a
private page with every open posting on it, each title a link straight to
the employer's application:

https://claude.ai/code/artifact/6155f927-5fcc-4299-a1e7-fc82e164383d

**Every open posting stays on that page until you mark it.** Not until it
stops being new — until *you* tick **Applied** or **Not relevant** on it.
That is the difference from what this used to do.

`DIGEST.md` is the same information rendered for GitHub, and `digest.html`
is a desktop copy. Both still work; neither is where the ticking happens.

### 6. Nothing to set up for email

There isn't any. An earlier version opened a GitHub issue and assigned it
to you, because GitHub emails you on assignment and that needed no SMTP
password. It was removed, and the reason is the point of the board:

An alert can only carry what is **new**, or it repeats itself three times a
day until you stop reading it. So it arrived with one or two postings in it
— whatever the last scan happened to turn up — and a job you did not act on
that morning was never put in front of you again. It was still in the
ledger, under a couple of hundred rows, which is not the same as being
shown to you.

The board carries the standing list instead. It can afford to, because it
is a page you open rather than a message you receive, and because nothing
on it repeats: what you have ticked off is gone from it for good.

---

## Using it

Three scans a day, at 7am, 1pm and 7pm Pacific.

**Work the board.** The first section, *Worth applying to now*, is the one
that matters: Level I roles and postings with no experience requirement.
That is usually a couple of dozen out of a few hundred tracked. Below it
are *Requirements unclear* — shown on purpose, because a job you cannot
rule out is not a job to hide — and *Experience required*, folded away.

Every title is a link to the employer's own application page. Search and a
drive-time filter are at the top; the drive filter is the one to reach for
first, since it is the only thing on the page you cannot change.

Under each job title is a line of detail the posting itself states — the
facility, the kind of nursing, full-time or per diem, the shift, the pay:

> **RN - On Call**
> Medical Hill Healthcare Center · Skilled nursing · On-call / Per diem ·
> AM / PM / NOC · $51.00–$52.50/hr

That line exists because the title often doesn't say anything. Post-acute
employers in particular title a dozen different jobs "RN", and a list of
identical titles can't be triaged without opening every one of them. The
same rule applies to it as to the requirement quote: **everything on that
line is stated by the posting.** A blank where the shift should be means
the posting never said, not that it's flexible.

Postings that require acute-care experience are hidden entirely. They are
the one thing suppressed, and they are suppressed because a false "no
experience required" costs you an application you were never eligible for.

**Ticking a posting off.** Each one has two buttons.

* **Not relevant** — wrong shift, wrong setting, too far, already know the
  place. It leaves the list and goes into a *Not relevant* fold at the
  bottom with a **Put it back** button, in case you were too quick.
* **Applied** — moves it to *In progress* at the top, where it stays
  whatever happens to the posting afterwards.

Nothing else takes a posting off the board. It will still be there next
week, and the week after, until you tick it. That is deliberate: the thing
this tool was getting wrong was showing you a job once.

Your ticks are saved to the page itself, so they survive a republish and
they are there on your phone and your laptop both. They are not yet in the
ledger — **ask Claude to fold the board's marks into `applications.csv`**
and it reads them back and runs `sync_board.py`. The ledger stays the
record; the board is where you decide.

You can still set **Status** in `applications.csv` by hand, from a phone or
a desktop, and it wins over the board:

| Set Status to | What happens |
|---|---|
| `applied`, `pending`, `interviewing`, `offer` | Moves to **In progress**, off every list above |
| `rejected`, `declined`, `withdrawn` | Moves to **Closed out** |
| `not relevant` | Moves to **Not relevant**, and never comes back as a new job |
| `unapplied` | Comes back to the main lists |

A row you have already decided about by hand is never overwritten by a
tick — the board may have been open for days, and the ledger is newer.

Capitals and stray spaces don't matter. A value it doesn't recognise is
treated as `unapplied` and the job stays on the main list — a typo should
show you a job again, never swallow one.

**An application in progress is remembered even after the posting comes
down.** That matters more than it sounds: a job you applied to is among the
likeliest to disappear, because the employer fills it or pulls it while
they interview. *In progress* is built from your ledger, not from what the
scan found today, so the row stays put and tells you `not listed since
2026-08-30` instead of silently vanishing.

The **Since** column is your `Applied On` where you filled it in. If you
didn't — and typing a date into a CSV on a phone is exactly the step that
gets skipped — it falls back to the date the scanner first saw the row
marked, which it records itself in `Marked active`.

The scanner **never** touches Status, Applied On, or Notes. It only adds new
rows and refreshes employer-controlled fields. A posting that disappears from
the employer's site gets marked `closed` rather than deleted, and only if you
hadn't applied to it — your applications are never overwritten.

---

## Read the evidence, not the label

Every row shows the requirement sentence its verdict is based on.

This matters. The classifier was confidently wrong six separate times while
being built — it labeled a posting "no experience required" while quoting
"2 years of recent relevant experience" as its own evidence. Each time, the
quote is what caught it.

If a quote doesn't support its label, the rule is wrong. Tell me and I'll fix
it.

Every one of those failures is now a case in `test_rules.py`, which the
workflow runs before each scan. If a rule change reintroduces one, the run
fails instead of quietly recommending a job you can't get. Run it yourself
with `python3 test_rules.py` after editing `classifier.py` or `geo.py`.

The most recent batch, found by checking labels against their own evidence:

* County postings write "One (1) year". The parenthetical sits between the
  number and the unit, and the duration pattern required them adjacent — so
  it matched nothing, and six RN roles demanding a year of acute-care
  experience came back as "no experience required".
* Labels that are also ordinary words were matched mid-sentence. "Previous
  acute care experience is strongly preferred" was split at the word
  "experience", making the requirement into a heading and leaving evidence
  that supported nothing.
* An acute-care requirement stating no duration and never saying "required"
  counted as no requirement at all — which is how a Sutter posting reached
  the recommendations quoting "Previous experience as an RN in an acute care
  hospital setting" as its evidence for needing no experience.

---

## Optional: turn on the VA

USAJOBS covers VA Palo Alto, Martinez, Mather and San Francisco. Federal
hiring is slow but new-grad friendly and doesn't weight prior rotations.

1. Request a free key at https://developer.usajobs.gov/apirequest/
2. **Settings → Secrets and variables → Actions → New repository secret**
3. Add `USAJOBS_KEY` (the key) and `USAJOBS_EMAIL` (the address you registered)

Check the first run's output carefully. This adapter was written from the
documented schema and never tested against live data, so the field mapping
may need correcting.

---

## What's covered

| Source | Status |
|---|---|
| Sutter Health | Working — Workday, full requirement text |
| Alameda Health System | Working |
| PACS Group (post-acute) | Working — 70 facilities geolocated via CMS data |
| El Camino Health | Working |
| John Muir Health | Working |
| Kindred / ScionHealth (LTAC) | Working — no staff RN roles open today |
| Contra Costa County | Working — NEOGOV |
| Solano County | Working — NEOGOV |
| Marin County | Working — NEOGOV |
| Napa County | Working — NEOGOV |
| City of Berkeley | Working — NEOGOV |
| City of Oakland | Working — NEOGOV |
| Kentfield (Vibra, LTAC) | Working — JIBE JSON API, no Kentfield roles open today |
| San Francisco DPH + citywide | Working — SmartRecruiters open API |
| St. Rose Hospital, Hayward | Working — Smart Hires, whole board in one GET |
| USAJOBS / VA | Needs a key, untested |
| CalCareers / CDCR | Blocked — DevExpress AJAX callbacks, needs a headless browser |

Three of those moved out of "blocked" or "not built" without a headless
browser, because the original read was of the wrong page:

* **NEOGOV** (`governmentjobs.com`) looks client-rendered from every angle —
  `/careers/{agency}/jobs` serves a 976-byte shell, the agency root serves
  204 KB of Knockout scaffolding with no postings in it, there is no JSON
  API and `/jobs/rss` returns HTML. But the agency root *does* render the
  listing server-side for a caller that sends `X-Requested-With:
  XMLHttpRequest`. No browser, no session.
* **Vibra/Kentfield** was judged from the marketing site. The careers
  subdomain runs JIBE, which has an open JSON API at `/api/jobs` that
  returns full descriptions inline and filters by state server-side.
* **St. Rose Hospital** was not blocked, it was simply never looked for.
  It is independent — not part of Sutter, John Muir, Alameda Health or a
  county — so no adapter reached it, and it runs on Smart Hires, an ATS
  none of the other adapters speak. Its board pages, filters and sorts
  itself with DWR calls after load, which makes it look like an app, but
  the table arrives complete in the first response. One GET is the whole
  board.
* **San Francisco** runs SmartRecruiters, whose API is open and
  unauthenticated. The only hard part is the company identifier: every
  sensible spelling returns HTTP 200 with `totalFound: 0`, which reads as
  an empty job board rather than a wrong name. The real one,
  `CityAndCountyOfSanFrancisco1`, is in an apply link on careers.sf.gov.
  If that adapter ever reports zero, check the identifier before the API.

CalCareers is genuinely blocked: it is ASP.NET WebForms and renders its
results grid through DevExpress AJAX callbacks, so a `__VIEWSTATE` POST
returns a page with no jobs in it.

Kaiser Permanente and Stanford are excluded by request, including from the
aggregator fallback.

---

## When something breaks

It will. These are mostly undocumented endpoints that change without notice.

**A source reports 0 listings.** Its adapter broke. The scan continues and
the log line names the source.

**Actions fails red.** Open the run, read the step that failed. Most often
it's step 3 above — write permissions not enabled.

**The board is showing an old scan.** It is a page, not a feed — it shows
whatever was last published to it. Ask Claude to refresh it; the date under
the title is the scan the page was built from.

**A posting you ticked off is back on the board.** Its mark did not reach
`applications.csv`, so the next publish rebuilt the page without it. Ask
Claude to fold the marks in, then refresh.

**A job appears in the wrong drive-time bucket.** Add the city to `geo.py`,
in `IN_CITIES` or `OUT_CITIES`. Whole city names only, never split on spaces
— splitting is what once put "Sutter Creek" into the out-of-range set and
quietly rejected every job in Walnut Creek.

**A new PACS facility shows up under "Location needs checking."** Look it up
in the CMS dataset and add it to `pacs_facilities.json`. Don't guess from the
name: East Bay Post Acute is in Castro Valley, not Oakland.

**A posting shows a street address instead of a city.** Some employers
publish no city at all — John Muir posts its Tice Valley roles as bare
"1914 Tice Valley Blvd", and its detail feed has no city either. Add the
street name to `LANDMARKS` in `geo.py`. Digits are stripped before
matching, so key on the street name only, and look the address up rather
than guessing from the name.

**A county source suddenly returns far fewer jobs than it claims.** The
NEOGOV listing reports its own total — "75 Job Postings found". If the
scan brings back fewer, the `SORT` parameter in the `NeoGov` adapter has
stopped working. Without a fixed sort the server reorders between
requests, later pages repeat rows you already have, and the tail is never
served at all. That failure is silent: no error, just fewer jobs.

To test a change without waiting on the schedule, use **Run workflow** on the
Actions tab. `python3 run_scan.py --quick` skips detail fetches and runs in
about a minute, but doesn't analyse requirements.
