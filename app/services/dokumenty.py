"""
services/dokumenty.py — indexace dokumentů klienta z OneDrive a fulltextové hledání.

Index = extrahovaný text + metadata v tabulce KlientDokument (Postgres na Railway).
Originály zůstávají na SharePointu; u nás je jen text pro hledání a (později) AI.
"""
from datetime import datetime, timezone

from ..extensions import db
from . import onedrive, extrakce, embeddings

MAX_BAJTU = 25 * 1024 * 1024  # nečteme soubory větší než 25 MB


def index_klienta(firma):
    """Projde složku klienta, vytáhne text z podporovaných souborů a uloží/aktualizuje index.
    Vrací (pocet_indexovano, pocet_souboru, chyba|None)."""
    from ..models import KlientDokument
    if not onedrive.je_nakonfigurovano() or not firma.onedrive_odkaz:
        return 0, 0, "Klient nemá napojenou OneDrive složku."
    data = onedrive.vsechny_soubory(firma.onedrive_odkaz)
    if data is None:
        return 0, 0, "Složku se nepodařilo načíst (ověř odkaz/oprávnění)."
    drive_id, soubory = data["drive_id"], data["soubory"]
    ted = datetime.now(timezone.utc).isoformat(timespec="seconds")
    existujici = {d.item_id: d for d in KlientDokument.query.filter_by(firma_id=firma.id).all()}
    videno, indexovano = set(), 0
    for s in soubory:
        videno.add(s["id"])
        if not extrakce.lze_extrahovat(s["nazev"]) or (s["velikost"] or 0) > MAX_BAJTU:
            continue
        obsah = onedrive.stahni(drive_id, s["id"])
        if not obsah:
            continue
        text = extrakce.extrahuj_text(obsah, s["nazev"]) or ""
        d = existujici.get(s["id"]) or KlientDokument(firma_id=firma.id, item_id=s["id"])
        d.drive_id = drive_id
        d.nazev = s["nazev"]
        d.cesta = s["cesta"]
        d.web_url = s["web_url"]
        d.velikost = s["velikost"] or 0
        d.text = text
        d.updated = ted
        db.session.add(d)
        db.session.flush()  # potřebujeme d.id pro chunky
        embeddings.reindex_dokument(d)
        indexovano += 1
        if indexovano % 5 == 0:   # průběžné ukládání (vidět postup, neztratit při pádu)
            db.session.commit()
    # odstraň záznamy souborů, které už ve složce nejsou
    for item_id, d in existujici.items():
        if item_id not in videno:
            db.session.delete(d)
    db.session.commit()
    return indexovano, len(soubory), None


def index_klienta_async(app, firma_id):
    """Spustí indexaci na pozadí (mimo webový request) — nastaví/sundá příznak běhu."""
    from ..models import Firma
    with app.app_context():
        firma = Firma.query.get(firma_id)
        if not firma:
            return
        firma.dok_index_bezi = True
        db.session.commit()
        try:
            index_klienta(firma)
        except Exception as e:
            print(f"[dokumenty] async index: {e}")
            db.session.rollback()
        finally:
            firma = Firma.query.get(firma_id)
            if firma:
                firma.dok_index_bezi = False
                db.session.commit()


def stav(firma_id):
    """Vrátí (pocet_dokumentu, posledni_aktualizace) z indexu."""
    from ..models import KlientDokument
    q = KlientDokument.query.filter_by(firma_id=firma_id)
    pocet = q.count()
    posl = q.order_by(KlientDokument.updated.desc()).first()
    return pocet, (posl.updated if posl else None)


def hledej(dotaz, firma_id=None, limit=40):
    """Fulltextové (ILIKE) hledání v indexu. firma_id=None → napříč všemi klienty.
    Vrací [{nazev, cesta, web_url, firma_id, firma, snippet}]."""
    from ..models import KlientDokument, Firma
    dotaz = (dotaz or "").strip()
    if not dotaz:
        return []
    q = KlientDokument.query
    if firma_id:
        q = q.filter_by(firma_id=firma_id)
    vzor = f"%{dotaz}%"
    q = q.filter(db.or_(KlientDokument.text.ilike(vzor), KlientDokument.nazev.ilike(vzor)))
    vysledky = q.limit(limit).all()
    nazvy = {f.id: f.nazev for f in Firma.query.all()}
    out = []
    for d in vysledky:
        out.append({"nazev": d.nazev, "cesta": d.cesta, "web_url": d.web_url,
                    "firma_id": d.firma_id, "firma": nazvy.get(d.firma_id, ""),
                    "snippet": _snippet(d.text, dotaz)})
    return out


def _snippet(text, dotaz, okoli=160):
    if not text:
        return ""
    i = text.lower().find(dotaz.lower())
    if i < 0:
        return text[:okoli].strip() + "…"
    od = max(0, i - okoli // 2)
    return ("…" if od else "") + text[od:i + len(dotaz) + okoli // 2].strip() + "…"
