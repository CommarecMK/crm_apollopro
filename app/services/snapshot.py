"""
services/snapshot.py — denní snímek dat z Clockify uložený v DB.

Stránky čtou ze snapshotu (rychlé), Clockify se volá jen při obnově (1× denně).
Struktura JSON:
{
  "updated": "ISO datum",
  "mesice": ["2025-05", ..., "2026-06"],          # okno měsíců (14)
  "hodiny": { zkratka: { "YYYY-MM": [tot, bill] } },
  "uzivatele": { zkratka: [[jmeno, celkem, bill], ...] }   # YTD aktuální rok
}
"""
import json
import calendar
from datetime import datetime, timezone

from ..extensions import db
from . import clockify


def _okno_mesicu(pocet=14):
    now = datetime.now(timezone.utc)
    out, r, m = [], now.year, now.month
    for _ in range(pocet):
        out.append((r, m))
        m -= 1
        if m == 0:
            m, r = 12, r - 1
    return list(reversed(out))


def nacti():
    """Vrátí (data_dict, updated_str). Když snapshot není, prázdné."""
    from ..models import Snapshot
    snap = Snapshot.query.first()
    if not snap or not snap.data:
        return {}, None
    try:
        return json.loads(snap.data), snap.updated
    except Exception:
        return {}, snap.updated


def obnov():
    """Stáhne data z Clockify a uloží snapshot. Volá se 1× denně (nebo ručně)."""
    from ..models import Snapshot, Zakazka
    if not clockify.je_nakonfigurovano():
        return False, "Clockify není nakonfigurovaný"
    try:
        ws = clockify._workspace_id()
        id2zkr = {k.get("id"): (k.get("note") or "").strip()
                  for k in clockify._seznam(ws, "clients") if (k.get("note") or "").strip()}
        okno = _okno_mesicu()
        labels = [f"{r}-{m:02d}" for r, m in okno]
        hodiny = {}
        for r, m in okno:
            posl = calendar.monthrange(r, m)[1]
            od = f"{r}-{m:02d}-01T00:00:00Z"
            do = f"{r}-{m:02d}-{posl:02d}T23:59:59Z"
            key = f"{r}-{m:02d}"
            tot = {cid: h for cid, _, h in clockify._hodiny_summary(ws, "CLIENT", od, do)}
            bil = {cid: h for cid, _, h in clockify._hodiny_summary(ws, "CLIENT", od, do, billable=True)}
            for cid, zkr in id2zkr.items():
                t, b = tot.get(cid, 0), bil.get(cid, 0)
                if t or b:
                    hodiny.setdefault(zkr, {})[key] = [round(t, 1), round(b, 1)]

        # Hodiny po zaměstnancích — jedním dotazem pro všechny klienty (YTD aktuální rok)
        now = datetime.now(timezone.utc)
        od_rok = f"{now.year}-01-01T00:00:00Z"
        do_ted = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        uzivatele = clockify.uzivatele_vsech_klientu(od_rok, do_ted)

        data = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "mesice": labels, "hodiny": hodiny, "uzivatele": uzivatele}

        snap = Snapshot.query.first() or Snapshot()
        snap.updated = data["updated"]
        snap.data = json.dumps(data)
        db.session.add(snap)
        db.session.commit()
        return True, data["updated"]
    except Exception as e:
        db.session.rollback()
        return False, str(e)


# ── Pomocné funkce pro čtení ze snapshotu ────────────────────────
def hodiny_zkr(snap, zkr, mesic_list):
    """Sečte (tot, bill) pro jednu zkratku přes zadané měsíce ('YYYY-MM')."""
    h = (snap.get("hodiny") or {}).get(zkr, {})
    tot = sum(h.get(mm, [0, 0])[0] for mm in mesic_list)
    bil = sum(h.get(mm, [0, 0])[1] for mm in mesic_list)
    return round(tot, 1), round(bil, 1)


def uzivatele_zkr(snap, zkratky):
    """Sloučí hodiny po zaměstnancích přes více zakázek → [(jmeno, celkem, bill)]."""
    agg = {}
    uz = snap.get("uzivatele") or {}
    for zkr in zkratky:
        for jmeno, celkem, bill in uz.get(zkr, []):
            a = agg.setdefault(jmeno, [0, 0])
            a[0] += celkem
            a[1] += bill
    return sorted([(j, round(v[0], 1), round(v[1], 1)) for j, v in agg.items()], key=lambda x: -x[1])
