"""
services/onedrive.py — čtení dokumentů klientů z OneDrive/SharePointu přes Microsoft Graph.

Přístup: app-only (client credentials) — aplikace má vlastní oprávnění (Sites.Read.All / Files.Read.All).
U klienta se uloží ODKAZ na jeho složku (Firma.onedrive_odkaz); z odkazu se přes /shares
zjistí driveItem (drive_id + item_id) a z něj se vypisují/čtou soubory.
"""
import time
import base64
import requests

from ..extensions import GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET

GRAPH = "https://graph.microsoft.com/v1.0"
TIMEOUT = 30
_TOKEN = {"val": None, "exp": 0}
_CACHE = {}
CACHE_TTL = 300


def je_nakonfigurovano():
    return bool(GRAPH_TENANT_ID and GRAPH_CLIENT_ID and GRAPH_CLIENT_SECRET)


def _token():
    if _TOKEN["val"] and time.time() < _TOKEN["exp"] - 60:
        return _TOKEN["val"]
    url = f"https://login.microsoftonline.com/{GRAPH_TENANT_ID}/oauth2/v2.0/token"
    data = {"client_id": GRAPH_CLIENT_ID, "client_secret": GRAPH_CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"}
    r = requests.post(url, data=data, timeout=TIMEOUT)
    r.raise_for_status()
    j = r.json()
    _TOKEN["val"] = j["access_token"]
    _TOKEN["exp"] = time.time() + int(j.get("expires_in", 3600))
    return _TOKEN["val"]


def _hlavicky():
    return {"Authorization": f"Bearer {_token()}"}


def _share_id(odkaz):
    """Zakóduje sdílecí URL do tvaru, který bere Graph endpoint /shares/{id}."""
    b = base64.urlsafe_b64encode(odkaz.strip().encode()).decode().rstrip("=")
    return "u!" + b


def resolve_slozka(odkaz):
    """Z odkazu na složku vrátí (drive_id, item_id) nebo None."""
    if not odkaz:
        return None
    ck = f"resolve:{odkaz}"
    c = _CACHE.get(ck)
    if c and (time.time() - c[0]) < CACHE_TTL:
        return c[1]
    try:
        r = requests.get(f"{GRAPH}/shares/{_share_id(odkaz)}/driveItem",
                         headers=_hlavicky(), params={"$select": "id,name,parentReference"},
                         timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        d = r.json()
        drive_id = (d.get("parentReference") or {}).get("driveId")
        item_id = d.get("id")
        vysledek = (drive_id, item_id) if drive_id and item_id else None
        _CACHE[ck] = (time.time(), vysledek)
        return vysledek
    except Exception as e:
        print(f"[onedrive] resolve: {e}")
        return None


def _polozka(it):
    je_slozka = "folder" in it
    return {"id": it.get("id"), "nazev": it.get("name", ""), "je_slozka": je_slozka,
            "velikost": it.get("size") or 0,
            "pocet": (it.get("folder") or {}).get("childCount") if je_slozka else None,
            "zmeneno": (it.get("lastModifiedDateTime") or "")[:10],
            "web_url": it.get("webUrl", ""),
            "download_url": it.get("@microsoft.graph.downloadUrl", "")}


def vypis(drive_id, item_id):
    """Vrátí seznam položek (složky první) ve složce."""
    if not je_nakonfigurovano() or not drive_id or not item_id:
        return []
    ck = f"vypis:{drive_id}:{item_id}"
    c = _CACHE.get(ck)
    if c and (time.time() - c[0]) < CACHE_TTL:
        return c[1]
    try:
        out = []
        url = (f"{GRAPH}/drives/{drive_id}/items/{item_id}/children"
               "?$select=id,name,folder,file,size,webUrl,lastModifiedDateTime&$top=200")
        while url:
            r = requests.get(url, headers=_hlavicky(), timeout=TIMEOUT)
            if r.status_code != 200:
                break
            j = r.json()
            out.extend(_polozka(it) for it in j.get("value", []))
            url = j.get("@odata.nextLink")
        out.sort(key=lambda x: (not x["je_slozka"], x["nazev"].lower()))
        _CACHE[ck] = (time.time(), out)
        return out
    except Exception as e:
        print(f"[onedrive] vypis: {e}")
        return []


def vypis_slozky_klienta(odkaz, item_id=None):
    """Pro UI: z odkazu klienta vypíše obsah kořenové složky, nebo dané podsložky (item_id)."""
    r = resolve_slozka(odkaz)
    if not r:
        return None  # nelze najít složku
    drive_id, root_id = r
    return {"drive_id": drive_id, "polozky": vypis(drive_id, item_id or root_id)}


def vsechny_soubory(odkaz, max_souboru=800, max_hloubka=6):
    """Rekurzivně projde složku klienta a vrátí seznam souborů [{id,nazev,cesta,velikost,web_url}].
    drive_id je u všech stejné (z resolve)."""
    r = resolve_slozka(odkaz)
    if not r:
        return None
    drive_id, root_id = r
    out = []

    def _walk(item_id, cesta, hloubka):
        if hloubka > max_hloubka or len(out) >= max_souboru:
            return
        for p in vypis(drive_id, item_id):
            if len(out) >= max_souboru:
                return
            if p["je_slozka"]:
                _walk(p["id"], f"{cesta}/{p['nazev']}", hloubka + 1)
            else:
                out.append({"id": p["id"], "nazev": p["nazev"], "cesta": cesta.strip("/"),
                            "velikost": p["velikost"], "web_url": p["web_url"]})
    _walk(root_id, "", 0)
    return {"drive_id": drive_id, "soubory": out}


def stahni(drive_id, item_id):
    """Stáhne obsah souboru (bytes) — pro Fázi 2 (extrakce textu pro AI)."""
    if not je_nakonfigurovano():
        return None
    try:
        r = requests.get(f"{GRAPH}/drives/{drive_id}/items/{item_id}/content",
                         headers=_hlavicky(), timeout=TIMEOUT)
        return r.content if r.status_code == 200 else None
    except Exception as e:
        print(f"[onedrive] stahni: {e}")
        return None


def diagnostika(odkaz=None):
    out = {"nakonfigurovano": je_nakonfigurovano()}
    if not je_nakonfigurovano():
        return out
    try:
        out["token_ok"] = bool(_token())
    except Exception as e:
        out["token_chyba"] = str(e)
        return out
    if odkaz:
        r = resolve_slozka(odkaz)
        out["resolve"] = r
        if r:
            out["pocet_polozek"] = len(vypis(r[0], r[1]))
    return out
