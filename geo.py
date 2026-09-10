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
        ripon, lodi, galt, modesto, ceres, riverbank, salida, davis, west sacramento,
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

# Added 2026-09-09, in its own block for the same reason the OUT block
# below is: _csv splits on commas and nothing else, so a comment written
# inside one of those strings becomes a city name.
#
# These are places inside the ring that the table simply did not know.
# Every one of them was reaching `classify` as UNKNOWN, which does not
# lose a posting — the review bucket exists for exactly this — but a
# review list nobody reads is a review list that hides things, and one of
# these was load-bearing: Kentfield is a long-term acute care hospital
# this scan already reads through Vibra's board, and the user asked for
# LTAC by name. Its postings could never be ranked.
#
# Names that are also a place somewhere else are deliberately left out
# rather than guessed at, because a wrong IN is a silent wrong answer
# while an UNKNOWN is a question. "Ashland" is in Alameda County and in
# Oregon, and this scan reads an employer with Oregon programs; "Carmel"
# is in Monterey County and in Indiana; "Empire" is in Stanislaus County
# and inside "Inland Empire"; "Hillsborough" is on the Peninsula and in
# three other states. Those stay in review.
for _bucket, _names in [
    ("<30", """san lorenzo, cherryland, fairview, north richmond"""),
    ("30-60", """kentfield, ross, san anselmo, fairfax, san quentin,
        pacifica, colma, half moon bay, montara, el granada, moss beach,
        woodside, portola valley, redwood shores, alviso, los altos hills,
        stanford, sunol, bay point, pacheco, crockett, rodeo, blackhawk,
        byron, knightsen, bethel island, cordelia, mountain house"""),
    ("60-90", """bolinas, point reyes station, glen ellen, kenwood,
        boyes hot springs, penngrove, coyote, san martin, monte sereno,
        escalon, linden, lockeford, woodbridge, thornton, banta, patterson,
        farmington, walnut grove, courtland, clarksburg, arden arcade,
        north highlands, rio linda, gold river, mather, mcclellan,
        oakville, rutherford"""),
    ("90-120", """guerneville, forestville, occidental, graton, geyserville,
        oakdale, waterford, hughson, denair, keyes, el dorado hills,
        cameron park, shingle springs, diamond springs, granite bay,
        loomis, penryn, rancho murieta, wilton, deer park, felton,
        ben lomond, boulder creek, la selva beach, castroville, prunedale,
        del rey oaks, sand city, atwater, winton"""),
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

# Added 2026-09-09, and kept in its own block because _csv splits on commas
# and nothing else: a comment written inside the string above becomes a
# city named "# added 2026-09-09 ..." and swallows the first real entry
# after it, which is exactly what happened on the first attempt at this.
#
# Every one of these came out of the review bucket on the first scan after
# Tenet, Providence and Adventist were added — three statewide employers
# whose California postings are mostly southern. 119 of the 121 rows under
# "Location needs checking" were these fifteen towns, which is enough noise
# to make a review list nobody reads. All are 200+ miles out; none is a
# close call, and a close call belongs in review rather than here.
OUT_CITIES |= set(_csv("""
    simi valley, tehachapi, reedley, joshua tree, orange, indio, fullerton,
    templeton, mission hills, apple valley, montebello, mission viejo,
    san pedro, tarzana, brea
"""))

# Added 2026-09-09 with AHMC, whose hospitals are mostly in the San
# Gabriel Valley. Two of these are not merely new towns: "Monterey Park"
# and "Marina del Rey" *contain* a city this table already calls in range,
# and whole-phrase matching is longest-first, so without them a Monterey
# Park posting matched "monterey" and was filed 90 minutes from Oakland
# instead of 350 miles away. Check for that shape whenever a name is
# added: the danger is a short in-range name sitting inside a long
# out-of-range one.
OUT_CITIES |= set(_csv("""
    monterey park, marina del rey, san gabriel, south el monte, alhambra,
    west covina, baldwin park, rosemead, arcadia, pico rivera, norwalk,
    bellflower, lakewood, cerritos, la mirada, hacienda heights,
    rowland heights, diamond bar, chino, chino hills, upland, claremont,
    garden grove, santa ana, westminster, buena park, la habra, azusa,
    glendora, covina, monrovia, duarte, sun valley, panorama city
"""))

# Some employers record a street address and no city at all — John Muir
# posts its Tice Valley outpatient roles as bare "1914 Tice Valley Blvd",
# and the detail endpoint has no city either, so there is nothing to parse
# and the posting fell into review every scan. Curated landmarks close that
# gap. Digits are stripped before matching, so key on the street name only.
# Add an entry here when a real address keeps showing up under review.
LANDMARKS: dict[str, str] = {
    "tice valley blvd": "<30",      # John Muir outpatient, Walnut Creek
}

# Longest first so "sutter creek" is tested before "creek"-containing names
# and "south san francisco" before "san francisco".
_ORDERED: list[tuple[str, Geo, str | None]] = sorted(
    [(n, Geo.OUT, None) for n in OUT_CITIES]
    + [(n, Geo.IN, b) for n, b in IN_CITIES.items()]
    + [(n, Geo.IN, b) for n, b in LANDMARKS.items()],
    key=lambda t: -len(t[0]))


def haversine_mi(lat, lon) -> float | None:
    if lat is None or lon is None:
        return None
    lat1, lon1 = map(math.radians, ORIGIN)
    lat2, lon2 = math.radians(lat), math.radians(lon)
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 3958.8 * 2 * math.asin(math.sqrt(a))


# A posting in another state is out of range and does not need a city
# lookup to prove it. This exists because 30 of the 33 rows in one scan's
# review bucket were the same CommonSpirit "National Resident RN" posting
# in Lufkin and Livingston, Texas — towns no California gazetteer will
# ever list, arriving without coordinates, and crowding out the three
# rows that were genuinely worth a look. The review bucket is only useful
# if it is short enough to read.
#
# Only the last comma-separated segment is tested, and only against a
# whole state name or code. Testing the whole string would put "Nevada
# City, CA" and "Kansas City" out of range on a substring.
_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "colorado", "connecticut",
    "delaware", "florida", "georgia", "hawaii", "idaho", "illinois",
    "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire",
    "new jersey", "new mexico", "new york", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas",
    "utah", "vermont", "virginia", "washington", "west virginia",
    "wisconsin", "wyoming", "district of columbia",
    "al", "ak", "az", "ar", "co", "ct", "de", "fl", "ga", "hi", "id", "il",
    "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo",
    "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or",
    "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi",
    "wy", "dc",
}


def _other_state(location: str) -> bool:
    """True when the location's last segment names a state that isn't CA."""
    # A multi-site posting is labelled "City, ST (+N more)" by the adapters
    # that resolve one, and that suffix would otherwise hide the state.
    loc = re.sub(r"\s*\(\+\d+\s+more\)\s*$", "", location or "")
    parts = [p.strip() for p in loc.split(",") if p.strip()]
    if len(parts) < 2:
        return False
    tail = re.sub(r"[^a-z ]", " ", parts[-1].lower())
    tail = re.sub(r"\s+", " ", tail).strip()
    return tail in _STATES


def _normalize(location: str) -> str:
    s = (location or "").lower()
    s = re.sub(r"[^a-z\s]", " ", s)
    s = re.sub(r"\b(california|ca|usa|us|united states)\b", " ", s)
    return " " + re.sub(r"\s+", " ", s).strip() + " "


def classify(location: str, lat=None, lon=None):
    """Returns (verdict, drive_time_bucket, straight_line_miles)."""
    miles = haversine_mi(lat, lon)
    if _other_state(location):
        return Geo.OUT, None, miles
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
