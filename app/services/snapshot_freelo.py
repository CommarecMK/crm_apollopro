"""
services/snapshot_freelo.py — denní snímek úkolů z Freela uložený v DB.

Operativa (seznam, dashboard, řešitelé, zavřené) čte odtud → rychlé a 100% přesné.
Freelo se volá jen při obnově (1× denně nebo ručně).

Struktura JSON:
{
  "updated": "ISO datum",
  "tasklisty": { "<tasklist_id>": {"aktivni":[...], "hotove":[...], "open_count":n} }
}
"""
import json
import time
from datetime import datetime, timezone

from ..extensions import db
from . import freelo

# Cache parsovaného snapshotu (ať se JSON neparsuje opakovaně v rámci požadavku/minuty)
_MEM = {"t": 0, "data": None, "updated": None}
_MEM_TTL = 60


def nacti():
    if _MEM["data"] is not None and (time.time() - _MEM["t"]) < _MEM_TTL:
        return _MEM["data"], _MEM["updated"]
    from ..models import FreeloSnapshot
    snap = FreeloSnapshot.query.first()
    if not snap or not snap.data:
        _MEM.update(t=time.time(), data={}, updated=None)
        return {}, None
    try:
        data = json.loads(snap.data)
    except Exception:
        data = {}
    _MEM.update(t=time.time(), data=data, updated=snap.updated)
    return data, snap.updated


def tasklist(tasklist_id):
    """Vrátí {aktivni,hotove,open_count} pro tasklist ze snapshotu, nebo None když chybí."""
    data, _ = nacti()
    return (data.get("tasklisty") or {}).get(str(tasklist_id))


def obnov():
    """Stáhne úkoly všech napojených klientů z Freela + plnou mapu reakcí a uloží snapshot."""
    from ..models import FreeloSnapshot, Firma
    if not freelo.je_nakonfigurovano():
        return False, "Freelo není nakonfigurované"
    try:
        # Plná mapa posledních reakcí (komentářů) napříč všemi úkoly
        mapa = freelo.reakce_mapa(vse=True)
        tasklisty = {}
        napojene = (Firma.query.filter(Firma.freelo_tasklist_id.isnot(None)).all())
        for f in napojene:
            tlid = f.freelo_tasklist_id
            tasklisty[str(tlid)] = freelo.ukoly_raw(tlid, mapa)
        data = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "tasklisty": tasklisty, "reakci": len(mapa)}
        snap = FreeloSnapshot.query.first() or FreeloSnapshot()
        snap.updated = data["updated"]
        snap.data = json.dumps(data)
        db.session.add(snap)
        db.session.commit()
        _MEM.update(t=0, data=None, updated=None)  # invaliduj cache
        return True, data["updated"]
    except Exception as e:
        db.session.rollback()
        return False, str(e)
