"""
services/clockify.py — read-only napojení na Clockify.

Nic do Clockify nezapisuje. Klíč se bere z env proměnné CLOCKIFY_API_KEY.
Hodiny se párují na zakázky podle názvu projektu ("Klient - Typ služby").
"""
import requests
from ..extensions import CLOCKIFY_API_KEY, CLOCKIFY_WORKSPACE_ID

API = "https://api.clockify.me/api/v1"
REPORTS = "https://reports.api.clockify.me/v1"
TIMEOUT = 20


def je_nakonfigurovano():
    return bool(CLOCKIFY_API_KEY)


def _headers():
    return {"X-Api-Key": CLOCKIFY_API_KEY, "Content-Type": "application/json"}


def _workspace_id():
    if CLOCKIFY_WORKSPACE_ID:
        return CLOCKIFY_WORKSPACE_ID
    r = requests.get(f"{API}/user", headers=_headers(), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("activeWorkspace")


def _norm(s):
    return (s or "").strip().lower()


def hodiny_dle_projektu(datum_od, datum_do):
    """
    Vrátí dict {normalizovaný_název_projektu: hodiny} za zadané období.
    Při chybě nebo bez klíče vrací prázdný dict — kokpit funguje dál.
    Datumy ve formátu 'YYYY-MM-DDTHH:MM:SSZ'.
    """
    if not je_nakonfigurovano():
        return {}
    try:
        ws = _workspace_id()
        body = {
            "dateRangeStart": datum_od,
            "dateRangeEnd": datum_do,
            "summaryFilter": {"groups": ["PROJECT"]},
        }
        r = requests.post(
            f"{REPORTS}/workspaces/{ws}/reports/summary",
            headers=_headers(), json=body, timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        vysledek = {}
        for g in data.get("groupOne", []):
            nazev = g.get("name", "")
            sekundy = g.get("duration", 0) or 0
            vysledek[_norm(nazev)] = round(sekundy / 3600.0, 1)
        return vysledek
    except Exception as e:
        # Tiše degraduj — kokpit ukáže zakázky bez hodin a důvod
        print(f"[clockify] chyba: {e}")
        return {}


def obohat_zakazky(zakazky, datum_od, datum_do):
    """Doplní každé zakázce atribut .hodiny (float) z Clockify dle názvu."""
    mapa = hodiny_dle_projektu(datum_od, datum_do)
    for z in zakazky:
        z.hodiny = mapa.get(_norm(z.nazev), 0.0)
    return zakazky
