"""
services/osm.py — vzdálenosti mezi místy přes OpenStreetMap (zdarma, bez klíče).
Geokódování: Nominatim. Trasa: OSRM (silniční km). Výsledky se cachují (i do DB Nastaveni).
"""
import time
import json
import requests

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OSRM = "https://router.project-osrm.org/route/v1/driving"
UA = "ApolloPro/1.0 (martin.komarek@commarec.cz)"
TIMEOUT = 20
_GEO = {}   # paměťová cache geokódování
_DIST = {}  # paměťová cache vzdáleností


def geokoduj(misto):
    """Vrátí (lat, lon) pro adresu/místo, nebo None. S cache."""
    if not misto:
        return None
    klic = misto.strip().lower()
    if klic in _GEO:
        return _GEO[klic]
    try:
        r = requests.get(NOMINATIM, headers={"User-Agent": UA},
                         params={"q": misto + ", Česko", "format": "json", "limit": 1}, timeout=TIMEOUT)
        time.sleep(1)  # Nominatim policy: max 1 req/s
        if r.status_code == 200 and r.json():
            d = r.json()[0]
            vysl = (float(d["lat"]), float(d["lon"]))
            _GEO[klic] = vysl
            return vysl
    except Exception as e:
        print(f"[osm] geokoduj {misto}: {e}")
    _GEO[klic] = None
    return None


def vzdalenost_km(misto_a, misto_b):
    """Silniční vzdálenost mezi dvěma místy v km (zaokrouhleno), nebo None."""
    if not misto_a or not misto_b:
        return None
    klic = f"{misto_a.strip().lower()}|{misto_b.strip().lower()}"
    if klic in _DIST:
        return _DIST[klic]
    a, b = geokoduj(misto_a), geokoduj(misto_b)
    if not a or not b:
        _DIST[klic] = None
        return None
    try:
        url = f"{OSRM}/{a[1]},{a[0]};{b[1]},{b[0]}"
        r = requests.get(url, params={"overview": "false"}, timeout=TIMEOUT)
        if r.status_code == 200:
            routes = r.json().get("routes") or []
            if routes:
                km = round(routes[0]["distance"] / 1000.0, 1)
                _DIST[klic] = km
                return km
    except Exception as e:
        print(f"[osm] vzdalenost: {e}")
    _DIST[klic] = None
    return None


def vzdalenosti_mezi(mista):
    """Pro seznam míst vrátí vzdálenosti mezi sousedními body: [{a,b,km}]."""
    out = []
    for i in range(len(mista) - 1):
        out.append({"a": mista[i], "b": mista[i + 1], "km": vzdalenost_km(mista[i], mista[i + 1])})
    return out
