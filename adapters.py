"""
RN Job Scanner — source adapters.

Two adapters, both verified working 2026-09-01:

  WorkdayCXS   — native Workday tenants. One class, N tenants.
                 Verified against John Muir Health.
  SutterPhenom — Sutter's Phenom front-end. Its JSON API rejects
                 anonymous callers, but the search page embeds its
                 results server-side, so we parse those.

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

INCLUDE_TITLE = re.compile(
    r"\b(RN|R\.N\.|registered nurse|staff nurse|clinical nurse|nurse resident"
    r"|new grad(uate)?|graduate nurse)\b", re.I)

EXCLUDE_TITLE = re.compile(
    r"\b(LVN|LPN|CNA|nursing assistant|medical assistant|nurse practitioner|NP"
    r"|CRNA|nurse anesthetist|CNS|clinical nurse specialist"
    r"|manager|director|supervisor|educator|informatics|analyst"
    r"|travel|per[- ]diem agency|locum"
    r"|student|intern|volunteer|extern)\b", re.I)


def title_passes(title: str) -> bool:
    return bool(INCLUDE_TITLE.search(title)) and not EXCLUDE_TITLE.search(title)


# ── http ─────────────────────────────────────────────────────────────

# Browser-shaped Accept. Some portals (Alameda Health's especially) return a
# truncated stub page if you announce "Accept: application/json" — 8 KB
# instead of 479 KB, with the data blob stripped out. Look like a browser.
ACCEPT = ("text/html,application/xhtml+xml,application/xml;q=0.9,"
          "application/json;q=0.8,*/*;q=0.7")


def _request(url, data=None, headers=None):
    hdrs = {"User-Agent": UA, "Accept": ACCEPT,
            "Accept-Language": "en-US,en;q=0.9"}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    hdrs.update(headers or {})
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs)
            with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
                body = r.read().decode("utf-8", "replace")
            time.sleep(REQUEST_DELAY_SEC)
            return body
        except Exception as e:                      # noqa: BLE001
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"failed after {MAX_RETRIES} tries: {url} ({last})")


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

    def fetch_listings(self, max_pages: int = 50) -> list[Posting]:
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
        return out

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
        try:
            with open(self.cache_path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {"facilities": {}}

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


# ── registry ─────────────────────────────────────────────────────────
# Kaiser Permanente and Stanford Health Care are excluded by request.

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
    USAJobs(),                                            # UNTESTED — needs USAJOBS_KEY
    # Add once host/site confirmed via DevTools:
    #   WorkdayCXS("MarinHealth", ...)
    #   WorkdayCXS("NorthBay Health", ...)
    #   WorkdayCXS("Washington Hospital Healthcare System", ...)
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
