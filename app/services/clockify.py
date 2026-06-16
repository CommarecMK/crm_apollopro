"""
services/clockify.py — read-only napojení na Clockify.

Párování zakázek: spolehlivý klíč je ZKRATKA (IČO_pořadí, např. 47678488_2),
kterou má uživatel uloženou v poli "Note" u Clockify klienta.
Hodiny se berou ze summary reportu seskupeného podle CLIENT (případně PROJECT).
"""
import time
from datetime import datetime, timezone, timedelta
import requests
from ..extensions import CLOCKIFY_API_KEY, CLOCKIFY_WORKSPACE_ID

API = "https://api.clockify.me/api/v1"
REPORTS = "https://reports.api.clockify.me/v1"
TIMEOUT = 25

# ── Krátkodobá cache (zrychluje načítání stránek) ────────────────
_CACHE = {}
CACHE_TTL = 300  # sekund (5 min)


def _cache_get(key):
    v = _CACHE.get(key)
    if v and (time.time() - v[0]) < CACHE_TTL:
        return v[1]
    return None


def _cache_set(key, val):
    _CACHE[key] = (time.time(), val)
    return val


def je_nakonfigurovano():
    return bool(CLOCKIFY_API_KEY)


def _headers():
    return {"X-Api-Key": CLOCKIFY_API_KEY, "Content-Type": "application/json"}


def _workspace_id():
    if CLOCKIFY_WORKSPACE_ID:
        return CLOCKIFY_WORKSPACE_ID
    c = _cache_get("ws")
    if c:
        return c
    r = requests.get(f"{API}/user", headers=_headers(), timeout=TIMEOUT)
    r.raise_for_status()
    return _cache_set("ws", r.json().get("activeWorkspace"))


def _norm(s):
    return (s or "").strip().lower()


def _seznam(ws, co):
    """Načte VŠECHNY klienty/projekty vč. archivních (vč. pole note). co = 'clients' | 'projects'.
    Clockify defaultně vrací jen aktivní — proto stáhneme aktivní i archivované zvlášť a spojíme."""
    ck = f"seznam:{ws}:{co}"
    c = _cache_get(ck)
    if c is not None:
        return c
    out, videno = [], set()
    for archived in ("false", "true"):
        page = 1
        while True:
            r = requests.get(f"{API}/workspaces/{ws}/{co}",
                             headers=_headers(),
                             params={"page": page, "page-size": 200, "archived": archived},
                             timeout=TIMEOUT)
            r.raise_for_status()
            davka = r.json()
            if not davka:
                break
            for item in davka:
                if item.get("id") not in videno:
                    videno.add(item.get("id"))
                    out.append(item)
            if len(davka) < 200:
                break
            page += 1
    return _cache_set(ck, out)


def _hodiny_summary(ws, group, datum_od, datum_do, billable=None):
    """Vrátí list (id, name, hodiny) ze summary reportu pro dané seskupení.
    billable=True → jen fakturovatelné, False → jen nefakturovatelné, None → vše."""
    ck = f"sum:{ws}:{group}:{datum_od}:{datum_do}:{billable}"
    c = _cache_get(ck)
    if c is not None:
        return c
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
    return _cache_set(ck, out)


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


def hodiny_dle_zkratky_obdobi(periods):
    """periods = list (od, do). Sečte hodiny přes všechna období → {zkr:{celkem,bill,nonbill}}."""
    if not je_nakonfigurovano() or not periods:
        return {}
    try:
        ws = _workspace_id()
        id2zkr, name2zkr = {}, {}
        for k in _seznam(ws, "clients"):
            zkr = (k.get("note") or "").strip()
            if zkr:
                id2zkr[k.get("id")] = zkr
                name2zkr[_norm(k.get("name"))] = zkr

        def _zkr(cid, cn):
            return id2zkr.get(cid) or name2zkr.get(_norm(cn))

        res = {}
        for od, do in periods:
            for cid, cn, hod in _hodiny_summary(ws, "CLIENT", od, do):
                zkr = _zkr(cid, cn)
                if zkr:
                    res.setdefault(zkr, {"celkem": 0, "bill": 0, "nonbill": 0})["celkem"] += hod
            for cid, cn, hod in _hodiny_summary(ws, "CLIENT", od, do, billable=True):
                zkr = _zkr(cid, cn)
                if zkr and zkr in res:
                    res[zkr]["bill"] += hod
        for v in res.values():
            v["nonbill"] = round(max(v["celkem"] - v["bill"], 0), 1)
            v["celkem"] = round(v["celkem"], 1)
            v["bill"] = round(v["bill"], 1)
        return res
    except Exception as e:
        print(f"[clockify] obdobi: {e}")
        return {}


def obohat_zakazky_obdobi(zakazky, periods):
    """Jako obohat_zakazky, ale přes seznam období (více měsíců)."""
    mapa = hodiny_dle_zkratky_obdobi(periods)
    for z in zakazky:
        v = mapa.get(z.zkratka) or {}
        z.hodiny = v.get("celkem", 0.0)
        z.hodiny_bill = v.get("bill", 0.0)
        z.hodiny_nonbill = v.get("nonbill", 0.0)
    return zakazky


def firma_prehled(zkratky, rok, do_mesic):
    """Pro kartu klienta: per-zakázku měsíční hodiny (total i billable).
    Vrací {zkr: {'tot':[...], 'bill':[...]}} pro měsíce 1..do_mesic + 'mesice'."""
    mesice = list(range(1, do_mesic + 1))
    prazdny = {"per": {z: {"tot": [0] * do_mesic, "bill": [0] * do_mesic} for z in zkratky}, "mesice": mesice}
    if not je_nakonfigurovano() or not zkratky:
        return prazdny
    try:
        import calendar as _cal
        ws = _workspace_id()
        id2zkr = {k.get("id"): (k.get("note") or "").strip()
                  for k in _seznam(ws, "clients") if (k.get("note") or "").strip() in set(zkratky)}
        per = {z: {"tot": [0] * do_mesic, "bill": [0] * do_mesic} for z in zkratky}
        for i, m in enumerate(mesice):
            posl = _cal.monthrange(rok, m)[1]
            od = f"{rok}-{m:02d}-01T00:00:00Z"
            do = f"{rok}-{m:02d}-{posl:02d}T23:59:59Z"
            for cid, _, h in _hodiny_summary(ws, "CLIENT", od, do):
                z = id2zkr.get(cid)
                if z:
                    per[z]["tot"][i] += h
            for cid, _, h in _hodiny_summary(ws, "CLIENT", od, do, billable=True):
                z = id2zkr.get(cid)
                if z:
                    per[z]["bill"][i] += h
        return {"per": per, "mesice": mesice}
    except Exception as e:
        print(f"[clockify] firma_prehled: {e}")
        return prazdny


def _hodiny_uzivatele_ids(ws, ids, datum_od, datum_do):
    """Hodiny po zaměstnancích pro dané client-id. [(jmeno, celkem, bill)]."""
    if not ids:
        return []
    ck = f"usr:{ws}:{','.join(sorted(ids))}:{datum_od}:{datum_do}"
    c = _cache_get(ck)
    if c is not None:
        return c

    def _po_uzivatelich(billable=None):
        body = {"dateRangeStart": datum_od, "dateRangeEnd": datum_do,
                "summaryFilter": {"groups": ["USER"]},
                "clients": {"ids": ids, "contains": "CONTAINS", "status": "ALL"}}
        if billable is not None:
            body["billable"] = billable
        r = requests.post(f"{REPORTS}/workspaces/{ws}/reports/summary",
                          headers=_headers(), json=body, timeout=TIMEOUT)
        r.raise_for_status()
        return {g.get("_id"): (g.get("name", ""), round((g.get("duration") or 0) / 3600.0, 1))
                for g in r.json().get("groupOne", [])}

    tot, bil = _po_uzivatelich(), _po_uzivatelich(True)
    out = [(nm, h, bil.get(uid, ("", 0))[1]) for uid, (nm, h) in tot.items()]
    return _cache_set(ck, sorted(out, key=lambda x: -x[1]))


def hodiny_dle_uzivatelu(zkratka, datum_od, datum_do):
    """Hodiny po zaměstnancích pro JEDNU zakázku (dle zkratky v Note)."""
    if not je_nakonfigurovano() or not zkratka:
        return []
    try:
        ws = _workspace_id()
        ids = [k.get("id") for k in _seznam(ws, "clients")
               if (k.get("note") or "").strip() == zkratka]
        return _hodiny_uzivatele_ids(ws, ids, datum_od, datum_do)
    except Exception as e:
        print(f"[clockify] uzivatele: {e}")
        return []


def uzivatele_vsech_klientu(datum_od, datum_do):
    """Jedním dotazem (CLIENT→USER) vrátí hodiny po zaměstnancích pro VŠECHNY klienty.
    Vrací {zkratka: [(jmeno, celkem, bill)]}. Šetří desítky dotazů."""
    if not je_nakonfigurovano():
        return {}
    try:
        ws = _workspace_id()
        id2zkr = {k.get("id"): (k.get("note") or "").strip()
                  for k in _seznam(ws, "clients") if (k.get("note") or "").strip()}

        def _vnoreno(billable=None):
            body = {"dateRangeStart": datum_od, "dateRangeEnd": datum_do,
                    "summaryFilter": {"groups": ["CLIENT", "USER"]}}
            if billable is not None:
                body["billable"] = billable
            r = requests.post(f"{REPORTS}/workspaces/{ws}/reports/summary",
                              headers=_headers(), json=body, timeout=TIMEOUT)
            r.raise_for_status()
            out = {}
            for g in r.json().get("groupOne", []):
                cid = g.get("_id")
                for ch in g.get("children", []):
                    out[(cid, ch.get("_id"))] = (ch.get("name", ""),
                                                 round((ch.get("duration") or 0) / 3600.0, 1))
            return out

        tot, bil = _vnoreno(), _vnoreno(True)
        per = {}
        for (cid, uid), (nm, h) in tot.items():
            zkr = id2zkr.get(cid)
            if not zkr:
                continue
            per.setdefault(zkr, []).append((nm, h, bil.get((cid, uid), ("", 0))[1]))
        for zkr in per:
            per[zkr] = sorted(per[zkr], key=lambda x: -x[1])
        return per
    except Exception as e:
        print(f"[clockify] uzivatele_vsech: {e}")
        return {}


def projekty_vsech_klientu(datum_od, datum_do):
    """Jedním dotazem (CLIENT→PROJECT) vrátí hodiny po projektech pro VŠECHNY klienty.
    Vrací {zkratka: [(projekt, celkem, bill)]}."""
    if not je_nakonfigurovano():
        return {}
    try:
        ws = _workspace_id()
        id2zkr = {k.get("id"): (k.get("note") or "").strip()
                  for k in _seznam(ws, "clients") if (k.get("note") or "").strip()}

        def _vnoreno(billable=None):
            body = {"dateRangeStart": datum_od, "dateRangeEnd": datum_do,
                    "summaryFilter": {"groups": ["CLIENT", "PROJECT"]}}
            if billable is not None:
                body["billable"] = billable
            r = requests.post(f"{REPORTS}/workspaces/{ws}/reports/summary",
                              headers=_headers(), json=body, timeout=TIMEOUT)
            r.raise_for_status()
            out = {}
            for g in r.json().get("groupOne", []):
                cid = g.get("_id")
                for ch in g.get("children", []):
                    out[(cid, ch.get("_id"))] = (ch.get("name", ""),
                                                 round((ch.get("duration") or 0) / 3600.0, 1))
            return out

        tot, bil = _vnoreno(), _vnoreno(True)
        per = {}
        for (cid, pid), (nm, h) in tot.items():
            zkr = id2zkr.get(cid)
            if not zkr:
                continue
            per.setdefault(zkr, []).append((nm, h, bil.get((cid, pid), ("", 0))[1]))
        for zkr in per:
            per[zkr] = sorted(per[zkr], key=lambda x: -x[1])
        return per
    except Exception as e:
        print(f"[clockify] projekty_vsech: {e}")
        return {}


def hodiny_uzivatele_firma(zkratky, datum_od, datum_do):
    """Hodiny po zaměstnancích za CELÉHO klienta (více zakázek/zkratek)."""
    if not je_nakonfigurovano() or not zkratky:
        return []
    try:
        ws = _workspace_id()
        ids = [k.get("id") for k in _seznam(ws, "clients")
               if (k.get("note") or "").strip() in set(zkratky)]
        return _hodiny_uzivatele_ids(ws, ids, datum_od, datum_do)
    except Exception as e:
        print(f"[clockify] uzivatele firma: {e}")
        return []


def posledni_aktivita(dny=60):
    """Vrátí {zkratka: 'YYYY-MM-DD'} poslední odpracovaný záznam (za posledních `dny` dní)."""
    if not je_nakonfigurovano():
        return {}
    try:
        ws = _workspace_id()
        id2zkr = {k.get("id"): (k.get("note") or "").strip()
                  for k in _seznam(ws, "clients") if (k.get("note") or "").strip()}
        proj2cli = {p.get("id"): p.get("clientId") for p in _seznam(ws, "projects")}
        do = datetime.now(timezone.utc)
        od = do - timedelta(days=dny)
        out, page = {}, 1
        while True:
            body = {"dateRangeStart": od.strftime("%Y-%m-%dT00:00:00Z"),
                    "dateRangeEnd": do.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "detailedFilter": {"page": page, "pageSize": 1000, "sortColumn": "DATE"},
                    "sortOrder": "DESCENDING"}
            r = requests.post(f"{REPORTS}/workspaces/{ws}/reports/detailed",
                              headers=_headers(), json=body, timeout=TIMEOUT)
            r.raise_for_status()
            entries = r.json().get("timeentries", []) or []
            if not entries:
                break
            for e in entries:
                cid = e.get("clientId") or proj2cli.get(e.get("projectId"))
                zkr = id2zkr.get(cid)
                if not zkr:
                    continue
                d = (e.get("timeInterval") or {}).get("start", "")[:10]
                if d and (zkr not in out or d > out[zkr]):
                    out[zkr] = d
            if len(entries) < 1000:
                break
            page += 1
        return out
    except Exception as e:
        print(f"[clockify] posledni_aktivita: {e}")
        return {}


def prehled_vse(rok, do_mesic):
    """Celofiremní měsíční data: serie (hodiny) + billable hodiny po zkratce a měsíci
    (pro výpočet tržeb se sazbou z DB)."""
    prazdny = {"serie": [], "bill_zkr_mesic": {}}
    if not je_nakonfigurovano():
        return prazdny
    try:
        import calendar as _cal
        ws = _workspace_id()
        id2zkr = {k.get("id"): (k.get("note") or "").strip()
                  for k in _seznam(ws, "clients") if (k.get("note") or "").strip()}
        serie, bzm = [], {}
        for m in range(1, do_mesic + 1):
            posl = _cal.monthrange(rok, m)[1]
            od = f"{rok}-{m:02d}-01T00:00:00Z"
            do = f"{rok}-{m:02d}-{posl:02d}T23:59:59Z"
            tot = sum(h for cid, _, h in _hodiny_summary(ws, "CLIENT", od, do) if id2zkr.get(cid))
            bil = 0
            for cid, _, h in _hodiny_summary(ws, "CLIENT", od, do, billable=True):
                z = id2zkr.get(cid)
                if z:
                    bil += h
                    d = bzm.setdefault(z, {})
                    d[m] = d.get(m, 0) + h
            serie.append((m, round(tot, 1), round(bil, 1)))
        return {"serie": serie, "bill_zkr_mesic": bzm}
    except Exception as e:
        print(f"[clockify] prehled_vse: {e}")
        return prazdny


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
