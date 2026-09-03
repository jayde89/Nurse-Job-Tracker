# RN job scanner

Scans fifteen employer and public-agency career systems three times a day
for staff RN openings within two hours of Oakland, reads each posting's
actual requirements, and hides the ones that require acute-care experience.

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
digest_page.py
geo.py
notify.py
run_scan.py
test_rules.py
pacs_facilities.json
applications.csv
state/seen.json
README.md
```

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

### 5. Bookmark the digest

`digest.html` is the dashboard. It is a real page, not a table: a headline
count of what you can actually apply to today, tabs for *Apply now / New /
Everything / Applied*, a search box, drive-time filters, and a reading mode
sized for a phone. Every posting still carries the requirement sentence its
verdict rests on, one tap away.

The whole thing is one file with nothing loaded from the internet — no
fonts, no scripts, no images — so it opens anywhere, including with the
plane's wifi off.

`DIGEST.md` is still written every run. It is the fallback: GitHub renders
Markdown inside a private repo on a phone for free, with no download step.
It is a wall of table, which is exactly why `digest.html` exists.

See **Reading it on a phone** below for how to get the HTML onto one.

### 6. Check that the email reaches you

The scan emails you when it finds a posting that is both new and one you
could apply to today. It does this by opening an issue and assigning it to
you; GitHub emails you on assignment, so there is no password to set up and
nothing to configure. It also shows up in the GitHub mobile app.

Confirm two things once:

* **github.com/settings/notifications** → Email is ticked under
  "Subscriptions", and "Notifications for assigned issues" is on.
* The address GitHub has for you is one you actually read —
  **github.com/settings/emails**.

You will not get an email on every scan, by design. Three a day of the same
list is how an alert stops being read. Quiet means nothing new you can
apply to; the standing list is always in `DIGEST.md`.

If you would rather have the digest in your own inbox from your own
address, that needs SMTP credentials in repo secrets — a Gmail App Password
and a mail action in the workflow. The issue route was chosen because it
needs neither.

---

## Using it

Three scans a day, at 7am, 1pm and 7pm Pacific.

**Open `digest.html`.** It lands on *Apply now*: Level I roles and postings
with no experience requirement, nearest drive time first. That is usually a
couple of dozen out of a few hundred tracked, and it is the only tab you
need on a normal day.

The other tabs are there on purpose. *Everything* holds the roles that
require experience you don't have yet — to watch, not to apply to. Postings
that require acute-care experience are hidden entirely, and the page says
how many.

Three things worth knowing about the page:

* **Why it is labelled that** under each posting opens the requirement
  sentence the verdict rests on. Read it before you trust the label.
* **Reading mode** (top right) drops the filters, sets the type in serif,
  opens every quote, and gives you A− / A+ for size. It is the mode for
  going through the list on a phone rather than hunting a specific job.
* Your tab, filters, reading mode and text size are remembered in the
  browser, so it opens where you left it.

*Apply now* on `DIGEST.md` is the same list, if you would rather read it on
GitHub.

**When you apply**, open `applications.csv`, find the row, change **Status**
from `unapplied` to `applied`. Commit. On the next scan it moves to
*Applications pending*.

You can do this from your phone: tap the file, tap the pencil icon, edit,
commit. It's slightly fiddly but it keeps everything in one place with full
history.

The scanner **never** touches Status, Applied On, or Notes. It only adds new
rows and refreshes employer-controlled fields. A posting that disappears from
the employer's site gets marked `closed` rather than deleted, so anything you
already applied to keeps its record.

---

## Reading it on a phone

The repo is private, and GitHub does not render HTML from a repo or serve
free Pages from a private one. So the page has to get to the phone some
other way. In rough order of how little work they are:

**Save it once, re-save it when you want fresh numbers.** Open the repo in
Safari, tap `digest.html`, tap **Raw**, then Share → *Add to Home Screen*.
It behaves like an app, opens without Safari's chrome, and works offline.
It is a snapshot: repeat when you want the latest scan. Fine if you check
in once a day.

**Mail it to yourself each run.** The workflow already opens an issue when
there is something new worth applying to. Attaching `digest.html` to that
mail is a few lines in `.github/workflows/rn-scan.yml`, and then the current
page is always the newest thing in your inbox.

**Give it a real URL.** Two ways:

* **GitHub Pages on this repo.** Settings → Pages → deploy from `main`,
  root. Needs GitHub Pro (about $4/month) while the repo is private. Then
  the page is live at a fixed address within a minute of each scan, and
  *Add to Home Screen* on that URL is always current.
* **A second, public repo holding only the page.** Job postings are public
  information; your `applications.csv` — the Status, Applied On and Notes
  you write — is not, and stays here. The workflow pushes `digest.html`
  there, Pages serves it free. More moving parts, no monthly cost.

Pages is the one to pick if the $4 is not the deciding factor: one setting,
nothing to maintain, and nothing of yours leaves the private repo.

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
