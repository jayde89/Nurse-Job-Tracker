"""
RN Job Scanner — source adapters.

Nine adapters, all verified against live endpoints 2026-09-03:

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
    r"\b(LVN|LPN|nursing assistant|medical assistant|nurse practitioner"
    r"|CRNA|nurse anesthetist|clinical nurse specialist"
    r"|manager|director|supervisor|educator|informatics|analyst"
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
    }

    def __init__(self, employer="CA counties & cities (NEOGOV)", agencies=None):
        # `employer` labels the scan log only; each Posting carries the
        # agency that actually posted it.
        self.employer = employer
        self.agencies = agencies or self.AGENCIES

    @staticmethod
    def _text(fragment: str) -> str:
        return html.unescape(re.sub(r"\s+", " ",
                                    re.sub(r"<[^>]+>", " ", fragment))).strip()

    def fetch_listings(self) -> list[Posting]:
        out: list[Posting] = []
        for slug, (name, city) in self.agencies.items():
            seen: set[str] = set()
            for page in range(1, self.MAX_PAGES + 1):
                url = f"{self.BASE}/{slug}?page={page}{self.SORT}"
                try:
                    body = _request(url, headers=self.XHR)
                except Exception as e:                      # noqa: BLE001
                    print(f"     {name} page {page}: {e}")
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
        return out

    def fetch_detail(self, p: Posting) -> Posting:
        body = _request(p.url)
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
    NeoGov(),                                             # verified — 6 CA county/city agencies, 220 postings
    Jibe(),                                               # verified — Vibra/Kentfield LTAC, 87 CA postings
    SmartRecruiters(),                                    # verified — SF DPH + citywide, 182 postings
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
