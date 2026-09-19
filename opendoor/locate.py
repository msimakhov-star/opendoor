"""Turn a place name or street address into a postcode, so 'GPs near me' does not need a postcode.

OpenStreetMap Nominatim (keyless, one request per second, needs a User-Agent) finds the coordinates; postcodes.io (keyless)
turns them into the nearest postcode and says which country it is in. The search text goes to those two services and is
kept only in a local cache file on the machine running the app. England only: the NHS guidance Open Door checks is for England.
"""
import hashlib, json, threading, time
from pathlib import Path

import httpx

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache" / "locate"
UA = "OpenDoorResearch/0.1 (non-commercial hackathon research; https://github.com/msimakhov-star/opendoor)"
_lock, _last = threading.Lock(), [0.0]


class LocateError(Exception):
    """A message that is safe and useful to show to the person who typed the search."""


def _polite_get(client: httpx.Client, url: str, params: dict) -> httpx.Response:
    with _lock:  # Nominatim allows one request per second
        wait = 1.1 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
    return client.get(url, params=params, headers={"User-Agent": UA}, timeout=15)


def locate(q: str, client: httpx.Client | None = None) -> dict:
    """{place, postcode, lat, lon, country}. Raises LocateError with a plain message."""
    q = " ".join((q or "").split())[:200]
    if len(q) < 3:
        raise LocateError("Type a postcode, a town or a street address.")
    path = CACHE / (hashlib.sha256(q.lower().encode()).hexdigest() + ".json")
    if path.exists():
        found = json.loads(path.read_text())
    else:
        own = client is None
        client = client or httpx.Client()
        try:
            r = _polite_get(client, "https://nominatim.openstreetmap.org/search", {"q": q, "format": "json", "countrycodes": "gb", "limit": 1})
            hits = r.json() if r.status_code == 200 else []
            if not hits:
                raise LocateError("Could not find that place. Try a postcode, a town or a fuller street address.")
            lat, lon, name = float(hits[0]["lat"]), float(hits[0]["lon"]), hits[0].get("display_name", q)
            r = client.get("https://api.postcodes.io/postcodes", params={"lon": lon, "lat": lat, "limit": 1}, timeout=15)
            res = (r.json().get("result") or []) if r.status_code == 200 else []
            if not res:
                raise LocateError("Found the place but no postcode near it. Try a postcode.")
            found = {"place": name, "postcode": res[0]["postcode"], "lat": lat, "lon": lon, "country": res[0].get("country", "")}
        except httpx.HTTPError:
            raise LocateError("The place search is not answering. Try a postcode instead.")
        finally:
            if own:
                client.close()
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(found))
    if found["country"] != "England":
        raise LocateError("That place is in %s. The NHS guidance Open Door checks is for England." % (found["country"] or "an unknown country"))
    return {**found, "matched": ", ".join(found["place"].split(", ")[:3]), "place": q}  # place = what the person typed; matched = what the map service found


if __name__ == "__main__":
    import sys
    print(locate(" ".join(sys.argv[1:]) or "Stratford, London"))
