"""
Geographic filter — "within 2 hours' drive of Oakland".

Why this isn't a radius check: Crescent City is 350 mi and 6 hrs away, which
a radius catches fine. But Gilroy is 80 mi and routinely 2.5 hrs southbound,
while Sacramento is 85 mi and 90 minutes. Distance and drive time disagree
badly in this region, so the primary mechanism is a curated city table.

Three outcomes, and the third one matters:
    IN      — city is in the table, within 2 hrs
    OUT     — city is in the table, beyond 2 hrs
    UNKNOWN — city is not in the table

UNKNOWN postings are NOT silently dropped. They go to a review bucket so you
find out about the city rather than never seeing the job. Haversine distance
is attached as a hint to help you triage them quickly.

City names are matched as whole phrases, longest first. This matters more
than it sounds: an earlier version split names on whitespace, which put the
bare tokens "creek" (from Sutter Creek) and "santa" (from Santa Maria) into
the out-of-range set, and that silently rejected Walnut Creek and Santa Rosa.
Never tokenize a gazetteer.

The buckets are drive-time estimates under normal conditions, not live
traffic. To use real numbers later, swap `classify` for a routing API call —
nothing downstream depends on how the bucket was derived.
"""

from __future__ import annotations

import math
import re
from enum import Enum

ORIGIN = (37.8044, -122.2712)   # Oakland, CA


class Geo(Enum):
    IN = "in_range"
    OUT = "out_of_range"
    UNKNOWN = "needs_review"


def _csv(s):
    return [x.strip() for x in s.split(",") if x.strip()]


IN_CITIES: dict[str, str] = {}

for _bucket, _names in [
    ("<30", """oakland, berkeley, alameda, emeryville, piedmont, albany,
        san leandro, el cerrito, richmond, el sobrante, san pablo, orinda,
        lafayette, moraga, castro valley, walnut creek, san francisco,
        hayward, danville, alamo, kensington"""),
    ("30-60", """fremont, newark, union city, milpitas, pleasanton, dublin,
        livermore, san ramon, concord, pleasant hill, martinez, clayton,
        antioch, pittsburg, brentwood, oakley, vallejo, benicia, daly city,
        brisbane, south san francisco, san bruno, millbrae, burlingame,
        san mateo, foster city, belmont, san carlos, redwood city, atherton,
        menlo park, palo alto, east palo alto, mountain view, los altos,
        sunnyvale, santa clara, san rafael, larkspur, greenbrae, corte madera,
        mill valley, sausalito, novato, tiburon, tracy, fairfield, suisun city,
        hercules, pinole, discovery bay"""),
    ("60-90", """san jose, campbell, los gatos, saratoga, cupertino,
        morgan hill, vacaville, dixon, napa, american canyon, yountville,
        sonoma, petaluma, cotati, stockton, french camp, manteca, lathrop,
        ripon, lodi, galt, modesto, ceres, riverbank, davis, west sacramento,
        sacramento, woodland, rio vista, isleton, santa cruz, scotts valley,
        capitola, soquel, aptos, rancho cordova, vine hill"""),
    ("90-120", """santa rosa, rohnert park, sebastopol, windsor, healdsburg,
        st helena, saint helena, calistoga, angwin, roseville, rocklin,
        lincoln, folsom, citrus heights, carmichael, fair oaks, orangevale,
        elk grove, turlock, watsonville, freedom, gilroy, salinas, soledad,
        marina, seaside, monterey, pacific grove, auburn, placerville,
        merced, hollister"""),
]:
    for _n in _csv(_names):
        IN_CITIES[_n] = _bucket

OUT_CITIES: set[str] = set(_csv("""
    crescent city, eureka, arcata, mckinleyville, fortuna, ukiah, willits,
    lakeport, clearlake, clear lake, kelseyville, middletown, mendocino,
    fort bragg, redding, chico, oroville, paradise, red bluff, yuba city,
    marysville, colusa, willows, corning, susanville, jackson, sutter creek,
    ione, san andreas, angels camp, sonora, truckee, tahoe city,
    south lake tahoe, grass valley, nevada city, los banos, dos palos,
    gustine, madera, fresno, clovis, visalia, hanford, bakersfield,
    paso robles, atascadero, san luis obispo, santa maria, lompoc,
    santa barbara, ventura, oxnard, burbank, los angeles, pasadena,
    long beach, anaheim, irvine, riverside, san bernardino, san diego,
    chula vista, bishop, barstow, reno, las vegas, portland, seattle,
    el cajon, encinitas, oceanside, poway, la mesa, escondido, carlsbad,
    santa monica, west hills, chatsworth, el monte, thousand oaks,
    huntington beach, artesia, lancaster, palm desert, palm springs,
    rancho mirage, redlands, highland, san marcos, vista, national city,
    porterville, lindsay, tulare, clovis, selma, delano, taft, shafter,
    live oak, anderson, palo cedro, cottonwood, weaverville, hollywood,
    loma linda, ojai,
    van nuys, northridge, glendale, torrance, inglewood, downey, whittier,
    pomona, ontario, fontana, rialto, corona, temecula, murrieta
"""))

# Longest first so "sutter creek" is tested before "creek"-containing names
# and "south san francisco" before "san francisco".
_ORDERED: list[tuple[str, Geo, str | None]] = sorted(
    [(n, Geo.OUT, None) for n in OUT_CITIES]
    + [(n, Geo.IN, b) for n, b in IN_CITIES.items()],
    key=lambda t: -len(t[0]))


def haversine_mi(lat, lon) -> float | None:
    if lat is None or lon is None:
        return None
    lat1, lon1 = map(math.radians, ORIGIN)
    lat2, lon2 = math.radians(lat), math.radians(lon)
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 3958.8 * 2 * math.asin(math.sqrt(a))


def _normalize(location: str) -> str:
    s = (location or "").lower()
    s = re.sub(r"[^a-z\s]", " ", s)
    s = re.sub(r"\b(california|ca|usa|us|united states)\b", " ", s)
    return " " + re.sub(r"\s+", " ", s).strip() + " "


def classify(location: str, lat=None, lon=None):
    """Returns (verdict, drive_time_bucket, straight_line_miles)."""
    miles = haversine_mi(lat, lon)
    norm = _normalize(location)

    for name, verdict, bucket in _ORDERED:
        if f" {name} " in norm:
            return verdict, bucket, miles

    # No city matched — a street address, or a place not in the table.
    # Coordinates can still settle the obvious cases.
    if miles is not None:
        if miles > 130:
            return Geo.OUT, None, miles
        if miles < 25:
            return Geo.IN, "<30", miles
    return Geo.UNKNOWN, None, miles


def partition(postings):
    """Split postings into (in_range, needs_review, out_of_range)."""
    keep, review, drop = [], [], []
    for p in postings:
        verdict, bucket, miles = classify(
            p.location, getattr(p, "latitude", None), getattr(p, "longitude", None))
        p.drive_time_bucket = bucket
        p.straight_line_mi = round(miles, 1) if miles is not None else None
        p.geo_verdict = verdict.value
        {Geo.IN: keep, Geo.UNKNOWN: review, Geo.OUT: drop}[verdict].append(p)
    return keep, review, drop
