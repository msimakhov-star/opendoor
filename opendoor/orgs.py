"""Find GP practices by postcode prefix (NHS ORD API, keyless) and geocode them (postcodes.io, keyless).

Checked against the live API on 2026-09-19:
- PostCode is a PREFIX match on the whole postcode, so "E1" also returns E10 to E18 and E1W. We filter in code.
- PostCode needs at least 2 characters AND a digit ("E" and "SE" are both rejected with 406), so an area like
  "SE" is queried as SE1 .. SE9, which between them cover every SE district.
- Limit must be 1 to 1000. Offset pages. The list response already carries PostCode, no per-organisation call needed.
- postcodes.io (checked the same day): /postcodes/<pc> and /outcodes/<oc> both give latitude, longitude, outcode.
  /outcodes/<oc>/nearest takes limit (default 10, capped at 100) and radius in metres (capped at 25000), sorted by
  distance from that outcode's centroid, with no distance field, so we compute our own from the searched point.
CLI: uv run python -m opendoor.orgs E13 E6 E7      or      uv run python -m opendoor.orgs "E13 8AA"   (nearest 40)
"""
import json, os, re, sys
from concurrent.futures import ThreadPoolExecutor
from itertools import zip_longest
from pathlib import Path

import certifi, httpx

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "orgs"
ORD = "https://directory.spineservices.nhs.uk/ORD/2-0-0/organisations"
UA = {"User-Agent": "OpenDoorResearch/0.1 (non-commercial hackathon research on GP registration wording; read only)"}
LONDON_PREFIXES = ["E", "EC", "N", "NW", "SE", "SW", "W", "WC",
                   "BR", "CR", "DA", "EN", "HA", "IG", "KT", "RM", "SM", "TW", "UB"]


def clean_prefix(p: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", p.upper())


def matches(prefix: str, postcode: str) -> bool:
    """'E1' matches E1 and E1W, not E13. 'E13' matches only E13. An area like 'E' matches E1 to E20, not EC1 or EN1."""
    outward = postcode.split()[0] if " " in postcode else postcode[:-3]
    return bool(re.match(prefix + (r"\d" if prefix.isalpha() else r"(?!\d)"), outward))


def _queries(prefix: str) -> list[str]:
    return [prefix + str(d) for d in range(1, 10)] if prefix.isalpha() else [prefix]


def _ord(client: httpx.Client, q: str) -> list[dict]:
    out, offset = [], 0
    while True:
        params = {"PostCode": q, "PrimaryRoleId": "RO177", "NonPrimaryRoleId": "RO76", "Status": "Active", "Limit": 1000}
        if offset:
            params["Offset"] = offset
        r = client.get(ORD, params=params)
        r.raise_for_status()
        page = r.json().get("Organisations", [])
        out += page
        if len(page) < 1000:
            return out
        offset += 1000


def _geocode(client: httpx.Client, postcodes: list[str]) -> dict:
    geo = {}
    for i in range(0, len(postcodes), 100):  # postcodes.io bulk limit is 100 per request
        r = client.post("https://api.postcodes.io/postcodes", json={"postcodes": postcodes[i:i + 100]})
        r.raise_for_status()
        for x in r.json()["result"]:
            if x["result"]:
                geo[x["query"]] = x["result"]
    return geo


def _prefix_orgs(client: httpx.Client, prefix: str) -> list[dict]:
    """All practices for one prefix, geocoded, cached on disk so repeat runs (and the demo) do not hit the APIs again."""
    path = CACHE / f"{prefix}.json"
    if path.exists():
        return json.loads(path.read_text())
    with ThreadPoolExecutor(4) as ex:
        raw = [o for page in ex.map(lambda q: _ord(client, q), _queries(prefix)) for o in page]
    raw = list({o["OrgId"]: o for o in raw if matches(prefix, o["PostCode"])}.values())
    geo = _geocode(client, sorted({o["PostCode"] for o in raw}))
    orgs = [{"code": o["OrgId"], "name": o["Name"], "postcode": o["PostCode"],
             "lat": geo.get(o["PostCode"], {}).get("latitude"), "lon": geo.get(o["PostCode"], {}).get("longitude"),
             "region": geo.get(o["PostCode"], {}).get("region")} for o in sorted(raw, key=lambda o: o["OrgId"])]
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(orgs, indent=1))
    return orgs


PC = "https://api.postcodes.io"
PLACE = re.compile(r"([A-Z]{1,2}\d[A-Z\d]?)(\d[A-Z]{2})?")  # outcode, optional inward code
BATCH = 8  # districts read in parallel per round
MARGIN_KM = 2.0  # ponytail: a district's practices can sit ~2 km from its centroid; widen if far edges look missed


def single_place(s: str) -> str | None:
    """'e13 8aa' -> 'E13 8AA', 'E13' -> 'E13'. A list or an area ('E13, E6', 'SE') -> None, which means a prefix sweep."""
    m = PLACE.fullmatch(re.sub(r"\s+", "", (s or "").upper()))
    return m and " ".join(filter(None, m.groups()))


def km(a: dict, b: dict) -> float:
    """Haversine distance between two {lat, lon} dicts."""
    from math import asin, cos, radians, sin, sqrt
    p1, p2, dl = radians(a["lat"]), radians(b["lat"]), radians(b["lon"] - a["lon"])
    return 2 * 6371.0 * asin(sqrt(sin((p2 - p1) / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2))


def _point(client: httpx.Client, place: str) -> dict:
    """{lat, lon, outcode} of a full postcode (/postcodes/<pc>) or an outcode centroid (/outcodes/<oc>)."""
    r = client.get(f"{PC}/{'postcodes' if ' ' in place else 'outcodes'}/{place.replace(' ', '')}")
    if r.status_code == 404:
        raise ValueError(f"postcodes.io does not know {place}")
    r.raise_for_status()
    x = r.json()["result"]
    return {"lat": x["latitude"], "lon": x["longitude"], "outcode": x["outcode"]}


def _nearest_outcodes(client: httpx.Client, outcode: str) -> list[dict]:
    """Up to 100 outcodes within 25 km of this outcode's centroid (postcodes.io caps limit at 100 and radius at 25000 m)."""
    r = client.get(f"{PC}/outcodes/{outcode}/nearest", params={"limit": 100, "radius": 25000})
    r.raise_for_status()
    return [{"outcode": o["outcode"], "lat": o["latitude"], "lon": o["longitude"]} for o in r.json()["result"] or [] if o.get("latitude") is not None]


def find_nearest(place: str, n: int) -> tuple[list[dict], dict]:
    """The n practices nearest to a full postcode or an outcode, closest first, each with distance_km. Returns (orgs, centre).
    Districts are read nearest first, BATCH at a time, until n practices are found and the next district's centroid is
    further than the n-th practice plus MARGIN_KM."""
    with httpx.Client(headers=UA, timeout=30) as client:
        c = _point(client, place)
        near = sorted(_nearest_outcodes(client, c["outcode"]), key=lambda o: km(c, o))
        found, i, k = {}, 0, max(n, 1)
        with ThreadPoolExecutor(BATCH) as ex:
            while i < len(near):
                d = sorted(o["distance_km"] for o in found.values())
                if len(d) >= k and km(c, near[i]) > d[k - 1] + MARGIN_KM:
                    break
                batch, i = near[i:i + BATCH], i + BATCH
                for orgs in ex.map(lambda o: _prefix_orgs(client, clean_prefix(o["outcode"])), batch):
                    for o in orgs:
                        if o.get("lat") is not None:  # no coordinates, no distance: cannot be ranked
                            found.setdefault(o["code"], {**o, "distance_km": round(km(c, o), 2)})
    c["districts_read"] = min(i, len(near))
    return sorted(found.values(), key=lambda o: o["distance_km"])[:max(0, n)], c


def find_practices(postcode_prefixes: list[str], limit: int, region: str | None = None) -> list[dict]:
    """Org = {code, name, postcode, lat, lon, region}. Deduped by code, at most `limit`.
    region="London" drops practices postcodes.io places outside London (DA, EN, KT, RM, TW ... reach into the home counties)."""
    seen: dict[str, dict] = {}
    with httpx.Client(headers=UA, timeout=30) as client:
        for p in filter(None, map(clean_prefix, postcode_prefixes)):
            for o in _prefix_orgs(client, p):
                if region is None or o.get("region") == region:
                    seen.setdefault(o["code"], o)
    # Round-robin over districts, so a limit below the total spreads over the whole area instead of filling up from E1.
    by_district: dict[str, list] = {}
    for o in seen.values():
        by_district.setdefault((o.get("postcode") or "").split(" ")[0], []).append(o)
    spread = [o for row in zip_longest(*by_district.values()) for o in row if o]
    return spread[:max(0, limit)]


if __name__ == "__main__":
    if len(sys.argv) == 2 and single_place(sys.argv[1]):
        found, c = find_nearest(single_place(sys.argv[1]), 40)
        print(c, "| outcodes:", sorted({o["postcode"].split()[0] for o in found}), "| furthest km:", found[-1]["distance_km"] if found else None)
    else:
        found = find_practices(sys.argv[1:] or ["E13"], 5000)
    for o in found[:10]:
        print(o)
    print(len(found), "practices |", sum(1 for o in found if o["lat"] is None), "without coordinates")
