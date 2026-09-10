"""
RN Job Scanner — source adapters.

Ten adapters, all verified against live endpoints 2026-09-03:

  WorkdayCXS     — native Workday tenants. One class, N tenants.
                   John Muir, Sutter, El Camino.
  SutterPhenom   — Sutter's Phenom front-end. Kept for reference; Sutter is
                   now read through its real Workday tenant instead, because
                   Phenom returned 320-char marketing teasers with no
                   requirements section.
  PACS           — post-acute / skilled nursing, 70 facilities geolocated.
  ScionHealth    — Kindred LTAC.
  HealthcareSource — Alameda Health System.
  NeoGov         — governmentjobs.com. Six CA county and city agencies.
  Jibe           — Vibra / Kentfield, via the JSON API behind their JIBE site.
  SmartRecruiters — San Francisco DPH and citywide, via the open SR API.
  SmartHires     — St. Rose Hospital, Hayward. Independent, so no other
                   adapter reached it, and the only source on this ATS.
  USAJobs        — VA. Needs a key; still untested against live data.

Design notes that came out of probing the live endpoints:

  * Do NOT trust the sources' own search. John Muir titles its postings
    "RN - ...", so searchText="registered nurse" returns 9 of 32 jobs.
    Sutter's relevance ranking returned a CT Technologist as the top hit
    for "registered nurse". Both are cheap to pull in full, so we pull
    broad and filter locally.
  * Do NOT trust Workday's job-family facets. John Muir tags only 5
    postings "Nursing" while 6 match on title alone.
  * Two stages. Listing pages are cheap; detail pages are not, and every
    detail page you fetch is also tokens you pay the classifier to read.
    Prefilter on title first, fetch detail only for survivors.

Run directly to test:  python3 adapters.py
"""

from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone

import geo

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Be a good citizen. Three scans a day at this rate is invisible to them.
REQUEST_DELAY_SEC = 1.0
TIMEOUT_SEC = 30
MAX_RETRIES = 3


# ── normalized record ────────────────────────────────────────────────

@dataclass
class Posting:
    employer: str
    req_id: str
    title: str
    location: str
    url: str
    posted_date: str | None = None      # ISO-8601 where the source gives one
    description: str = ""               # populated only after fetch_detail
    department: str | None = None
    schedule: str | None = None
    shift: str | None = None
    # What kind of nursing this is — set by the adapter, which knows what
    # sort of employer it is reading, never mined out of the body text.
    # "Skilled nursing experience preferred" in a hospital posting would
    # otherwise relabel an ED job as a nursing home.
    setting: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    # populated by geo.partition()
    drive_time_bucket: str | None = None
    straight_line_mi: float | None = None
    geo_verdict: str | None = None
    source_adapter: str = ""
    scraped_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def key(self) -> str:
        """Stable dedupe key. req_id is unique within an employer."""
        return f"{self.employer}::{self.req_id}"


# ── title prefilter ──────────────────────────────────────────────────
# Cheap gate before the LLM classifier ever sees a posting. Deliberately
# loose on the include side — the classifier makes the real call. The
# exclusions are the ones that are unambiguous from the title alone.

# Bare "nurse" is included on purpose. Naming only the common phrasings
# ("staff nurse", "clinical nurse", ...) looks safe and isn't: it silently
# dropped "Ambulatory Services Nurse I, PreOp & PACU" — the one Level I
# role on Sutter's board, in range at Mountain View, with no experience
# section at all. The classifier was written for that exact posting (it is
# named in classifier.py's docstring and in TITLE_LEVEL_I) but never saw
# it, because this gate ran first. Sutter also writes "Clinic Nurse II",
# "Hospice Nurse II" and "Ambulatory Services Nurse II" — none of which
# contain any of the listed phrasings either. Let the title in and make
# EXCLUDE_TITLE do the rejecting, which is what the comment above already
# says this filter is for.
INCLUDE_TITLE = re.compile(
    r"\b(RN|R\.N\.|registered nurse|staff nurse|clinical nurse|nurse resident"
    r"|new grad(uate)?|graduate nurse|nurse)\b", re.I)

# Spelled-out "Licensed Vocational Nurse" and "Nurse Assistant" (as
# distinct from "Nursing Assistant") are here because loosening the include
# side above admits them: Sutter posts "Licensed Vocational Nurse II,
# Urology" and "Nurse Assistant - Oncology", neither of which contains the
# LVN acronym or the word "nursing".
# "licensed vocation nurse" with no "al" is not a typo worth ignoring:
# CommonSpirit posts it that way in Sacramento, and the spelled-out form
# was the only thing keeping LVN roles out once the include side loosened.
# "informaticist" is the noun form of the "informatics" already here.
# The senior and non-bedside words below were added 2026-09-09 at the
# user's explicit instruction, after an audit of one scan found 36 of 197
# shown rows were charge, lead, coordinator, navigator, consultant or
# specialist roles — none of which a new graduate is hired into, and one
# of which ("RN Education Program Site Coordinator Experienced") the
# classifier had labelled "Level I / new grad" while its own title said
# Experienced.
#
# This is a deliberate narrowing of a filter the rest of this file keeps
# deliberately loose, so do not "fix" it back: the user asked for it in as
# many words, the same way the graded Level II rule was asked for. Each
# word here is unambiguous from the title alone, which is the standard
# this filter holds itself to. "Charge" and "lead" are the supervisory
# rungs above a new grad; "coordinator", "navigator" and "consultant" are
# roles staffed from experienced nurses; "specialist" generalises the
# "clinical nurse specialist" that was already here.
EXCLUDE_TITLE = re.compile(
    r"\b(LVN|LPN|licensed voc(?:\.|ationa?l?)?\s+nurse|licensed practical nurse"
    r"|nursing assistant|nurse assistant|medical assistant|nurse practitioner"
    r"|CRNA|nurse anesthetist|clinical nurse specialist"
    r"|manager|director|supervisor|educator|informatics|informaticist|analyst"
    r"|charge|lead|coordinator|navigator|consultant|specialist|preceptor"
    r"|travel|per[- ]diem agency|locum"
    r"|student|intern|volunteer|extern)\b", re.I)

# Credential acronyms that collide with things that aren't the job's role.
# John Muir suffixes postings with the bargaining unit, so
#   "RN - CMC Emergency Services - Part Time - 12 Hour - Nights - CNA"
# is a staff RN opening and the CNA is the California Nurses Association.
# Excluding on the bare acronym threw that posting away. Let these veto a
# posting only when nothing else in the title says RN. Case-sensitive: these
# are always written as acronyms, and lowercasing invites new collisions.
AMBIGUOUS_ACRONYM = re.compile(r"\b(CNA|NP|CNS)\b")
RN_MARKER = re.compile(r"\b(RN|R\.N\.|registered nurse|staff nurse)\b", re.I)


def title_passes(title: str) -> bool:
    if not INCLUDE_TITLE.search(title):
        return False
    if EXCLUDE_TITLE.search(title):
        return False
    if AMBIGUOUS_ACRONYM.search(title) and not RN_MARKER.search(title):
        return False
    return True


# ── http ─────────────────────────────────────────────────────────────

# Browser-shaped Accept. Some portals (Alameda Health's especially) return a
# truncated stub page if you announce "Accept: application/json" — 8 KB
# instead of 479 KB, with the data blob stripped out. Look like a browser.
ACCEPT = ("text/html,application/xhtml+xml,application/xml;q=0.9,"
          "application/json;q=0.8,*/*;q=0.7")


# Block-level tags that end a statement. Stripping every tag to a space
# reads fine on prose and destroys a bulleted requirements list: Adventist
# writes
#     <li>Bachelor's Degree in Nursing (BSN): Preferred</li>
#     <li>Acute care facility experience: Preferred</li>
# and a space-strip yields "...(BSN): Preferred Acute care facility
# experience: Preferred" — one run-on in which the two bullets have merged.
# The digest then quotes "(BSN): Preferred Acute care facility" as the
# evidence for a verdict, which is a quote no reader can check, and the
# contract in CLAUDE.md is that the quote must support the label. Worse,
# a "2 years Required" bullet abutting a "Preferred" one puts both words
# in the same clause and the requirement rules can read either.
_BLOCK_END = re.compile(
    r"(?is)</(?:li|p|div|tr|h[1-6]|ul|ol|table|section)>|<br\s*/?>")


def _html_to_text(raw: str) -> str:
    """HTML to plain text, keeping statement boundaries."""
    if not raw:
        return ""
    text = _BLOCK_END.sub("\n", raw)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    # Collapse runs of spaces but keep the newlines the block tags left,
    # then normalise each line. A line that already ends in punctuation is
    # left alone; one that does not gets a period, so the classifier's
    # sentence handling sees a bullet as the statement it is rather than
    # as the opening of the next one.
    out = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t\xa0]+", " ", line).strip()
        if not line:
            continue
        out.append(line if line[-1] in ".;:!?" else line + ".")
    return " ".join(out)


def _request(url, data=None, headers=None, timeout=None, retries=None):
    """
    One HTTP call with retries. `timeout` and `retries` are per-source
    overrides for hosts that need more patience than the defaults; see
    NeoGov, which is the reason they exist.
    """
    timeout = TIMEOUT_SEC if timeout is None else timeout
    retries = MAX_RETRIES if retries is None else retries
    hdrs = {"User-Agent": UA, "Accept": ACCEPT,
            "Accept-Language": "en-US,en;q=0.9"}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    hdrs.update(headers or {})
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "replace")
            time.sleep(REQUEST_DELAY_SEC)
            return body
        except Exception as e:                      # noqa: BLE001
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"failed after {retries} tries: {url} ({last})")


# ── adapter 1: Workday CXS ───────────────────────────────────────────

class WorkdayCXS:
    """
    Every Workday tenant exposes the same endpoint its own careers page calls:
        POST https://{host}/wday/cxs/{tenant}/{site}/jobs
    Detail for one posting:
        GET  https://{host}/wday/cxs/{tenant}/{site}{externalPath}

    Adding a tenant is a config line, not new code. To find a tenant's
    host/site: open its careers page, DevTools > Network, filter "jobs",
    and read the request URL.
    """

    PAGE = 20   # Workday caps the listing page at 20

    def __init__(self, employer: str, host: str, tenant: str, site: str,
                 url_prefix: str | None = None):
        self.employer, self.host, self.tenant, self.site = employer, host, tenant, site
        self.base = f"https://{host}/wday/cxs/{tenant}/{site}"
        # Public-facing path, which is not always /{site}. Sutter sits on the
        # shared myworkdaysite.com host at /recruiting/sutterhealth/SH.
        self.url_prefix = url_prefix if url_prefix is not None else f"/{site}"

    # 50 pages x 20 = exactly 1000, which is not a coincidence: Sutter has
    # 1203 postings and the scan was stopping dead on the cap, hiding 202 of
    # them and 4 in-range RN roles with them. The loop already exits on
    # `offset >= total`, so this only needs to be high enough never to be
    # the thing that stops it. 200 pages = 4000 postings of headroom.
    def fetch_listings(self, max_pages: int = 200) -> list[Posting]:
        out, offset, total = [], 0, None
        seen: set[str] = set()
        for _ in range(max_pages):
            body = _request(f"{self.base}/jobs",
                            data={"appliedFacets": {}, "limit": self.PAGE,
                                  "offset": offset, "searchText": ""})
            d = json.loads(body)
            # Only page 1 reports a trustworthy total. El Camino's tenant
            # returns total=0 on every subsequent page, which silently
            # truncated this loop at 40 of 80 postings until it was caught.
            # Take the first non-zero total and never re-read it.
            if total is None and d.get("total"):
                total = d["total"]
            batch = d.get("jobPostings", [])
            if not batch:
                break
            new = 0
            for j in batch:
                path = j.get("externalPath", "")
                if path in seen:
                    continue
                seen.add(path)
                new += 1
                bullets = j.get("bulletFields") or []
                out.append(Posting(
                    employer=self.employer,
                    req_id=bullets[0] if bullets else path.rsplit("_", 1)[-1],
                    title=j.get("title", ""),
                    location=j.get("locationsText", ""),
                    url=f"https://{self.host}{self.url_prefix}{path}",
                    posted_date=None,          # listing says "Posted Today"; detail has the real date
                    source_adapter=f"workday:{self.tenant}",
                ))
            offset += self.PAGE
            # Stop on a short page, on no new records, or once we have them all.
            if len(batch) < self.PAGE or new == 0:
                break
            if total and offset >= total:
                break
        self._resolve_multi_locations(out)
        return out

    # Workday collapses a posting open at several sites down to "3 Locations"
    # on the listing page. geo can't parse that, so those postings landed in
    # the review bucket and never reached the ledger — including a
    # "Registered Nurse II, Primary Care" that is open in Castro Valley and
    # Antioch, both well inside the two-hour ring. The detail endpoint names
    # the real cities, so ask it. Restricted to postings that already look
    # like nurse roles: a handful of extra fetches, not hundreds.
    _MULTI_LOC = re.compile(r"^\s*\d+\s+locations?\s*$", re.I)
    _BUCKET_RANK = {"<30": 0, "30-60": 1, "60-90": 2, "90-120": 3}

    @classmethod
    def _closeness(cls, city: str) -> int:
        verdict, bucket, _ = geo.classify(city)
        if verdict is geo.Geo.IN:
            return cls._BUCKET_RANK.get(bucket, 4)
        return 5 if verdict is geo.Geo.UNKNOWN else 6

    def _resolve_multi_locations(self, postings: list[Posting]) -> None:
        for p in postings:
            if not self._MULTI_LOC.match(p.location or ""):
                continue
            if not title_passes(p.title):
                continue
            try:
                path = "/job/" + p.url.split("/job/", 1)[1]
                d = json.loads(_request(f"{self.base}{path}")).get(
                    "jobPostingInfo", {})
            except Exception as e:                      # noqa: BLE001
                print(f"     multi-location resolve failed {p.req_id}: {e}")
                continue
            cities = [c for c in [d.get("location") or ""]
                      + list(d.get("additionalLocations") or []) if c]
            if not cities:
                continue
            # Report it under its nearest site. A job you'd take in Castro
            # Valley shouldn't be filed under the Antioch listing.
            best = min(cities, key=self._closeness)
            others = len(cities) - 1
            p.location = f"{best} (+{others} more)" if others else best

    def fetch_detail(self, p: Posting) -> Posting:
        # Split on "/job/", never on the site slug. El Camino's host is
        # ech.wd5.myworkdayjobs.com and its site slug is also "ech", so
        # splitting on f"/{site}" matched inside the hostname and produced
        # .../wday/cxs/ech/ech.wd5.myworkdayjobs.com/ech/job/... -> 422 on
        # every detail fetch.
        path = "/job/" + p.url.split("/job/", 1)[1]
        d = json.loads(_request(f"{self.base}{path}")).get("jobPostingInfo", {})
        p.description = re.sub(r"<[^>]+>", " ", d.get("jobDescription", ""))
        p.description = re.sub(r"\s+", " ", p.description).strip()
        p.posted_date = d.get("startDate") or p.posted_date
        p.schedule = d.get("timeType")
        p.url = d.get("externalUrl") or p.url
        return p


# ── adapter 2: Sutter (Phenom, server-rendered) ──────────────────────

class SutterPhenom:
    """
    Sutter's /api/apply/v2/jobs and /widgets both return "Tenant not
    identified" to anonymous callers. But the search-results page embeds
    the same records under "eagerLoadRefineSearch", including lat/long,
    posted date, department and an apply URL. Parse those.

    Fragile by nature — it is a page, not an API. If this breaks, the
    aggregator fallback is what tells you.
    """

    PAGE = 10
    BASE = "https://jobs.sutterhealth.org/us/en/search-results"

    def __init__(self, employer: str = "Sutter Health"):
        self.employer = employer

    @staticmethod
    def _extract(html: str) -> list[dict]:
        i = html.find('"eagerLoadRefineSearch"')
        if i < 0:
            return []
        start = html.find("{", i)
        obj, _ = json.JSONDecoder().raw_decode(html[start:])
        return (obj.get("data") or obj).get("jobs", [])

    # Their relevance ranking is poor and their titles are inconsistent
    # ("RN II", "Registered Nurse", "Staff Nurse II"), so run several
    # queries and union the results rather than trusting one.
    KEYWORDS = ("nurse", "RN", "registered nurse", "staff nurse")
    HARD_PAGE_CAP = 300          # 3000 postings per keyword; safety valve only
    DUPE_PAGE_TOLERANCE = 3      # stop after N consecutive all-duplicate pages

    def fetch_listings(self, keywords: str | None = None,
                       max_pages: int | None = None) -> list[Posting]:
        queries = (keywords,) if keywords else self.KEYWORDS
        cap = max_pages or self.HARD_PAGE_CAP
        out, seen = [], set()
        for kw in queries:
            dry_streak = 0
            for page in range(cap):
                url = (f"{self.BASE}?keywords={urllib.parse.quote(kw)}"
                       f"&from={page * self.PAGE}&s=1")
                jobs = self._extract(_request(url))
                if not jobs:
                    break                      # genuinely exhausted
                new = 0
                for j in jobs:
                    seq = j.get("jobSeqNo")
                    if seq in seen:
                        continue
                    seen.add(seq)
                    new += 1
                    out.append(self._to_posting(j))
                # A page of pure duplicates can happen mid-run when queries
                # overlap, so tolerate a few before concluding we are done.
                dry_streak = dry_streak + 1 if new == 0 else 0
                if dry_streak >= self.DUPE_PAGE_TOLERANCE:
                    break
        return out

    def _to_posting(self, j: dict) -> Posting:
        return Posting(
            employer=self.employer,
            req_id=j.get("reqId") or j.get("jobSeqNo", ""),
            title=j.get("title", ""),
            location=j.get("cityState") or j.get("location", ""),
            url=j.get("applyUrl", ""),
            posted_date=j.get("postedDate"),
            description=j.get("descriptionTeaser", ""),
            department=j.get("department"),
            schedule=j.get("jobSchedule"),
            shift=j.get("Shift") or j.get("shift"),
            latitude=_f(j.get("latitude")),
            longitude=_f(j.get("longitude")),
            source_adapter="phenom:sutter",
        )

    def fetch_detail(self, p: Posting) -> Posting:
        # The listing teaser is usually enough for the classifier. Full text
        # would mean parsing the job page HTML; add it only if the classifier
        # turns out to need more than the teaser.
        return p


# ── adapter 5: PACS Group (post-acute / skilled nursing) ─────────────

class PACS:
    """
    PACS Group — post-acute and skilled nursing facilities. Runs on Workday
    (tenant `pacs`, host wd108) but needs its own class for two reasons.

    1. Facets instead of keywords. PACS exposes a Job_Profile facet with an
       "RN-H" value, so RN roles can be selected exactly rather than guessed
       at from title text. Combined with the California state facet that is
       2000 postings narrowed to ~112 server-side.

    2. No location data at all. Not in the listing (locationsText is null),
       not in the detail record, not in jobRequisitionLocation, not in the
       body — only a facility name like "East Bay Post Acute". Automatic
       geocoding fails on most of them, so facility -> city is resolved from
       pacs_facilities.json and anything unlisted goes to review rather than
       being guessed.

    Worth the extra work: post-acute is the segment that hires new grads and
    asks for skilled-nursing experience as "preferred" rather than requiring
    acute care.
    """

    BASE = "https://pacs.wd108.myworkdayjobs.com/wday/cxs/pacs/pacs"
    PAGE = 20

    def __init__(self, employer="PACS Group", state="California",
                 profile="RN-H", cache_path="pacs_facilities.json"):
        self.employer, self.state, self.profile = employer, state, profile
        self.cache_path = cache_path
        self._facets = None

    def _facet_ids(self) -> dict:
        if self._facets is None:
            d = json.loads(_request(f"{self.BASE}/jobs",
                                    data={"appliedFacets": {}, "limit": 1,
                                          "offset": 0, "searchText": ""}))
            ids = {}
            for f in d.get("facets", []):
                for v in f.get("values", []):
                    if v.get("descriptor") == self.state:
                        ids["LocationRegionStateProvince"] = [v["id"]]
                    if v.get("descriptor") == self.profile:
                        ids["Job_Profile"] = [v["id"]]
            self._facets = ids
        return self._facets

    def _cache(self) -> dict:
        """
        The facility -> city table. Say so loudly when it isn't there.

        Without it every PACS posting resolves to a bare facility name,
        geo.py cannot place any of them, and eighty jobs land in "Location
        needs checking" with nothing on screen explaining why. That looked
        exactly like a geo regression for a while; it was a missing file.
        Degrading quietly is the failure mode this project keeps paying
        for, so it degrades loudly instead.
        """
        try:
            with open(self.cache_path) as f:
                cache = json.load(f)
        except (OSError, json.JSONDecodeError) as e:                # noqa: BLE001
            print(f"  !! {self.employer}: cannot read {self.cache_path} ({e}) "
                  f"— every posting will go to location review")
            return {"facilities": {}}
        if not cache.get("facilities"):
            print(f"  !! {self.employer}: {self.cache_path} lists no "
                  f"facilities — every posting will go to location review")
        return cache

    def fetch_listings(self, max_pages: int = 30) -> list[Posting]:
        facets = self._facet_ids()
        cache = self._cache()
        facilities = cache.get("facilities", {})
        out, offset = [], 0
        for _ in range(max_pages):
            d = json.loads(_request(f"{self.BASE}/jobs",
                                    data={"appliedFacets": facets,
                                          "limit": self.PAGE, "offset": offset,
                                          "searchText": ""}))
            batch = d.get("jobPostings", [])
            if not batch:
                break
            for j in batch:
                path = j.get("externalPath", "")
                parts = path.split("/")
                facility = parts[2].replace("-", " ") if len(parts) > 2 else ""
                # Resolve to a city if we know one; otherwise pass the facility
                # name through and let geo.py send it to review.
                city = facilities.get(facility)
                out.append(Posting(
                    employer=self.employer,
                    req_id=(j.get("bulletFields") or [path.rsplit("_", 1)[-1]])[0],
                    title=j.get("title", ""),
                    location=city or facility,
                    department=facility,
                    url=f"https://pacs.wd108.myworkdayjobs.com/pacs{path}",
                    posted_date=None,
                    setting="Skilled nursing",
                    source_adapter="workday:pacs",
                ))
            offset += self.PAGE
            if len(batch) < self.PAGE:
                break
        return out

    def fetch_detail(self, p: Posting) -> Posting:
        path = p.url.split("/pacs", 2)[-1]
        d = json.loads(_request(f"{self.BASE}{path}")).get("jobPostingInfo", {})
        p.description = re.sub(r"\s+", " ",
                               re.sub(r"<[^>]+>", " ", d.get("jobDescription", ""))).strip()
        p.posted_date = d.get("startDate") or p.posted_date
        p.url = d.get("externalUrl") or p.url
        return p


# ── adapter 6: ScionHealth / Kindred (LTAC) ──────────────────────────

class ScionHealth:
    """
    Kindred Hospitals, now part of ScionHealth. Long-term acute care.

    Radancy/TalentBrew portal — no JSON API exposed, but the search-jobs
    page renders job links server-side and, usefully, encodes the city in
    the URL path: /job/san-leandro/case-manager-ii-ft-days/42238/99054486864
    So location comes free from the listing, no cache needed.

    In-range campus: Kindred Hospital San Francisco Bay Area, San Leandro.
    LTAC sits between acute and skilled nursing — worth watching because it
    hires from a wider experience band than the acute systems do.
    """

    BASE = "https://jobs.scionhealth.com"
    RE_JOB = re.compile(
        r'href="(/job/([a-z0-9\-]+)/([a-z0-9\-]+)/\d+/\d+)"[^>]*>(?:\s*<[^>]+>)*\s*([^<]{3,120})')

    # The portal's /search-jobs/{location}/ path is decorative — it returns
    # nationwide results regardless. So we pull broad and filter on the city
    # slug in the job URL against the campuses actually within range.
    IN_RANGE_SLUGS = {"san-leandro", "oakland", "san-francisco", "berkeley",
                      "hayward", "san-jose", "sacramento", "modesto",
                      "stockton", "vallejo", "concord", "walnut-creek"}

    def __init__(self, employer="Kindred / ScionHealth", slugs=None):
        self.employer = employer
        self.slugs = slugs or self.IN_RANGE_SLUGS
        self.locations = ("San Leandro, CA",)

    def fetch_listings(self, max_pages: int = 10) -> list[Posting]:
        out, seen = [], set()
        for loc in self.locations:
            for page in range(1, max_pages + 1):
                url = (f"{self.BASE}/search-jobs/{urllib.parse.quote(loc)}/"
                       f"?p={page}" if page > 1 else
                       f"{self.BASE}/search-jobs/{urllib.parse.quote(loc)}/")
                try:
                    html_body = _request(url)
                except RuntimeError:
                    break
                found = self.RE_JOB.findall(html_body)
                if not found:
                    break
                new = 0
                for path, city, slug, title in found:
                    if path in seen or city not in self.slugs:
                        continue
                    seen.add(path)
                    new += 1
                    out.append(Posting(
                        employer=self.employer,
                        req_id=path.rsplit("/", 1)[-1],
                        title=html.unescape(title).strip(),
                        # Never append a state. The portal's location URL does
                        # not actually filter, so results are nationwide, and
                        # tacking ", CA" onto every slug invented "Cleveland, CA"
                        # and "Indianapolis, CA". Pass the bare city and let
                        # geo.py judge it.
                        location=city.replace("-", " ").title(),
                        url=self.BASE + path,
                        setting="Long-term acute care",
                        source_adapter="radancy:scionhealth",
                    ))
                if new == 0:
                    break
        return out

    def fetch_detail(self, p: Posting) -> Posting:
        body = _request(p.url)
        body = re.sub(r"(?s)<(script|style).*?</\1>", " ", body)
        p.description = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()[:9000]
        return p


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── adapter 3: HealthcareSource / hctsportals (Alameda Health System) ─

class HealthcareSource:
    """
    HealthcareSource career portals render a `cslocations` JSON blob into the
    search page to drive their map widget. It carries id, title, permalink and
    lat/lng for every posting on the page — which is everything we need, and
    the coordinates mean the geo filter never has to guess.

    Pagination is ?page=N. The path segment is a job-family slug; "nursing"
    is the one that matters here.
    """

    BASE = "https://alameda-health-system-careers.hctsportals.com"
    RE_BLOB = re.compile(r"cslocations\s*=\s*\$cs\.parseJSON\('(.*?)'\)\s*;", re.S)

    def __init__(self, employer="Alameda Health System", family="nursing"):
        self.employer, self.family = employer, family

    @classmethod
    def _extract(cls, html: str) -> list[dict]:
        m = cls.RE_BLOB.search(html)
        if not m:
            return []
        try:
            return json.loads(m.group(1).encode().decode("unicode_escape"))
        except json.JSONDecodeError:
            return []

    def fetch_listings(self, max_pages: int = 40) -> list[Posting]:
        out, seen = [], set()
        for page in range(1, max_pages + 1):
            url = f"{self.BASE}/search/{self.family}/jobs?page={page}"
            try:
                jobs = self._extract(_request(url))
            except RuntimeError:
                break
            if not jobs:
                break
            new = 0
            for j in jobs:
                jid = str(j.get("id"))
                if jid in seen:
                    continue
                seen.add(jid)
                new += 1
                g = j.get("geography") or {}
                out.append(Posting(
                    employer=self.employer,
                    req_id=jid,
                    title=j.get("title", ""),
                    location=j.get("location_string", ""),
                    url=f"{self.BASE}/jobs/{jid}-{j.get('permalink','')}",
                    latitude=_f(g.get("lat")),
                    longitude=_f(g.get("lng")),
                    source_adapter="healthcaresource:ahs",
                ))
            if new == 0:
                break
        return out

    def fetch_detail(self, p: Posting) -> Posting:
        html = _request(p.url)
        body = re.sub(r"(?s)<(script|style).*?</\1>", " ", html)
        text = re.sub(r"<[^>]+>", " ", body)
        p.description = re.sub(r"\s+", " ", text).strip()[:8000]
        return p


# ── adapter 4: USAJOBS (VA) ──────────────────────────────────────────

class USAJobs:
    """
    Official public API. Free, documented, stable — but it requires a key,
    so unlike the other three this adapter is UNTESTED against live data.
    An unauthenticated call returns 401, which is all I could confirm.

    To activate:
        1. Request a key at https://developer.usajobs.gov/apirequest/
           (free, arrives by email)
        2. export USAJOBS_KEY=...  and  USAJOBS_EMAIL=you@example.com
        3. Re-run. Verify the field mapping below against the first response
           before trusting it — I mapped it from the documented schema, not
           from a live payload.

    Series 0610 is Nurse. VA facilities in range: Palo Alto, Martinez,
    Mather/Sacramento, San Francisco.
    """

    ENDPOINT = "https://data.usajobs.gov/api/search"
    LOCATIONS = ("Palo Alto, California", "Martinez, California",
                 "Sacramento, California", "San Francisco, California",
                 "Oakland, California", "Fairfield, California")

    def __init__(self, employer="US Dept of Veterans Affairs"):
        self.employer = employer
        self.key = os.environ.get("USAJOBS_KEY")
        self.email = os.environ.get("USAJOBS_EMAIL", "")

    def fetch_listings(self, max_pages: int = 10) -> list[Posting]:
        if not self.key:
            raise RuntimeError(
                "USAJOBS_KEY not set — request one at "
                "https://developer.usajobs.gov/apirequest/ (adapter is untested)")
        headers = {"Host": "data.usajobs.gov",
                   "User-Agent": self.email,
                   "Authorization-Key": self.key}
        out = []
        for loc in self.LOCATIONS:
            for page in range(1, max_pages + 1):
                q = urllib.parse.urlencode({
                    "JobCategoryCode": "0610", "LocationName": loc,
                    "ResultsPerPage": 500, "Page": page})
                d = json.loads(_request(f"{self.ENDPOINT}?{q}", headers=headers))
                items = d.get("SearchResult", {}).get("SearchResultItems", [])
                for it in items:
                    o = it.get("MatchedObjectDescriptor", {})
                    locs = o.get("PositionLocation") or [{}]
                    out.append(Posting(
                        employer=o.get("OrganizationName") or self.employer,
                        req_id=o.get("PositionID", ""),
                        title=o.get("PositionTitle", ""),
                        location=o.get("PositionLocationDisplay", ""),
                        url=o.get("PositionURI", ""),
                        posted_date=o.get("PublicationStartDate"),
                        description=(o.get("UserArea", {}).get("Details", {})
                                     .get("JobSummary", "")),
                        latitude=_f(locs[0].get("Latitude")),
                        longitude=_f(locs[0].get("Longitude")),
                        source_adapter="usajobs",
                    ))
                if len(items) < 500:
                    break
        return out

    def fetch_detail(self, p: Posting) -> Posting:
        return p   # search response already carries the full summary


# ── adapter 7: NEOGOV / governmentjobs.com (CA counties and cities) ──

class NeoGov:
    """
    NEOGOV powers the HR site of most California county and city
    governments. One class, N agencies.

    This was written off as "needs a headless browser", and it looks that
    way from the outside: /careers/{agency}/jobs serves a 976-byte shell,
    the agency root serves 204 KB of Knockout scaffolding with no postings
    in it, there is no JSON API, and /jobs/rss returns HTML. Every visible
    signal says client-side rendering.

    It isn't. The listing is rendered server-side, but only for a caller
    that identifies as an XHR. Send X-Requested-With and the same agency
    root returns the rows as HTML, ten to a page. No browser, no session,
    no cookie.

    Detail pages carry a JSON-LD JobPosting block. Prefer it to the
    surrounding 130 KB of navigation: it holds the title, an ISO date, the
    location and the entire requirements text in one object. Its
    description field is double-escaped — unescape before stripping tags,
    or the tags survive and the classifier reads markup as prose.
    """

    BASE = "https://www.governmentjobs.com/careers"
    HOST = "https://www.governmentjobs.com"
    XHR = {"X-Requested-With": "XMLHttpRequest"}

    # governmentjobs.com is the slowest host this scan talks to and the
    # only one that has ever failed in bulk: on 2026-09-08 and 09-09 it
    # timed out for five of six agencies in the same run, twice, taking
    # ~205 listings down to 50. Three tries over 30s was not enough
    # patience for a host that answers in 20s on a good day.
    TIMEOUT = 45
    RETRIES = 4

    # ...but patience has to be bounded, because it multiplies. Eleven
    # agencies each burning four 45s timeouts plus backoff is over half an
    # hour on its own, and the workflow is killed at 60 minutes — which
    # publishes nothing at all, a worse outcome than a short scan. So the
    # whole adapter gets a wall-clock budget. Agencies not reached before
    # it runs out are simply not read, and covered_employers() already
    # makes that safe: their rows are left alone and the digest names
    # them. Generous against a healthy host (a full sweep is ~90s) and a
    # hard stop against a sick one.
    BUDGET_SEC = 420
    MAX_PAGES = 40          # 10 per page; largest agency here is ~75

    # Paging without an explicit sort is not stable: the server reorders
    # between requests, so later pages repeat rows already returned and the
    # tail is never served at all. Contra Costa reports 75 postings and an
    # unsorted sweep of all 8 pages yielded 46 of them, silently. Sorting by
    # title pins the order and returns all 75. Do not remove this.
    SORT = "&sort=PositionTitle&isDescendingSort=false"

    # slug -> (employer name, city to file postings under).
    # The listing reports the location as "Contra Costa County, CA", which
    # is a jurisdiction and not a place geo can rank, so each agency names
    # the city its facilities actually sit in. Verify before adding one:
    # a county seat is not always where the health department is.
    AGENCIES = {
        "contracosta":  ("Contra Costa County", "Martinez"),
        "solanocounty": ("Solano County", "Fairfield"),
        "marincounty":  ("Marin County", "San Rafael"),
        "napacounty":   ("Napa County", "Napa"),
        "berkeley":     ("City of Berkeley", "Berkeley"),
        "oaklandca":    ("City of Oakland", "Oakland"),
        # Added 2026-09-09. Each city below is the one the agency's
        # hospital actually sits in, not the county seat: Santa Clara
        # County's Valley Medical Center is in San Jose, and Monterey
        # County's Natividad is in Salinas.
        "santaclara":   ("County of Santa Clara", "San Jose"),
        "sanmateo":     ("County of San Mateo", "San Mateo"),
        "montereycounty": ("County of Monterey", "Salinas"),
        "sacramento":   ("Sacramento County", "Sacramento"),
        "sonoma":       ("County of Sonoma", "Santa Rosa"),
        # Slugs that look right and are not, checked 2026-09-09:
        #   "sjcounty"   is San Juan County, UT — not San Joaquin.
        #   "alamedaca"  is the City of Alameda, not Alameda County;
        #                Alameda Hospital is already read via
        #                HealthcareSource (Alameda Health System).
        # San Joaquin County is not on NEOGOV at all; it runs JobAps.
    }

    def __init__(self, employer="CA counties & cities (NEOGOV)", agencies=None):
        # `employer` labels the scan log only; each Posting carries the
        # agency that actually posted it.
        self.employer = employer
        self.agencies = agencies or self.AGENCIES
        # Agencies whose listing this run actually read, by the employer
        # name their postings carry. This is not bookkeeping for its own
        # sake: on 2026-09-08 five of six agencies timed out, the adapter
        # still returned the sixth and so still counted as "ok", and the
        # scan marked eleven live postings closed because it could not
        # tell an agency that went quiet from an agency it never reached.
        # Six of those were Contra Costa Regional Medical Center roles
        # that were still open, and a closed row never comes back.
        self.reached: set[str] = set()

    @staticmethod
    def _text(fragment: str) -> str:
        return html.unescape(re.sub(r"\s+", " ",
                                    re.sub(r"<[^>]+>", " ", fragment))).strip()

    def covered_employers(self) -> set[str]:
        """The agencies this run actually read. See `reached`."""
        return set(self.reached)

    def all_employers(self) -> set[str]:
        """Every agency this adapter is responsible for, read or not.
        The gap between this and covered_employers() is what the digest
        reports as a degraded source."""
        return {name for name, _city in self.agencies.values()}

    def fetch_listings(self) -> list[Posting]:
        out: list[Posting] = []
        self.reached = set()
        deadline = time.monotonic() + self.BUDGET_SEC
        for slug, (name, city) in self.agencies.items():
            if time.monotonic() > deadline:
                print(f"     {name}: skipped, adapter budget "
                      f"({self.BUDGET_SEC}s) spent")
                continue
            seen: set[str] = set()
            ok = True
            for page in range(1, self.MAX_PAGES + 1):
                url = f"{self.BASE}/{slug}?page={page}{self.SORT}"
                try:
                    body = _request(url, headers=self.XHR,
                                    timeout=self.TIMEOUT, retries=self.RETRIES)
                except Exception as e:                      # noqa: BLE001
                    print(f"     {name} page {page}: {e}")
                    # Page 1 failing means we saw nothing for this agency.
                    # A later page failing means we saw a prefix of it —
                    # equally unsafe to close rows on, because the tail we
                    # did not read is indistinguishable from a tail that
                    # was taken down.
                    ok = False
                    break
                rows = body.split('<li class="list-item"')[1:]
                # The end of the listing is an empty page, and that is the
                # only thing that ends the loop. An earlier version also
                # stopped when a page contributed no new ids, which turned
                # the reordering above into a silent 40-of-75 truncation.
                if not rows:
                    break
                for chunk in rows:
                    m = re.search(r'data-job-id="(\d+)"', chunk)
                    a = re.search(
                        r'class="item-details-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                        chunk, re.S)
                    if not (m and a):
                        continue
                    jid = m.group(1)
                    if jid in seen:
                        continue
                    seen.add(jid)
                    dept = re.search(r'data-department-name="([^"]*)"', chunk)
                    out.append(Posting(
                        employer=name,
                        req_id=jid,
                        title=self._text(a.group(2)),
                        # The agency's own city, not the county name the
                        # listing prints, which geo cannot rank.
                        location=city,
                        url=self.HOST + html.unescape(a.group(1)),
                        department=self._text(dept.group(1)) if dept else None,
                        source_adapter=f"neogov:{slug}",
                    ))
            if ok:
                self.reached.add(name)
        return out

    def fetch_detail(self, p: Posting) -> Posting:
        body = _request(p.url, timeout=self.TIMEOUT, retries=self.RETRIES)
        m = re.search(r'<script type="application/ld\+json">(.*?)</script>',
                      body, re.S)
        if not m:
            return p
        try:
            d = json.loads(m.group(1))
        except json.JSONDecodeError:
            return p
        # Unescape first: the field arrives with its markup escaped, so
        # stripping tags before unescaping strips nothing at all.
        raw = html.unescape(d.get("description", ""))
        p.description = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()
        p.posted_date = d.get("datePosted") or p.posted_date
        p.schedule = d.get("employmentType") or p.schedule
        return p


# ── adapter 8: JIBE (Vibra / Kentfield, iCIMS behind a JIBE front-end) ─

class Jibe:
    """
    JIBE career sites sit in front of an iCIMS ATS and expose a plain JSON
    API that needs no key, no session and no browser:

        GET https://{host}/api/jobs?page=1&limit=100&state=California

    This is the source the README wrote off as needing a headless browser.
    The mistake was reading the marketing site (vibrahealthcare.com/careers)
    rather than the careers subdomain; the subdomain identifies itself as
    JIBE in its own markup and the API is one path down from there.

    Two things make this the cheapest adapter here. The listing response
    already carries the full description and qualifications, so there is no
    detail request to make — fetch_detail just returns what it was given.
    And `state` filters server-side, so one request covers every California
    posting instead of paging the whole national board.

    Kentfield Rehabilitation (Marin) is a Vibra LTAC and appears here when
    it has openings; it had none when this was written, which is why the
    README recorded the source as blocked rather than empty.
    """

    PER_PAGE = 100
    MAX_PAGES = 20

    def __init__(self, employer="Vibra Healthcare",
                 host="careers.vibrahealthcare.com", state="California"):
        self.employer, self.host, self.state = employer, host, state
        self.base = f"https://{host}/api/jobs"

    @staticmethod
    def _clean(fragment: str) -> str:
        return html.unescape(re.sub(r"\s+", " ",
                                    re.sub(r"<[^>]+>", " ", fragment or ""))).strip()

    @staticmethod
    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def fetch_listings(self) -> list[Posting]:
        out: list[Posting] = []
        seen: set[str] = set()
        for page in range(1, self.MAX_PAGES + 1):
            url = (f"{self.base}?page={page}&limit={self.PER_PAGE}"
                   f"&state={urllib.parse.quote(self.state)}")
            d = json.loads(_request(url))
            batch = d.get("jobs") or []
            if not batch:
                break
            for row in batch:
                j = row.get("data") or {}
                rid = str(j.get("req_id") or j.get("slug") or "")
                if not rid or rid in seen:
                    continue
                seen.add(rid)
                # Requirements often sit in `qualifications` rather than
                # `description`; the classifier needs both or it reads an
                # overview with no requirements section and says so.
                body = " ".join(filter(None, [
                    self._clean(j.get("description")),
                    self._clean(j.get("qualifications")),
                    self._clean(j.get("responsibilities")),
                ]))
                out.append(Posting(
                    employer=self.employer,
                    req_id=rid,
                    title=j.get("title", ""),
                    location=j.get("full_location") or j.get("city") or "",
                    url=j.get("apply_url") or f"https://{self.host}/jobs/{rid}",
                    posted_date=(j.get("posted_date") or "")[:10] or None,
                    description=body,
                    department=j.get("department") or None,
                    schedule=j.get("employment_type") or None,
                    latitude=self._f(j.get("latitude")),
                    longitude=self._f(j.get("longitude")),
                    setting="Long-term acute care / rehab",
                    source_adapter=f"jibe:{self.host}",
                ))
            if len(batch) < self.PER_PAGE:
                break
        return out

    def fetch_detail(self, p: Posting) -> Posting:
        # The listing already carried the full text. Nothing to fetch.
        return p


# ── adapter 9: SmartRecruiters (San Francisco DPH and citywide) ──────

class SmartRecruiters:
    """
    SmartRecruiters publishes an open, unauthenticated API:

        GET https://api.smartrecruiters.com/v1/companies/{co}/postings
        GET https://api.smartrecruiters.com/v1/companies/{co}/postings/{id}

    San Francisco's careers site is a SmartRecruiters front end, which is
    how SFDPH gets covered. The company identifier is not guessable — every
    sensible spelling of it returns HTTP 200 with `totalFound: 0`, which
    looks like an empty board rather than a wrong name. The real one,
    CityAndCountyOfSanFrancisco1, is in an apply link on careers.sf.gov.
    If this adapter ever reports zero, check that first.

    Requirements live in jobAd.sections.qualifications, separate from the
    duties in jobDescription. Send both to the classifier: SF states the
    licence in one and the experience in the other.
    """

    HOST = "https://api.smartrecruiters.com/v1/companies"
    PER_PAGE = 100
    MAX_PAGES = 20

    def __init__(self, employer="City & County of San Francisco",
                 company="CityAndCountyOfSanFrancisco1"):
        self.employer, self.company = employer, company
        self.base = f"{self.HOST}/{company}/postings"

    @staticmethod
    def _clean(fragment: str) -> str:
        return re.sub(r"\s+", " ",
                      re.sub(r"<[^>]+>", " ",
                             html.unescape(fragment or ""))).strip()

    def fetch_listings(self) -> list[Posting]:
        out: list[Posting] = []
        for page in range(self.MAX_PAGES):
            d = json.loads(_request(
                f"{self.base}?limit={self.PER_PAGE}&offset={page * self.PER_PAGE}"))
            batch = d.get("content") or []
            if not batch:
                break
            for j in batch:
                loc = j.get("location") or {}
                out.append(Posting(
                    employer=self.employer,
                    req_id=str(j.get("id") or j.get("refNumber") or ""),
                    title=j.get("name", ""),
                    location=loc.get("city") or "",
                    url=f"https://careers.sf.gov/role/?id={j.get('id')}",
                    posted_date=(j.get("releasedDate") or "")[:10] or None,
                    department=(j.get("department") or {}).get("label"),
                    schedule=(j.get("typeOfEmployment") or {}).get("label"),
                    source_adapter=f"smartrecruiters:{self.company}",
                ))
            if len(batch) < self.PER_PAGE:
                break
        return out

    def fetch_detail(self, p: Posting) -> Posting:
        d = json.loads(_request(f"{self.base}/{p.req_id}"))
        sec = (d.get("jobAd") or {}).get("sections") or {}

        def part(name):
            v = sec.get(name)
            return self._clean(v.get("text", "")) if isinstance(v, dict) else ""

        # qualifications first: it holds the licence and experience gates,
        # and the classifier reads from the front of what it is given.
        # companyDescription is 2 KB of DEI boilerplate on every SF posting
        # and is deliberately left out.
        p.description = " ".join(filter(None, [
            part("qualifications"), part("jobDescription"),
            part("additionalInformation"),
        ]))
        p.url = d.get("postingUrl") or p.url
        return p


# ── adapter 10: Smart Hires (St. Rose Hospital, Hayward) ─────────────

class SmartHires:
    """
    St. Rose Hospital, Hayward.

    Why it was missing: nothing here reached it. St. Rose is independent —
    not Sutter, not John Muir, not Alameda Health, not a county — so no
    adapter covered it, and it runs on Smart Hires, an ATS none of the other
    nine speak. It is an acute-care hospital inside the `<30` bucket, with
    its own subacute and skilled-nursing units, and it was invisible to
    every scan this project has ever run.

    The board looks client-rendered and is not. Paging, filtering and
    sorting are all DWR calls made after load, which is what makes the page
    look like an app, but the table itself is delivered complete in the
    first response — every open requisition, no paging. That is the same
    mistake NEOGOV nearly cost us: check whether the markup is really empty
    before reaching for a browser.

    Two useful things about the markup:

    * Each row carries hidden inputs — hidjobId, hidpositiontitle,
      hidjobType, hidencriptedJobId — holding the fields the visible cell
      only shows as a truncated teaser ("Current valid CA Registered Nurse
      license required. Curre..."). Parse the inputs, not the teaser.
    * The listing states no location at all. The detail page states a full
      street address. St. Rose is a single campus, so `location` is seeded
      with it and then replaced by whatever the detail page actually says —
      and if that turns out to be somewhere else, the drive-time bucket is
      recomputed rather than left on the seeded value.

    The detail page is also where the requirement lives, in two forms that
    can disagree. The ED posting's prose says "Minimum two-years Emergency
    Department experience preferred" while the structured field beneath it
    says "Experience: Minimum 2 Years". Both go into the description on
    purpose. The hard field is what stops a posting whose prose only ever
    says "preferred" from reading as no-experience-required; keeping the
    prose alongside it is what leaves you a sentence worth reading rather
    than the two words "Minimum 2 Years".
    """

    BASE = "https://app.smarthires.com"

    # id="hidpositiontitle623158" value="RN - Emergency 334"
    RE_FIELD = re.compile(r'id="hid([A-Za-z]+?)(\d+)"\s+value="([^"]*)"')
    # <div class="span5 joblabel">Job Location</div><div class="span7">...</div>
    RE_LABEL = re.compile(
        r'(?s)class="[^"]*joblabel[^"]*">\s*([^<]{2,40}?)\s*</div>\s*'
        r'<div[^>]*>(.*?)</div>')
    RE_SPAN = re.compile(r'(?s)<span id="(resSpan|reqQuliSpan)">(.*?)</span>')
    RE_PARA = re.compile(r'(?s)<strong>\s*([^<:]{2,30}):\s*</strong>(.*?)</p>')

    def __init__(self, employer="St. Rose Hospital",
                 board="St.-Rose-Hospital-2",
                 campus="Hayward, CA",
                 setting="Acute hospital"):
        self.employer, self.board = employer, board
        self.campus, self.setting = campus, setting

    def fetch_listings(self) -> list[Posting]:
        body = _request(f"{self.BASE}/jobopenings/{self.board}.htm")
        jobs: dict[str, dict[str, str]] = {}
        for field, job_id, value in self.RE_FIELD.findall(body):
            jobs.setdefault(job_id, {})[field] = html.unescape(value)

        out = []
        for job_id, f in jobs.items():
            enc = f.get("encriptedJobId")
            if not enc or not f.get("positiontitle"):
                continue
            out.append(Posting(
                employer=self.employer,
                # The number the posting itself shows as its Position Id, so
                # a row in the ledger can be matched against a confirmation
                # email without translation.
                req_id=f"STROS{job_id}",
                title=f["positiontitle"].strip(),
                location=self.campus,
                url=f"{self.BASE}/showempjob.htm?viewId={enc}",
                schedule=f.get("jobType") or None,
                department=(f.get("department") or "").strip() or None,
                setting=self.setting,
                source_adapter="smarthires:st-rose",
            ))
        return out

    # "Full-Time (0.9) NOC Shift (1900-0700) (Tues/Wed/Sat - Sun/Mon/Wed)"
    # is how every St. Rose posting opens its responsibilities block, and it
    # is the only place the shift is stated. Lift it into the record rather
    # than leave the digest to find it in the middle of a wall of duties.
    RE_FRONT = re.compile(
        r"(?is)^(.{0,200}?)\s*(?:APPROXIMATE PAY RANGE|POSITION SUMMARY"
        r"|JOB SUMMARY|Under general supervision)")

    def fetch_detail(self, p: Posting) -> Posting:
        body = _request(p.url)

        def text(raw):
            return re.sub(r"\s+", " ",
                          html.unescape(re.sub(r"<[^>]+>", " ", raw or ""))).strip()

        labels = {k.strip().lower(): text(v) for k, v in self.RE_LABEL.findall(body)}
        spans = {k: text(v) for k, v in self.RE_SPAN.findall(body)}
        paras = {k.strip().lower(): text(v) for k, v in self.RE_PARA.findall(body)}

        # Requirements first: the licence and experience gates live in
        # reqQuliSpan, and both the classifier's section parse and its
        # free-form fallback read better when the qualifications are not
        # buried under two thousand words of essential duties.
        #
        # Note what is NOT done here: no invented section heading is glued
        # on the front. An earlier version prefixed this block with
        # "Required Qualification:" to give the classifier something to
        # anchor on, and that phrase promptly became a requirement clause in
        # its own right — the Surgery posting came out GENERAL_EXPERIENCE
        # quoting "Required Qualification: EDUCATION, EXPERIENCE, TRAINING
        # 1." as its evidence, a quote that supports nothing. The posting's
        # own words, in the posting's own order, or nothing.
        parts = [spans.get("reqQuliSpan", ""), spans.get("resSpan", "")]

        # Smart Hires also carries a structured experience field, and it can
        # contradict the prose above it: the ED posting's qualifications say
        # "Minimum two-years Emergency Department experience preferred"
        # while this field says "Minimum 2 Years". Keep it — on a posting
        # whose prose promises nothing, it is what trips the classifier's
        # duration veto — but do NOT label it "Experience:", which would
        # make it the parsed experience section and leave a two-word quote
        # standing in for the fuller sentence above it.
        if paras.get("experience"):
            # Terminated with a full stop so the classifier's clause split
            # keeps it separate. Without it the evidence quote reads
            # "Minimum 2 Years Degree required: Associate/Diploma Or Higher",
            # which buries the requirement in the credential line after it.
            parts.append(f"Stated experience requirement: {paras['experience']}.")
        if paras.get("degree required"):
            parts.append(f"Degree required: {paras['degree required']}.")
        p.description = " ".join(x for x in parts if x)

        front = self.RE_FRONT.match(spans.get("resSpan", "") or "")
        if front:
            p.shift = front.group(1).strip(" .-|") or None

        p.schedule = labels.get("job type") or p.schedule
        # "1 Regular/Full time Jobs" — the leading count and trailing plural
        # are Smart Hires chrome, not part of the job type.
        if p.schedule:
            p.schedule = re.sub(r"(?i)^\d+\s+|\s+jobs?$", "", p.schedule).strip()

        where = labels.get("job location")
        if where:
            p.location = self._city(where)
            # The listing had to assume the campus. If the detail page names
            # somewhere else, re-run the geo call rather than keep a bucket
            # that was derived from an assumption.
            verdict, bucket, miles = geo.classify(p.location)
            p.geo_verdict, p.drive_time_bucket = verdict.value, bucket
            if miles is not None:
                p.straight_line_mi = round(miles, 1)
        return p

    # "27200 Calaroga Avenue, HAYWARD, ALAMEDA, CALIFORNIA, UNITED STATES
    #  - 94545" -> "Hayward, CA". geo.py matches the full string perfectly
    # well, but this also lands in the digest's Location column and in your
    # ledger, where a 60-character address beside a two-word city is
    # unreadable.
    STATES = {"CALIFORNIA": "CA", "NEVADA": "NV", "OREGON": "OR",
              "ARIZONA": "AZ", "WASHINGTON": "WA"}
    NOT_A_CITY = {"UNITED STATES", "USA", "US"}

    @classmethod
    def _city(cls, address: str) -> str:
        segs = [s.strip(" -") for s in address.split(",") if s.strip(" -")]
        # The street line is the one with a house number in it; the state,
        # country and ZIP are named or numeric. What is left, first, is the
        # city — and Smart Hires puts the county right after it, which is
        # why this takes the first match and not the last.
        city = next((s for s in segs
                     if not re.search(r"\d", s)
                     and s.upper() not in cls.STATES
                     and s.upper() not in cls.NOT_A_CITY), "")
        state = next((cls.STATES[s.upper()] for s in segs
                      if s.upper() in cls.STATES), "")
        if city.isupper():
            city = city.title()
        return ", ".join(x for x in (city, state) if x) or address


# ── registry ─────────────────────────────────────────────────────────
# Kaiser Permanente and Stanford Health Care are excluded by request.

# ── adapter 11: Oracle Recruiting Cloud (Fusion) ─────────────────────

class OracleORC:
    """
    Oracle Recruiting Cloud, the ATS behind a surprising share of the
    hospitals this scan was missing. One class, N tenants:

        UCSF Health        822 postings — San Francisco AND Oakland (Benioff)
        Tenet Health      2919 postings — San Ramon Regional, Doctors Modesto
        Providence        2050 postings — Queen of the Valley, Santa Rosa Mem.
        Adventist Health  1427 postings
        NorthBay Health    124 postings

    All five present the same REST API, unauthenticated:

        GET /hcmRestApi/resources/latest/recruitingCEJobRequisitions
            ?onlyData=true&expand=requisitionList.secondaryLocations
            &finder=findReqs;siteNumber={site},limit=200,offset=N

    Two things about that URL are load-bearing:

      * `expand=requisitionList.secondaryLocations` — without it the
        response carries counts and facets but `requisitionList` comes back
        empty, which reads exactly like an employer with no open jobs.
      * limit is capped at 200 server-side. Asking for 500 returns 200 and
        no error, so a loop that trusted its own page size would stop at
        200 of 2919 and never say why. Page until offset >= total.

    These are national employers, so most of what comes back is thousands
    of miles away. Filtering to California here rather than in geo is not a
    second geo filter — it is the difference between fetching detail for 60
    in-state nurse postings and paying for 900 nationwide ones. A posting
    is kept when EITHER its primary or any secondary location is in CA, so
    a job listed in Phoenix but also open in San Ramon still reaches geo.
    """

    PAGE = 200          # server-side cap, not a preference
    LIST = ("/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
            "?onlyData=true&expand=requisitionList.secondaryLocations"
            "&finder=findReqs;siteNumber={site},limit={limit},offset={offset}")
    DETAIL = ("/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
              "?expand=all&onlyData=true"
              "&finder=ById;Id=%22{rid}%22,siteNumber={site}")

    _CA = re.compile(r",\s*CA\s*,|,\s*California\b", re.I)

    def __init__(self, employer: str, host: str, site: str = "CX_1",
                 setting: str | None = None):
        self.employer = employer
        self.host = host
        self.site = site
        self.setting = setting

    def _in_california(self, r: dict) -> bool:
        places = [r.get("PrimaryLocation") or ""]
        places += [s.get("Location") or s.get("Name") or ""
                   for s in (r.get("secondaryLocations") or [])
                   if isinstance(s, dict)]
        return any(self._CA.search(p) for p in places)

    @staticmethod
    def _city(place: str) -> str:
        """
        'San Ramon, CA, United States' -> 'San Ramon, CA'.

        geo matches whole city phrases, and the trailing ", United States"
        is harmless to it, but the ledger and the digest show this string to
        a human. Trim it there rather than teaching geo about countries.
        """
        parts = [x.strip() for x in (place or "").split(",")]
        return ", ".join(parts[:2]) if len(parts) >= 2 else (place or "")

    def _url(self, rid: str) -> str:
        """
        Always the Oracle-hosted candidate page, never the employer's
        branded careers domain.

        The branded one is tempting and wrong: Tenet's site really is at
        jobs.tenethealth.com, but it keys its URLs on a Radancy job id
        (97770889200) that has no relation to the Oracle requisition id
        (2603016285) this API returns. Composing the branded URL from the
        Oracle id produced a clean-looking link that 404s — a dead link on
        a job posting costs exactly what a false "no experience required"
        costs, an application that was never possible. The Oracle page
        resolves for every tenant here and redirects to the right site
        number on its own.
        """
        return (f"https://{self.host}/hcmUI/CandidateExperience/en/sites/"
                f"{self.site}/job/{rid}")

    # 200 pages x 200 = 40,000 postings of headroom. The loop already
    # exits on `offset >= total`, so this only needs to be high enough
    # never to be the thing that stops it — Workday's page cap silently
    # hid 202 Sutter postings by being exactly that thing.
    def fetch_listings(self, max_pages: int = 200) -> list[Posting]:
        out: list[Posting] = []
        offset, total = 0, None
        for _ in range(max_pages):
            url = "https://" + self.host + self.LIST.format(
                site=self.site, limit=self.PAGE, offset=offset)
            d = json.loads(_request(url))
            items = d.get("items") or [{}]
            head = items[0] if items else {}
            if total is None:
                total = head.get("TotalJobsCount") or 0
            batch = head.get("requisitionList") or []
            if not batch:
                break
            for r in batch:
                if not self._in_california(r):
                    continue
                rid = str(r.get("Id") or "")
                if not rid:
                    continue
                out.append(Posting(
                    employer=self.employer,
                    req_id=rid,
                    title=r.get("Title") or "",
                    location=self._city(r.get("PrimaryLocation") or ""),
                    url=self._url(rid),
                    posted_date=r.get("PostedDate"),
                    schedule=r.get("JobSchedule"),
                    shift=r.get("JobShift"),
                    setting=self.setting,
                    source_adapter=f"oracleorc:{self.host.split('.')[0]}",
                ))
            offset += len(batch)
            if len(batch) < self.PAGE:
                break
            if total and offset >= total:
                break
        else:
            # Fell out of the loop on max_pages rather than on the total.
            # Say so: three silent truncations in this codebase were found
            # by comparing an endpoint's own total against what was
            # actually collected, and none of them announced themselves.
            print(f"     !! {self.employer}: stopped at the page cap with "
                  f"{offset} of {total} postings read")
        return out

    def fetch_detail(self, p: Posting) -> Posting:
        url = "https://" + self.host + self.DETAIL.format(
            rid=p.req_id, site=self.site)
        items = json.loads(_request(url)).get("items") or []
        if not items:
            return p
        d = items[0]
        # Three separate fields, and the requirement sentence lives in
        # whichever one the employer chose to type it into — UCSF puts
        # "Required Qualifications:" in ExternalQualificationsStr, Tenet
        # writes it into the description body. Join them and let the
        # classifier find it. The headings are the employer's own; nothing
        # is invented here, because a fabricated "Required Qualification:"
        # prefix is exactly what produced a verdict quoting a heading as
        # its evidence when St. Rose was added.
        parts = [d.get("ExternalDescriptionStr") or "",
                 d.get("ExternalResponsibilitiesStr") or "",
                 d.get("ExternalQualificationsStr") or ""]
        p.description = " ".join(_html_to_text(x) for x in parts if x).strip()
        p.posted_date = d.get("PostedDate") or p.posted_date
        p.schedule = d.get("JobSchedule") or p.schedule
        p.shift = d.get("JobShift") or p.shift
        return p


# ── adapter 12: Radancy TalentBrew (CommonSpirit / Dignity Health) ────

class Radancy:
    """
    CommonSpirit Health — Dignity Health in this half of the state — was the
    largest hospital system with no adapter at all. It reaches, within range:
    Saint Francis Memorial and St. Mary's in San Francisco, Sequoia in
    Redwood City, Dominican in Santa Cruz, St. Joseph's in Stockton, Woodland
    Memorial, and the Mercy hospitals around Sacramento and Folsom.

    Its ATS is iCIMS, which is closed here — careers-commonspirit.icims.com
    answers a search with 156 bytes and no rows. The Radancy TalentBrew front
    end in front of it is fully server-rendered, and this reads that.

    Getting a filtered list out of it is the whole problem. The obvious
    endpoint, /search-jobs/results with Keywords and Location, accepts both
    and ignores both: a search for nurses near San Ramon returns page 1 of
    every job the company has, starting in San Antonio. Passing latitude and
    longitude instead returns `{"results": ""}`. What does work is the
    per-city page the site links from its own sitemap:

        /location/{city}-california-united-states-jobs/{brand}/{geo ids}/4

    So the sitemap is the index. Every California city page it lists is
    checked against geo's own city table and fetched only if it is inside
    the two-hour ring — which is why this adapter names no cities of its
    own. Add a city to geo and this source starts reporting it.

    Two page templates are in the wild and both appear on CommonSpirit
    pages, so the row parser keys on `data-job-id` and reads the fields out
    of the segment that follows it. Keying on the outer <li> instead looks
    tidier and silently loses the facility and location on the newer
    template, whose job-info fields are themselves <li> elements nested
    inside that outer one.
    """

    SITEMAP = "/sitemap.xml"
    _LOC_URL = re.compile(
        r"https://[^<\s]+/location/([a-z0-9-]+)-california-united-states-jobs/[^<\s]+")
    # One anchor, both attribute orders, capturing the link, the id and the
    # anchor's own inner HTML. Reading the title out of the anchor is the
    # point: an earlier version searched for the nearest <h2> in a window
    # around the anchor, and on a Redwood City page that returned the
    # titles "Filter Results" and "Related Content" — the page's own
    # furniture — for two of sixteen rows.
    _ROW = re.compile(
        r'<a\b[^>]*?href="(/job/[^"]+)"[^>]*?data-job-id="([^"]+)"[^>]*>(.*?)</a>'
        r'|<a\b[^>]*?data-job-id="([^"]+)"[^>]*?href="(/job/[^"]+)"[^>]*>(.*?)</a>',
        re.S)
    # The results list, so "jobs you might also like" from Bismarck, North
    # Dakota do not get filed as Redwood City postings. geo would drop them
    # anyway; they should not become Postings in the first place.
    _LIST_START = re.compile(
        r'id="(?:search-results-jobs|search-results-list)"')

    # 15 rows a page, so 40 pages is 600 postings in a single city — far
    # past anything in range (Redwood City, the busiest so far, is 3
    # pages). Like every other cap in this file it exists only so a broken
    # next-link cannot loop forever, never to be the thing that stops a
    # sweep; when it *is* the thing that stops one, the loop says so.
    MAX_PAGES = 40

    def __init__(self, employer: str, host: str, setting: str | None = None):
        self.employer = employer
        self.host = host
        self.setting = setting
        # Cities whose page failed this run, so the scan can tell "this
        # employer has nothing here today" apart from "we could not look".
        self.failed_cities: set[str] = set()

    @staticmethod
    def _text(fragment: str) -> str:
        return html.unescape(
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment))).strip()

    def _city_pages(self) -> list[tuple[str, str]]:
        """(city, url) for every California city page geo places in range."""
        body = _request(f"https://{self.host}{self.SITEMAP}")
        out, seen = [], set()
        for m in self._LOC_URL.finditer(body):
            url, slug = m.group(0), m.group(1)
            city = slug.replace("-", " ")
            if city in seen:
                continue
            seen.add(city)
            verdict, _bucket, _mi = geo.classify(city)
            if verdict is geo.Geo.IN:
                out.append((city, url))
        return sorted(out)

    def _rows(self, body: str, city: str) -> list[Posting]:
        m = self._LIST_START.search(body)
        if m:
            body = body[m.start():]
        out = []
        matches = list(self._ROW.finditer(body))
        for i, m in enumerate(matches):
            href, jid, inner = (m.group(1), m.group(2), m.group(3))
            if href is None:
                href, jid, inner = (m.group(5), m.group(4), m.group(6))
            # Tenet nests the title in an <h2> inside the anchor alongside
            # the facility and location spans; CommonSpirit puts the anchor
            # inside the <h2> and its text is the title alone. Taking the
            # <h2> when there is one covers both without a template flag.
            h2 = re.search(r"<h2[^>]*>(.*?)</h2>", inner, re.S)
            title = self._text(h2.group(1) if h2 else inner)
            if not title:
                continue
            # Fields live between this anchor and the next one.
            end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
            seg = inner + body[m.end():end]

            def field(cls):
                f = re.search(r"job-" + cls + r'"?[^>]*>(.*?)<', seg, re.S)
                return self._text(f.group(1)) if f else ""

            out.append(Posting(
                employer=self.employer,
                req_id=jid,
                title=title,
                # The city page is the authority. Some rows print no
                # location at all, and defaulting those to blank would send
                # a perfectly locatable posting to the review bucket.
                location=field("location") or f"{city.title()}, CA",
                url=f"https://{self.host}{html.unescape(href)}",
                department=field("department") or None,
                setting=self.setting,
                source_adapter=f"radancy:{self.host.split('.')[1]}",
            ))
        return out

    # Page 2 is a path suffix, not a query parameter, and the suffix goes
    # on a DIFFERENT path than the sitemap gives you: the sitemap lists
    # /location/redwood-city-california-united-states-jobs/.../4 and the
    # site's own next-link is /location/redwood-city-jobs/.../4/2.
    # Appending "&p=2" to the sitemap URL returns HTTP 200 with an empty
    # result list, which reads as "that was the last page" — it silently
    # collected 15 of Redwood City's 25 postings. So follow the next-link
    # the page prints rather than composing one.
    _NEXT = re.compile(r'<a[^>]*class="next"[^>]*href="([^"]+)"', re.I)
    _TOTAL_PAGES = re.compile(r'data-total-pages="(\d+)"')

    def fetch_listings(self) -> list[Posting]:
        out: list[Posting] = []
        self.failed_cities = set()
        for city, url in self._city_pages():
            seen: set[str] = set()
            target, pages, expected = url, 0, None
            while target and pages < self.MAX_PAGES:
                try:
                    body = _request(target)
                except Exception as e:                      # noqa: BLE001
                    print(f"     {self.employer} {city} page {pages + 1}: {e}")
                    self.failed_cities.add(city)
                    break
                pages += 1
                if expected is None:
                    m = self._TOTAL_PAGES.search(body)
                    expected = int(m.group(1)) if m else 1
                for p in self._rows(body, city):
                    if p.req_id in seen:
                        continue
                    seen.add(p.req_id)
                    out.append(p)
                nxt = self._NEXT.search(body)
                target = (f"https://{self.host}{html.unescape(nxt.group(1))}"
                          if nxt else None)
            else:
                if target:
                    print(f"     !! {self.employer} {city}: stopped at the "
                          f"page cap after {pages} of {expected} pages")
        return out

    def covered_employers(self) -> set[str]:
        """
        All-or-nothing. This adapter reads one employer across many city
        pages, so a single city that failed leaves us unable to say which
        of that employer's postings are gone and which we simply did not
        fetch. Report nothing covered and let the rows stand.
        """
        return set() if self.failed_cities else {self.employer}

    def all_employers(self) -> set[str]:
        return {self.employer}

    def fetch_detail(self, p: Posting) -> Posting:
        body = _request(p.url)
        # The posting body is the one JSON-LD block on the page. Prefer it
        # to the surrounding markup: it is the employer's own text, already
        # delimited, with no navigation in it.
        m = re.search(r'<script type="application/ld\+json">(.*?)</script>',
                      body, re.S)
        if m:
            try:
                d = json.loads(m.group(1))
                # Unescape first — the field arrives with its markup
                # escaped, so stripping before unescaping strips nothing —
                # then convert with the block-aware helper so a bulleted
                # requirements list does not collapse into one run-on.
                raw = html.unescape(d.get("description", "") or "")
                p.description = _html_to_text(raw)
                p.posted_date = d.get("datePosted") or p.posted_date
                p.schedule = d.get("employmentType") or p.schedule
                return p
            except json.JSONDecodeError:
                pass
        m = re.search(r'<div[^>]*class="[^"]*job-description[^"]*"[^>]*>(.*?)</div>',
                      body, re.S)
        if m:
            p.description = self._text(m.group(1))
        return p


# ── adapter 13: JobAps (San Joaquin County) ──────────────────────────

class JobAps:
    """
    JobAps is the third CA-government HR platform, after NEOGOV and
    SmartRecruiters. San Joaquin County is on it, and San Joaquin General
    Hospital in French Camp — a county hospital inside the ring — posts
    there and nowhere else this scan reads.

    It was nearly missed for the reason CLAUDE.md already warns about:
    the search for it started from "which NEOGOV slug is San Joaquin?"
    The answer looked like "sjcounty", which returns a real, populated,
    plausible board — for San Juan County, Utah. Ask which hospitals are
    in range, then find each one's platform; never assume the platform.

    The listing is the agency's landing page. Everything is in it already:
    one table row per open requisition with title, requisition number,
    city and department, no paging and no JSON behind it.
    """

    BASE = "https://www.jobapscloud.com"
    _ROW = re.compile(
        r'<th[^>]*class="JobTitle"[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*'
        r'class="JobTitle"[^>]*>(.*?)</a>\s*<a[^>]*class="JobNum"[^>]*>'
        r'(.*?)</a>(.*?)</tr>', re.S | re.I)

    def __init__(self, employer="San Joaquin County", agency="SJQ",
                 default_city="Stockton"):
        self.employer = employer
        self.agency = agency
        # Used only when a row prints no city of its own. The county seat
        # is Stockton; San Joaquin General is in French Camp and says so,
        # which is why the row's own value always wins.
        self.default_city = default_city

    @staticmethod
    def _text(fragment: str) -> str:
        # The trailing-fragment strip is not decoration. JobAps writes
        # `<td class="Locs">French Camp<br </td>`, and that <br is never
        # closed before the cell ends, so a plain <[^>]+> strip leaves
        # "French Camp<br" — which geo cannot match, sending a French Camp
        # posting to the review bucket instead of the 60-90 bucket.
        fragment = re.sub(r"<[^>]*$", " ", fragment)
        return html.unescape(
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment))).strip()

    def _cell(self, chunk: str, cls: str) -> str:
        m = re.search(r'<td[^>]*class="' + cls + r'"[^>]*>(.*?)</td>',
                      chunk, re.S | re.I)
        return self._text(m.group(1)) if m else ""

    def fetch_listings(self) -> list[Posting]:
        body = _request(f"{self.BASE}/{self.agency}/")
        out, seen = [], set()
        for m in self._ROW.finditer(body):
            href, title, num, rest = m.groups()
            req = self._text(num)
            title = self._text(title)
            if not req or req in seen:
                continue
            seen.add(req)
            url = html.unescape(href)
            if url.startswith("/"):
                url = self.BASE + url
            out.append(Posting(
                employer=self.employer,
                req_id=req,
                title=title,
                location=self._cell(rest, "Locs") or self.default_city,
                url=url,
                department=self._cell(rest, "Dept") or None,
                source_adapter=f"jobaps:{self.agency.lower()}",
            ))
        return out

    def fetch_detail(self, p: Posting) -> Posting:
        body = _request(p.url)
        # The bulletin is the whole page; strip the chrome by taking the
        # main content container when there is one and the body otherwise.
        m = re.search(r'<div[^>]*id="bulletin"[^>]*>(.*?)</div>\s*</div>',
                      body, re.S | re.I)
        chunk = m.group(1) if m else body
        chunk = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>",
                       " ", chunk)
        p.description = _html_to_text(chunk)
        return p


# ── adapter 14: Paylocity (Central Valley Specialty Hospital) ────────

class Paylocity:
    """
    Paylocity Recruiting, the ATS a lot of small independent employers
    use. Here it reaches Central Valley Specialty Hospital in Modesto —
    a long-term acute care hospital, in range at 60-90 minutes, and the
    only LTAC in the ring that no other adapter touches.

    LTAC matters to this user specifically and the coverage was thinner
    than it looked. Of the four LTACs inside two hours, Kindred (San
    Leandro) and Kentfield (Marin) are read by other adapters and both
    routinely sit at zero open RN roles, and Vibra's are 90-120 minutes
    out in Folsom. So on a normal day the list showed no LTAC at all
    while a Modesto LTAC was hiring RNs and saying "We encourage new RNs
    to apply" in the posting.

    The board is one GET: the page embeds its whole job list as JSON
    under "Jobs", with the city and state in a nested JobLocation object.

    Do NOT classify from the listing. The Description carried there is a
    110-character teaser, and a truncated description is what produced 40
    false "no experience required" verdicts when Sutter was read through
    its Phenom front end. The real text is on the detail page, inside the
    job-preview-details container.
    """

    HOST = "https://recruiting.paylocity.com"
    _JOBS = re.compile(r'"Jobs"\s*:\s*(\[.*?\])\s*[,}]', re.S)

    def __init__(self, employer: str, board_url: str,
                 setting: str | None = None):
        self.employer = employer
        self.board_url = board_url
        self.setting = setting

    def fetch_listings(self) -> list[Posting]:
        body = _request(self.board_url)
        m = self._JOBS.search(body)
        if not m:
            raise RuntimeError("no embedded Jobs array on the Paylocity board")
        out = []
        for j in json.loads(m.group(1)):
            jid = str(j.get("JobId") or "")
            if not jid:
                continue
            loc = j.get("JobLocation") or {}
            city, state = loc.get("City") or "", loc.get("State") or ""
            out.append(Posting(
                employer=self.employer,
                req_id=jid,
                title=j.get("JobTitle") or "",
                # LocationName is "On Site" or "Main Office" on this board,
                # which geo cannot rank. The nested address is the real one.
                location=", ".join(x for x in (city, state) if x),
                url=f"{self.HOST}/recruiting/jobs/Details/{jid}",
                posted_date=(j.get("PublishedDate") or "")[:10] or None,
                department=j.get("HiringDepartment") or None,
                setting=self.setting,
                source_adapter="paylocity",
            ))
        return out

    def fetch_detail(self, p: Posting) -> Posting:
        body = _request(p.url)
        i = body.find("job-preview-details")
        chunk = body[i:i + 40000] if i >= 0 else body
        chunk = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>",
                       " ", chunk)
        p.description = _html_to_text(chunk)
        return p


ADAPTERS = [
    WorkdayCXS("John Muir Health", "jmh.wd5.myworkdayjobs.com",
               "jmh", "JohnMuirHealthCareers"),          # verified
    # Sutter via its real Workday tenant, discovered from an apply URL.
    # The Phenom scraper below returned 320-char marketing teasers with no
    # requirements section, which made requirement classification impossible
    # and produced 40 false "no experience required" verdicts.
    WorkdayCXS("Sutter Health", "wd1.myworkdaysite.com", "sutterhealth", "SH",
               url_prefix="/recruiting/sutterhealth/SH"),   # verified
    WorkdayCXS("El Camino Health", "ech.wd5.myworkdayjobs.com",
               "ech", "ech"),                             # verified — 80 postings
    HealthcareSource(),                                   # verified — Alameda Health
    PACS(),                                               # verified — post-acute, 112 CA RN roles
    ScionHealth(),                                        # verified — Kindred LTAC, San Leandro
    NeoGov(),                                             # verified — 6 CA county/city agencies, 220 postings
    Jibe(),                                               # verified — Vibra/Kentfield LTAC, 87 CA postings
    SmartRecruiters(),                                    # verified — SF DPH + citywide, 182 postings
    SmartHires(),                                         # verified — St. Rose Hospital, Hayward

    # ---- added 2026-09-09, after a San Ramon posting reached the user
    # from outside every source above. All verified against live endpoints
    # on the day they were added; the counts are that day's.
    WorkdayCXS("MarinHealth", "mymarinhealth.wd5.myworkdayjobs.com",
               "mymarinhealth", "MHCareers"),             # verified — 148 postings
    WorkdayCXS("Salinas Valley Health",
               "salinasvalleyhealth.wd5.myworkdayjobs.com",
               "salinasvalleyhealth", "SalinasValleyHealth"),  # verified — 95 postings

    # Oracle Recruiting Cloud. UCSF is the one that mattered most: 28 RN
    # postings in San Francisco and Oakland, none of which any adapter
    # above could see. Tenet is what finally reaches San Ramon Regional.
    OracleORC("UCSF Health", "iazuqy.fa.ocs.oraclecloud.com"),      # 822 postings
    OracleORC("Tenet Health", "eodr.fa.us2.oraclecloud.com"),       # 2919 — San Ramon
    OracleORC("Providence", "evac.fa.us2.oraclecloud.com"),         # 2050 — Napa, Santa Rosa
    OracleORC("Adventist Health", "ecvz.fa.us2.oraclecloud.com"),   # 1427
    OracleORC("NorthBay Health", "erou.fa.us2.oraclecloud.com"),    # 124 — Fairfield

    # CommonSpirit / Dignity: Sequoia, Dominican, St. Joseph's Stockton,
    # Woodland, Mercy. Read through the Radancy front end because the
    # iCIMS ATS behind it is closed.
    Radancy("CommonSpirit / Dignity Health", "www.commonspirit.careers"),

    # San Joaquin General Hospital, French Camp. Not on NEOGOV: the slug
    # that looks like it ("sjcounty") is San Juan County, Utah.
    JobAps(),                                             # verified — 30 nurse rows

    # Central Valley Specialty Hospital, Modesto — long-term acute care.
    # The only LTAC in range that no other adapter reaches.
    Paylocity("Central Valley Specialty Hospital",
              "https://recruiting.paylocity.com/recruiting/jobs/All/"
              "59573989-59eb-4885-ac33-ae95e3c92fb2/Central-Valley-Special",
              setting="Long-term acute care"),   # verified — 24 postings

    USAJobs(),                                            # UNTESTED — needs USAJOBS_KEY

    # Genuinely blocked, checked 2026-09-09. Do not re-probe these without
    # a browser; each was tried with full browser headers and failed:
    #   HCA Healthcare (Good Samaritan San Jose, Regional Medical Center)
    #     — Cloudflare interstitial on every path including robots.txt.
    #       This is a real gap: Good Samaritan is a hospital the user
    #       named. It needs Playwright, same as CalCareers.
    #   Washington Hospital Healthcare System (Fremont) — 403 on whhs.com.
    #   CalCareers / CDCR — ASP.NET WebForms behind DevExpress callbacks.
]


def run(fetch_details: bool = True):
    """
    listings -> title prefilter -> geo filter -> detail fetch.

    Geo runs BEFORE detail on purpose: no reason to pay for a detail
    request on a Crescent City posting.

    Returns (in_range, needs_review). Out-of-range postings are counted
    and discarded.
    """
    kept: dict[str, Posting] = {}
    review: dict[str, Posting] = {}
    for ad in ADAPTERS:
        name = type(ad).__name__
        try:
            listings = ad.fetch_listings()
        except Exception as e:                          # noqa: BLE001
            print(f"  !! {ad.employer} ({name}) FAILED: {e}")
            continue

        passed = [p for p in listings if title_passes(p.title)]
        in_range, needs_review, out_of_range = geo.partition(passed)
        print(f"  {ad.employer}: {len(listings)} listings -> "
              f"{len(passed)} nurse titles -> {len(in_range)} in range "
              f"({len(out_of_range)} too far, {len(needs_review)} to review)")

        for p in in_range:
            if p.key in kept:                            # dedupe
                continue
            if fetch_details:
                try:
                    p = ad.fetch_detail(p)
                except Exception as e:                   # noqa: BLE001
                    print(f"     detail failed {p.req_id}: {e}")
            kept[p.key] = p
        for p in needs_review:
            review.setdefault(p.key, p)
    return list(kept.values()), list(review.values())


if __name__ == "__main__":
    print("Scanning...")
    rows, review = run()
    print(f"\n{len(rows)} in-range postings ready for the classifier")
    print(f"{len(review)} postings need a location review\n")
    for p in sorted(rows, key=lambda x: (x.drive_time_bucket or "", x.employer))[:18]:
        print(f"  [{p.drive_time_bucket:>6} min] {p.title[:62]}")
        print(f"                {p.employer} — {p.location}")
    if review:
        print("\n  NEEDS REVIEW (unrecognised location):")
        for p in review[:8]:
            mi = f"{p.straight_line_mi} mi" if p.straight_line_mi else "no coords"
            print(f"    {p.location!r} ({mi}) — {p.title[:50]}")
    with open("postings.jsonl", "w") as f:
        for p in rows:
            f.write(json.dumps(asdict(p)) + "\n")
    with open("needs_review.jsonl", "w") as f:
        for p in review:
            f.write(json.dumps(asdict(p)) + "\n")
    print("\nwrote postings.jsonl + needs_review.jsonl")
