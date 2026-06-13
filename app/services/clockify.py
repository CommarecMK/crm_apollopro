"""
services/clockify.py — read-only napojení na Clockify.

Párování zakázek: spolehlivý klíč je ZKRATKA (IČO_pořadí, např. 47678488_2),
kterou má uživatel uloženou v poli "Note" u Clockify klienta.
Hodiny se berou ze summary reportu seskupeného podle CLIENT (případně PROJECT).
"""
import requests
from ..extensions import CLOCKIFY_API_KEY, CLOCKIFY_WORKSPACE_ID

API = "https://api.clockify.me/api/v1"
REPORTS = "https://reports.api.clockify.me/v1"
TIMEOUT = 25


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


def _seznam(ws, co):
    """Načte všechny klienty/projekty (vč. pole note). co = 'clients' | 'projects'."""
    out, page = [], 1
    while True:
        r = requests.get(f"{API}/workspaces/{ws}/{co}",
                         headers=_headers(), params={"page": page, "page-size": 200},
                         timeout=TIMEOUT)
        r.raise_for_status()
        davka = r.json()
        if not davka:
            break
        out.extend(davka)
        if len(davka) < 200:
            break
        page += 1
    return out


def _hodiny_summary(ws, group, datum_od, datum_do, billable=None):
    """Vrátí list (id, name, hodiny) ze summary reportu pro dané seskupení.
    billable=True → jen fakturovatelné, False → jen nefakturovatelné, None → vše."""
    body = {"dateRangeStart": datum_od, "dateRangeEnd": datum_do,
            "summaryFilter": {"groups": [group]}}
    if billable is not None:
        body["billable"] = billable
    r = requests.post(f"{REPORTS}/workspaces/{ws}/reports/summary",
                      headers=_headers(), json=body, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for g in r.json().get("groupOne", []):
        out.append((g.get("_id"), g.get("name", ""), round((g.get("duration") or 0) / 3600.0, 1)))
    return out


def hodiny_dle_zkratky(datum_od, datum_do):
    """
    Hlavní funkce: vrátí {zkratka: {"celkem": x, "bill": y, "nonbill": z}}.
    Spáruje hodiny (po klientovi) se zkratkou z pole Note u Clockify klienta.
    Při chybě/bez klíče vrací prázdný dict.
    """
    if not je_nakonfigurovano():
        return {}
    try:
        ws = _workspace_id()
        klienti = _seznam(ws, "clients")
        # id klienta -> zkratka (z note); a název -> zkratka (záloha)
        id2zkr, name2zkr = {}, {}
        for k in klienti:
            zkr = (k.get("note") or "").strip()
            if zkr:
                id2zkr[k.get("id")] = zkr
                name2zkr[_norm(k.get("name"))] = zkr

        def _zkr(cid, cname):
            return id2zkr.get(cid) or name2zkr.get(_norm(cname))

        vysledek = {}
        # Celkové hodiny po klientech
        for cid, cname, hod in _hodiny_summary(ws, "CLIENT", datum_od, datum_do):
            zkr = _zkr(cid, cname)
            if zkr:
                z = vysledek.setdefault(zkr, {"celkem": 0, "bill": 0, "nonbill": 0})
                z["celkem"] += hod
        # Jen fakturovatelné
        for cid, cname, hod in _hodiny_summary(ws, "CLIENT", datum_od, datum_do, billable=True):
            zkr = _zkr(cid, cname)
            if zkr and zkr in vysledek:
                vysledek[zkr]["bill"] += hod
        # Nefakturovatelné = celkem - billable
        for zkr, v in vysledek.items():
            v["nonbill"] = round(max(v["celkem"] - v["bill"], 0), 1)
        return vysledek
    except Exception as e:
        print(f"[clockify] chyba: {e}")
        return {}


def obohat_zakazky(zakazky, datum_od, datum_do):
    """Doplní každé zakázce atributy .hodiny / .hodiny_bill / .hodiny_nonbill."""
    mapa = hodiny_dle_zkratky(datum_od, datum_do)
    for z in zakazky:
        v = mapa.get(z.zkratka) or {}
        z.hodiny = v.get("celkem", 0.0)
        z.hodiny_bill = v.get("bill", 0.0)
        z.hodiny_nonbill = v.get("nonbill", 0.0)
    return zakazky


def mesicni_serie(zkratka, rok, do_mesic):
    """Vrátí list (mesic, hodiny_celkem, hodiny_bill) pro leden..do_mesic daného roku.
    Páruje klienta podle zkratky v poli Note."""
    if not je_nakonfigurovano() or not zkratka:
        return []
    try:
        import calendar as _cal
        ws = _workspace_id()
        ids = {k.get("id") for k in _seznam(ws, "clients")
               if (k.get("note") or "").strip() == zkratka}
        out = []
        for m in range(1, do_mesic + 1):
            posl = _cal.monthrange(rok, m)[1]
            od = f"{rok}-{m:02d}-01T00:00:00Z"
            do = f"{rok}-{m:02d}-{posl:02d}T23:59:59Z"
            tot = sum(h for cid, _, h in _hodiny_summary(ws, "CLIENT", od, do) if cid in ids)
            bil = sum(h for cid, _, h in _hodiny_summary(ws, "CLIENT", od, do, billable=True) if cid in ids)
            out.append((m, round(tot, 1), round(bil, 1)))
        return out
    except Exception as e:
        print(f"[clockify] mesicni_serie: {e}")
        return []


def diagnostika(datum_od, datum_do):
    """Vrátí přehled toho, co Clockify vrací — pro doladění párování."""
    if not je_nakonfigurovano():
        return {"chyba": "CLOCKIFY_API_KEY není nastavený"}
    try:
        ws = _workspace_id()
        klienti = _seznam(ws, "clients")
        projekty = _seznam(ws, "projects")
        return {
            "workspace_id": ws,
            "pocet_klientu": len(klienti),
            "pocet_projektu": len(projekty),
            "ukazka_klientu": [
                {"name": k.get("name"), "note_zkratka": k.get("note")}
                for k in klienti[:8]
            ],
            "hodiny_po_klientech": _hodiny_summary(ws, "CLIENT", datum_od, datum_do)[:8],
            "hodiny_po_projektech": _hodiny_summary(ws, "PROJECT", datum_od, datum_do)[:8],
            "sparovano_zkratka_hodiny": dict(list(hodiny_dle_zkratky(datum_od, datum_do).items())[:8]),
        }
    except Exception as e:
        return {"chyba": str(e)}
