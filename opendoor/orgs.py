"""Find GP practices by postcode prefix (NHS ORD API, keyless) and geocode them (postcodes.io, keyless).

Checked against the live API on 2026-09-19:
- PostCode is a PREFIX match on the whole postcode, so "E1" also returns E10 to E18 and E1W. We filter in code.
- PostCode needs at least 2 characters AND a digit ("E" and "SE" are both rejected with 406), so an area like
  "SE" is queried as SE1 .. SE9, which between them cover every SE district.
- Limit must be 1 to 1000. Offset pages. The list response already carries PostCode, no per-organisation call needed.
CLI: uv run python -m opendoor.orgs E13 E6 E7
"""
import json, os, re, sys
from concurrent.futures import ThreadPoolExecutor
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


def find_practices(postcode_prefixes: list[str], limit: int, region: str | None = None) -> list[dict]:
    """Org = {code, name, postcode, lat, lon, region}. Deduped by code, at most `limit`.
    region="London" drops practices postcodes.io places outside London (DA, EN, KT, RM, TW ... reach into the home counties)."""
    seen: dict[str, dict] = {}
    with httpx.Client(headers=UA, timeout=30) as client:
        for p in filter(None, map(clean_prefix, postcode_prefixes)):
            for o in _prefix_orgs(client, p):
                if region is None or o.get("region") == region:
                    seen.setdefault(o["code"], o)
    return list(seen.values())[:max(0, limit)]


if __name__ == "__main__":
    found = find_practices(sys.argv[1:] or ["E13"], 5000)
    for o in found[:10]:
        print(o)
    print(len(found), "practices |", sum(1 for o in found if o["lat"] is None), "without coordinates")
