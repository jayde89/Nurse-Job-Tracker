# Every RN employer inside the ring, and whether this scan can see it

Written 2026-09-09, after the user asked what we might still be missing and
named Stockton and Sonoma. It is the answer to the question CLAUDE.md says
to ask first — *which places inside two hours employ nurses?* — rather than
the question that keeps missing things, *which systems are we missing?*

Four words are used below and they mean exactly this:

- **read** — an adapter fetches this employer's own board every scan.
- **blocked** — the board was tried and cannot be read without a browser.
  Each one says what stopped it, so nobody re-probes it for nothing.
- **gap** — inside the ring, reachable in principle, not read yet.
- **excluded** — Kaiser Permanente and Stanford, by the user's request.

A hospital being *read* is not a promise that it has open RN roles today.
It is a promise that if it posts one, this scan sees it.

---

## Acute-care hospitals

### Alameda

| Facility | City | Drive | Read by |
|---|---|---|---|
| Highland Hospital, Alameda Hospital, San Leandro Hospital, Fairmont, John George Psychiatric | Oakland / Alameda / San Leandro | <30 | Alameda Health System (HealthcareSource) |
| Alta Bates Summit | Berkeley / Oakland | <30 | Sutter (Workday) |
| Eden Medical Center | Castro Valley | <30 | Sutter (Workday) |
| UCSF Benioff Children's | Oakland | <30 | UCSF (Oracle) |
| St. Rose Hospital | Hayward | <30 | Smart Hires |
| Kindred Hospital SF Bay Area (LTAC) | San Leandro | <30 | ScionHealth |
| **Washington Hospital Healthcare System** | Fremont | 30-60 | **blocked** — 403 on whhs.com with full browser headers |
| Stanford Health Care Tri-Valley | Pleasanton | 30-60 | excluded |
| Kaiser (Oakland, Richmond, Fremont, San Leandro) | — | <30 | excluded |

### Contra Costa

| Facility | City | Drive | Read by |
|---|---|---|---|
| John Muir Health | Walnut Creek / Concord | <30 | Workday |
| Contra Costa Regional Medical Center | Martinez | <30 | Contra Costa County (NEOGOV) |
| Sutter Delta | Antioch | 30-60 | Sutter (Workday) |
| San Ramon Regional | San Ramon | 30-60 | Tenet (Oracle) |

### San Francisco

| Facility | City | Drive | Read by |
|---|---|---|---|
| UCSF Parnassus, Mission Bay, Mount Zion, Bayfront | SF | <30 | UCSF (Oracle) |
| **UCSF Health Saint Francis and St. Mary's** | SF | <30 | UCSF (Oracle) — see note below |
| Zuckerberg SF General, Laguna Honda | SF | <30 | City & County of SF (SmartRecruiters) |
| CPMC | SF | <30 | Sutter (Workday) |
| **Chinese Hospital** | SF | <30 | **gap** — runs no recognisable ATS |
| SF VA Medical Center | SF | <30 | USAJOBS adapter, **not running** (no API key) |

*The open question in CLAUDE.md — whether UCSF now carries the two former
Dignity hospitals — is settled. UCSF completed the purchase in August 2024;
Saint Francis is now UCSF Health Stanyan Hospital. Their postings are on the
UCSF board under a generic "San Francisco, CA" with no facility field, which
is why they are invisible as names. SF has no hole.*

### San Mateo

| Facility | City | Drive | Read by |
|---|---|---|---|
| Mills-Peninsula | Burlingame | 30-60 | Sutter (Workday) |
| Sequoia Hospital | Redwood City | 30-60 | CommonSpirit (Radancy) — 25 listings |
| San Mateo Medical Center | San Mateo | 30-60 | County of San Mateo (NEOGOV) |
| **Seton Medical Center + Seton Coastside** | Daly City / Moss Beach | 30-60 | **added this pass** — AHMC Healthcare on iCIMS. Six STAFF NURSE I roles open on the day it was added |
| Menlo Park VA | Menlo Park | 30-60 | USAJOBS, not running |

### Santa Clara

| Facility | City | Drive | Read by |
|---|---|---|---|
| Santa Clara Valley Medical Center, O'Connor, Saint Louise | San Jose / Gilroy | 60-90 | County of Santa Clara (NEOGOV) |
| El Camino Health | Mountain View / Los Gatos | 30-60 | Workday |
| **Good Samaritan, Regional Medical Center** | San Jose | 60-90 | **blocked** — HCA, Cloudflare interstitial on every path including robots.txt |
| Stanford Health Care, Lucile Packard | Palo Alto | 30-60 | excluded |

### Marin

| Facility | City | Drive | Read by |
|---|---|---|---|
| MarinHealth Medical Center | Greenbrae | 30-60 | Workday |
| Kentfield Hospital (LTAC) | Kentfield | 30-60 | Vibra (JIBE) |
| Novato Community | Novato | 30-60 | Sutter (Workday) |
| Marin County health services | San Rafael | 30-60 | NEOGOV |

*Kentfield's city was not in the geo table until this pass, so its postings
could never be ranked — an LTAC the user asked for by name, read by an
adapter that already worked, landing in the review bucket every scan.*

### Napa and Solano

| Facility | City | Drive | Read by |
|---|---|---|---|
| Queen of the Valley | Napa | 60-90 | Providence (Oracle) — 22 listings |
| Adventist Health St. Helena | Saint Helena | 90-120 | Adventist (Oracle) — 68 listings |
| NorthBay Medical Center, VacaValley | Fairfield / Vacaville | 30-60 | NorthBay (Oracle) |
| Sutter Solano | Vallejo | 30-60 | Sutter (Workday) |
| Solano County, Napa County | Fairfield / Napa | 30-60 | NEOGOV |
| **Napa State Hospital** (Dept of State Hospitals) | Napa | 60-90 | **blocked** — CalCareers |
| **California Medical Facility / CSP-Solano** | Vacaville | 30-60 | **blocked** — CalCareers (CCHCS) |
| **Veterans Home of California** | Yountville | 60-90 | **blocked** — CalCareers (CalVet) |

### Sonoma — the user asked about this one

| Facility | City | Drive | Read by |
|---|---|---|---|
| Santa Rosa Memorial | Santa Rosa | 90-120 | Providence (Oracle) — 37 listings |
| Petaluma Valley | Petaluma | 60-90 | Providence (Oracle) — 12 listings |
| Providence Healdsburg | Healdsburg | 90-120 | Providence (Oracle) — 12 listings |
| Sutter Santa Rosa Regional | Santa Rosa | 90-120 | Sutter (Workday) |
| **Sonoma Valley Hospital** | Sonoma | 60-90 | **added this pass** — iCIMS |
| **Sonoma Specialty Hospital (LTAC, 37 beds)** | Sebastopol | 90-120 | **added this pass** — Paylocity |
| County of Sonoma health services | Santa Rosa | 90-120 | NEOGOV |
| Kaiser Santa Rosa | Santa Rosa | 90-120 | excluded |

Two of the county's hospitals were reaching nobody. Sonoma Valley Hospital is
a district hospital belonging to no system, so no system-level adapter could
find it. Sonoma Specialty Hospital is **the fifth LTAC inside the ring** — the
repo believed there were four — and it had two RN roles open on the day it was
added, on a Paylocity board this scan already knew how to read.

### San Joaquin — the user asked about this one too

| Facility | City | Drive | Read by |
|---|---|---|---|
| San Joaquin General | French Camp | 60-90 | San Joaquin County (JobAps) |
| St. Joseph's Medical Center, St. Joseph's Behavioral | Stockton | 60-90 | CommonSpirit (Radancy) — 35 listings |
| Sutter Tracy Community | Tracy | 30-60 | Sutter (Workday) |
| Adventist Health Lodi Memorial | Lodi | 60-90 | Adventist (Oracle) — 74 listings |
| Doctors Hospital of Manteca | Manteca | 60-90 | Tenet (Oracle) — 9 listings |
| **Dameron Hospital (200 beds)** | Stockton | 60-90 | **blocked** — Paycom board renders client-side; no JSON endpoint answers |
| **California Health Care Facility** | Stockton | 60-90 | **blocked** — CalCareers (CCHCS) |
| Telecare programs | Stockton | 60-90 | **added this pass** — UKG |
| Kaiser Manteca, Stockton | — | 60-90 | excluded |

An Indeed cross-check of "registered nurse" near Stockton returned ten
postings: Tenet, San Joaquin County, Sutter and Adventist — every one already
read — plus two private outfits too small to have a board (a dialysis
contractor and an infusion agency). Stockton's hospital coverage is complete
except for Dameron.

### Stanislaus

| Facility | City | Drive | Read by |
|---|---|---|---|
| Doctors Medical Center | Modesto | 60-90 | Tenet (Oracle) — 59 listings |
| Emanuel Medical Center | Turlock | 90-120 | Tenet (Oracle) — 29 listings |
| Memorial Medical Center | Modesto | 60-90 | Sutter (Workday) |
| Central Valley Specialty Hospital (LTAC) | Modesto | 60-90 | Paylocity |
| **Oak Valley Hospital** | Oakdale | 90-120 | **gap** — district hospital, no ATS found on its careers page |
| **Stanislaus Surgical Hospital** | Modesto | 60-90 | **gap** — small, unexamined |
| **Stanislaus County** health services | Modesto | 60-90 | **gap** — platform unverified; do not guess the slug |

### Sacramento, Yolo, Placer, El Dorado

| Facility | City | Drive | Read by |
|---|---|---|---|
| Sutter Sacramento, Roseville, Davis, Center for Psychiatry | — | 60-90 | Sutter (Workday) |
| Mercy, Methodist, Woodland, Mercy Merced | — | 60-90 | CommonSpirit (Radancy) — 63 Sacramento, 14 Woodland, 22 Merced |
| Sacramento County health services | Sacramento | 60-90 | NEOGOV |
| **Marshall Medical Center** | Placerville | 90-120 | **added this pass** — Workday |
| **UC Davis Medical Center** | Sacramento | 60-90 | **blocked** — PeopleSoft/UCPath at careerspub.universityofcalifornia.edu/psp/ucdavis, which redirects every path to a cookie-gated login. The UC systemwide board carries no UCDMC rows and careers.ucdavis.edu answers 403 |

### Santa Cruz, Monterey, San Benito

| Facility | City | Drive | Read by |
|---|---|---|---|
| Dominican Hospital | Santa Cruz | 60-90 | CommonSpirit (Radancy) |
| Salinas Valley Health | Salinas | 90-120 | Workday |
| Natividad | Salinas | 90-120 | County of Monterey (NEOGOV) |
| Telecare Santa Cruz | Santa Cruz | 60-90 | **added this pass** — UKG |
| **Watsonville Community Hospital** | Watsonville | 90-120 | **gap** — ADP Workforce Now; its short board redirects in a loop to a non-browser caller |
| **Community Hospital of the Monterey Peninsula** | Monterey | 90-120 | **gap** — Montage Health, 403 |
| **Hazel Hawkins Memorial** | Hollister | 90-120 | **gap** — a Recruiterbox/Trakstar widget injected by JS; the company slug is not in the page |

---

## The tier that is not hospitals

This is where the honest gap is, and it is the tier the user's own criteria
point at: *basic RN experience that is not acute care* describes a skilled
nursing, clinic, behavioural-health or dialysis job, not a hospital one.

| Setting | Read | Not read |
|---|---|---|
| Skilled nursing | PACS Group (112 CA facilities) | Ensign / Pennant, Windsor, Covenant Care, Generations, Aspen, Sun Mar and every other chain |
| Behavioural health | **Telecare (added this pass)** — Oakland, San Leandro, San Jose, Stockton, Ceres, Santa Cruz | Crestwood Behavioral Health (Vallejo, Fremont, Pleasant Hill, Sacramento, Stockton), Aurora Santa Rosa (Signature Healthcare), Bay Area Community Services |
| Dialysis | nothing | Satellite Healthcare now points at careers.usrenalcare.com, which is iCIMS behind a vanity domain; the portal host answers but serves a 148-byte stub to `/jobs/search`, so the adapter that reads Sonoma Valley and Seton does not read this one yet. DaVita and Fresenius unexamined |
| Hospice and home health | nothing | Suncrest, VITAS, Amedisys, Bayada |
| Community clinics / FQHC | **La Clínica de La Raza (added this pass)** — Oakland, San Leandro, Union City, Concord, Pittsburg, Oakley, Vallejo | LifeLong Medical (Berkeley), Petaluma Health Center (Jobvite), Santa Rosa Community Health, West County Health Centers, Golden Valley Health Centers, Community Medical Centers Stockton (Paycom, blocked), Native American Health Center, Sonoma County Indian Health Project |
| Correctional health | Alameda County and the other county boards | CCHCS (blocked — CalCareers), Wellpath |
| Federal | — | VA — the adapter exists and has never run; it needs `USAJOBS_KEY` and `USAJOBS_EMAIL` as repo secrets |

An Indeed cross-check near Santa Rosa makes the shape of it plain: of ten RN
postings, three were Providence, one Sutter, one Kaiser (excluded) — and the
other five were a dialysis chain, a hospice, a skilled nursing facility, a
tribal health centre and a behavioural-health provider. Five of ten in a
county where we read every hospital.

---

## Blocked, and what each would take

| What | Why | What it needs |
|---|---|---|
| HCA (Good Samaritan, Regional Medical Center San Jose) | Cloudflare interstitial on every path | a real browser |
| Washington Hospital, Fremont | 403 on whhs.com | a real browser |
| CalCareers — CCHCS, Napa State Hospital, CalVet Yountville | ASP.NET WebForms behind DevExpress callbacks; a `classid` GET returns 550 KB with zero job rows in it | Playwright in the workflow |
| Paycom boards — Dameron Hospital, Community Medical Centers | the board is a React app; no `/api/*` path answers and the loader bundle carries no endpoint | a real browser |
| Chinese Hospital, SF | no recognisable ATS at all | a person to find where they post |

CalCareers is the single highest-value blocked item, because it is not one
employer: it is state prison health, the state hospitals and the veterans
home, all of which hire new-graduate RNs, and two of them are inside the ring
at Vacaville and Napa.

---

## What the second pass changed

Added: **Seton Medical Center** (Daly City, 30 minutes, iCIMS — six STAFF
NURSE I roles open) and **La Clínica de La Raza** (HRMDirect — Registered
Nurse I/II in Oakland and Concord). Seton was the nearest unread hospital
in the ring; La Clínica is the first community clinic this scan has ever
read, and clinic nursing is the tier the user's own criteria point at.

Eight adapters were flattening HTML to spaces, which merged bulleted
requirements into run-ons — the bug CLAUDE.md describes with Adventist,
live in the largest source here. Re-reading the verdicts afterwards found
five more places where a label rested on a quote that did not support it,
including two that mattered on their own terms: a "Registered Nurse I/II"
posting asking for two to three years was being labelled new-graduate
from its title, and a San Francisco posting that accepts clinic
experience was being suppressed as acute-required.

## What this pass changed

Added: Sonoma Valley Hospital (iCIMS), Sonoma Specialty Hospital (Paylocity),
Telecare (UKG), Marshall Medical Center (Workday), Alameda County (JobAps).

Fixed, all of which were losing things quietly:

- JobAps read 88 of San Joaquin County's 98 rows, because it keyed on a class
  the county writes on its main table and not on its departmental ones.
- JobAps postings were being classified from the whole page — the description
  opened with the site's own navigation menu — because the container it
  looked for exists on neither agency.
- The geo table did not know Kentfield, or 90 other places inside the ring.
- "Post-acute" and "sub-acute" matched the acute-care marker, which suppressed
  a staff RN posting whose only requirement was one year of *non*-acute
  experience — the exact thing the user's criteria say to show.
- 30 of the 33 rows in the review bucket were one Texas posting repeated.
  A location whose last segment names another state is now out of range
  without a city lookup.
