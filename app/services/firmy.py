"""
services/firmy.py — obohacení firmy podle IČO.

Primárně MERK (api.merk.cz, vrací i kontakty/obrat/zaměstnance),
záloha ARES (ares.gov.cz, oficiální a zdarma). Nic se nezapisuje do MERK/ARES.
"""
import os
from datetime import date
import requests

MERK_BASE = "https://api.merk.cz"
ARES_BASE = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty"
MERK_API_KEY = os.environ.get("MERK_API_KEY", "")
TIMEOUT = 20


def _merk_get(path, params=None):
    return requests.get(f"{MERK_BASE}{path}", params=params, timeout=TIMEOUT,
                        headers={"Accept": "application/json",
                                 "Authorization": f"Token {MERK_API_KEY}"})


def _z_merk(ico):
    """Vrátí dict s daty firmy + seznam kontaktů z MERK, nebo None."""
    if not MERK_API_KEY:
        return None
    try:
        r = _merk_get("/company/", {"regno": ico, "country_code": "cz"})
        if r.status_code != 200:
            return None
        item = r.json()
        if isinstance(item, list):
            item = item[0] if item else {}
        addr = item.get("address") or {}
        adresa = ", ".join(p for p in [str(addr.get("street") or ""),
                                       str(addr.get("municipality") or ""),
                                       str(addr.get("postal_code") or "")] if p)
        kontakty = []
        for p in (item.get("body") or {}).get("persons", [])[:10]:
            jmeno = f"{p.get('first_name','') or ''} {p.get('last_name','') or ''}".strip()
            if jmeno:
                kontakty.append({"jmeno": jmeno, "pozice": p.get("function") or "",
                                 "email": "", "telefon": ""})
        emails = item.get("emails") or []
        phones = (item.get("phones") or []) + (item.get("mobiles") or [])
        if kontakty and emails:
            kontakty[0]["email"] = emails[0].get("email", "")
        if kontakty and phones:
            kontakty[0]["telefon"] = phones[0].get("number", "")
        return {
            "zdroj": "MERK",
            "nazev": str(item.get("name") or ""),
            "dic": str(item.get("vatno") or ""),
            "adresa": adresa,
            "web": (item.get("webs") or [{}])[0].get("url", "") if item.get("webs") else "",
            "obor": (item.get("industry") or {}).get("text") or "",
            "zamestnanci": (item.get("magnitude") or {}).get("text") or "",
            "obrat": (item.get("turnover") or {}).get("text") or "",
            "kontakty": kontakty,
        }
    except Exception as e:
        print(f"[merk] chyba: {e}")
        return None


def _z_ares(ico):
    """Záloha: oficiální data z ARES (bez kontaktů/financí)."""
    try:
        r = requests.get(f"{ARES_BASE}/{ico}", timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        d = r.json()
        sidlo = d.get("sidlo") or {}
        return {
            "zdroj": "ARES",
            "nazev": d.get("obchodniJmeno") or "",
            "dic": d.get("dic") or "",
            "adresa": sidlo.get("textovaAdresa") or "",
            "web": "", "obor": "", "zamestnanci": "", "obrat": "",
            "kontakty": [],
        }
    except Exception as e:
        print(f"[ares] chyba: {e}")
        return None


def nacti_firmu(ico):
    """Vrátí data firmy z MERK, jinak z ARES, jinak None."""
    if not ico:
        return None
    return _z_merk(ico) or _z_ares(ico)


def obohat_firmu(firma):
    """Natáhne data do objektu Firma + doplní kontakty (z MERK). Vrací (ok, zdroj)."""
    from ..extensions import db
    from ..models import Kontakt
    data = nacti_firmu(firma.ico)
    if not data:
        return (False, None)
    firma.dic = data.get("dic") or firma.dic
    firma.adresa = data.get("adresa") or firma.adresa
    firma.web = data.get("web") or firma.web
    firma.obor = data.get("obor") or firma.obor
    firma.zamestnanci = data.get("zamestnanci") or firma.zamestnanci
    firma.obrat = data.get("obrat") or firma.obrat
    firma.merk_nacteno = f"{date.today().isoformat()} ({data['zdroj']})"
    # Kontakty z MERK – přidáme jen ty, které ještě nemáme (dle jména)
    existujici = {(k.jmeno or "").lower() for k in firma.kontakty}
    for k in data.get("kontakty", []):
        if (k["jmeno"] or "").lower() not in existujici:
            db.session.add(Kontakt(firma_id=firma.id, jmeno=k["jmeno"], pozice=k.get("pozice"),
                                   email=k.get("email"), telefon=k.get("telefon"), zdroj="merk"))
    db.session.commit()
    return (True, data["zdroj"])
