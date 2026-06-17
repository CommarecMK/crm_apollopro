"""
services/fleet.py — import vozidel ze souboru 'Souhrn aut.xlsx' (Fleet).

Očekávané sloupce (řádek 0 = hlavička):
Auto | SPZ | Splátka bez DPH | Začátek nájmu | Konec nájmu | Nájezd - celkem | Servis
Parsování je odolné vůči pořadí sloupců (hledá podle názvu hlavičky).
"""
import io
import datetime


def _norm(s):
    return (str(s or "").strip().lower()
            .replace("á", "a").replace("č", "c").replace("ě", "e")
            .replace("é", "e").replace("í", "i").replace("ř", "r")
            .replace("š", "s").replace("ž", "z").replace("ý", "y")
            .replace("ú", "u").replace("ů", "u").replace("ó", "o").replace("ň", "n").replace("ť", "t"))


# klíč interní -> kandidáti v hlavičce (normalizované)
SLOUPCE = {
    "model":   ["auto", "vozidlo", "model"],
    "spz":     ["spz", "rz", "registracni znacka"],
    "splatka": ["splatka bez dph", "splatka", "najem bez dph"],
    "najem_od": ["zacatek najmu", "od", "zacatek"],
    "najem_do": ["konec najmu", "do", "konec"],
    "najezd_limit": ["najezd - celkem", "najezd celkem", "najezd"],
    "servis":  ["servis", "servisni interval"],
}


def _datum(v):
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.date() if isinstance(v, datetime.datetime) else v
    return None


def _int(v):
    try:
        return int(float(str(v).replace(" ", "").replace(",", ".")))
    except (ValueError, TypeError):
        return None


def _float(v):
    try:
        return float(str(v).replace(" ", "").replace(",", "."))
    except (ValueError, TypeError):
        return None


def parsuj_souhrn(xlsx_bytes):
    """Vrátí {radky:[{model,spz,splatka,najem_od,najem_do,najezd_limit,servis}], chyba}."""
    try:
        import openpyxl
    except ImportError:
        return {"radky": [], "chyba": "Chybí knihovna openpyxl."}
    try:
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True)
    except Exception as e:
        return {"radky": [], "chyba": f"Soubor se nepodařilo otevřít: {e}"}
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return {"radky": [], "chyba": "Prázdný list."}
    hlavicka = [_norm(c) for c in rows[0]]
    # mapuj interní klíč -> index sloupce
    idx = {}
    for klic, kandidati in SLOUPCE.items():
        for i, h in enumerate(hlavicka):
            if h in kandidati:
                idx[klic] = i
                break
    if "spz" not in idx:
        return {"radky": [], "chyba": "V souboru nebyl nalezen sloupec SPZ."}
    out = []
    for r in rows[1:]:
        def g(klic):
            i = idx.get(klic)
            return r[i] if i is not None and i < len(r) else None
        spz = str(g("spz") or "").strip().upper()
        model = str(g("model") or "").strip()
        if not spz and not model:
            continue
        out.append({
            "model": model,
            "spz": spz,
            "splatka": _float(g("splatka")),
            "najem_od": _datum(g("najem_od")),
            "najem_do": _datum(g("najem_do")),
            "najezd_limit": _int(g("najezd_limit")),
            "servis": _int(g("servis")),
        })
    return {"radky": out, "chyba": None}


def import_vozidla(radky):
    """Upsert vozidel dle SPZ. Vrací (vytvoreno, aktualizovano, preskoceno)."""
    from ..models import Vozidlo
    from ..extensions import db
    vytvoreno = aktualizovano = preskoceno = 0

    def _spz_norm(s):
        return (s or "").replace(" ", "").upper()

    existujici = {_spz_norm(v.spz): v for v in Vozidlo.query.all()}
    for r in radky:
        spz = r.get("spz") or ""
        if not spz:
            preskoceno += 1  # bez SPZ nelze párovat (např. dosud nepřihlášené auto)
            continue
        v = existujici.get(_spz_norm(spz))
        novy = v is None
        if novy:
            v = Vozidlo(spz=spz, aktivni=True)
            db.session.add(v)
        if r.get("model"):
            v.model = r["model"][:120]
        if r.get("splatka") is not None:
            v.splatka = r["splatka"]
        if r.get("najem_od"):
            v.najem_od = r["najem_od"]
        if r.get("najem_do"):
            v.najem_do = r["najem_do"]
        if r.get("najezd_limit") is not None:
            v.najezd_limit = r["najezd_limit"]
        if r.get("servis") is not None:
            v.servis_interval_km = r["servis"]
        if novy:
            vytvoreno += 1
            existujici[_spz_norm(spz)] = v
        else:
            aktualizovano += 1
    db.session.commit()
    return vytvoreno, aktualizovano, preskoceno
