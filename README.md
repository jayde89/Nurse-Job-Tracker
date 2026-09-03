# RN job scanner

Scans six employer career systems three times a day for staff RN openings
within two hours of Oakland, reads each posting's actual requirements, and
hides the ones that require acute-care experience.

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
run_scan.py
pacs_facilities.json
applications.csv
state/seen.json
README.md
```

`applications.csv` and `state/seen.json` carry the current scan. Uploading
them means your first automated run reports only genuinely new postings
instead of all 157.

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

Open `DIGEST.md` in the repo. That page is your dashboard — GitHub renders
it properly on a phone, inside a private repo, free. Bookmark it.

`digest.html` is the same content, nicer looking, for desktop. Download and
open it locally; GitHub won't render HTML from a repo.

---

## Using it

Three scans a day, at 7am, 1pm and 7pm Pacific.

**Read `DIGEST.md`.** The first section, *Worth applying to now*, is the one
that matters: Level I roles and postings with no experience requirement.
Today that's 18 of 157.

The other 139 are in *Not applied yet* on purpose. They require experience
you don't have yet. They're there so you can watch them, not so you apply to
them.

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

## Read the evidence, not the label

Every row shows the requirement sentence its verdict is based on.

This matters. The classifier was confidently wrong six separate times while
being built — it labeled a posting "no experience required" while quoting
"2 years of recent relevant experience" as its own evidence. Each time, the
quote is what caught it.

If a quote doesn't support its label, the rule is wrong. Tell me and I'll fix
it.

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
| Kindred / ScionHealth (LTAC) | Working — no RN roles open today |
| USAJOBS / VA | Needs a key, untested |
| CalCareers / CDCR | Blocked — needs a headless browser |
| Kentfield (Vibra, LTAC) | Blocked — needs a headless browser |
| Contra Costa, Solano, SFDPH, others | Not built yet |

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

To test a change without waiting on the schedule, use **Run workflow** on the
Actions tab. `python3 run_scan.py --quick` skips detail fetches and runs in
about a minute, but doesn't analyse requirements.
