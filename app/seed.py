"""
seed.py — naimportuje zakázky z data/zakazky.xlsx do DB (jen pokud je prázdná).
Sloupce: Zkratka | Název | Firma - Název
"""
import os
import openpyxl
from .extensions import db
from .models import Firma, Zakazka

XLSX = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "zakazky.xlsx")


def _typ_sluzby(nazev):
    return nazev.split(" - ")[-1].strip() if " - " in nazev else ""


def seed_pokud_prazdno():
    if Zakazka.query.first():
        return  # už naplněno
    if not os.path.exists(XLSX):
        print("[seed] data/zakazky.xlsx nenalezen — přeskakuji.")
        return

    wb = openpyxl.load_workbook(XLSX, data_only=True)
    ws = wb.active
    firmy_cache = {}
    pocet = 0

    for row in list(ws.iter_rows(values_only=True))[1:]:
        if not row or not row[0]:
            continue
        zkratka = str(row[0]).strip()
        nazev = str(row[1]).strip() if row[1] else zkratka
        firma_nazev = str(row[2]).strip() if len(row) > 2 and row[2] else "Neznámá firma"

        firma = firmy_cache.get(firma_nazev)
        if not firma:
            firma = Firma.query.filter_by(nazev=firma_nazev).first()
            if not firma:
                firma = Firma(nazev=firma_nazev)
                db.session.add(firma)
                db.session.flush()
            firmy_cache[firma_nazev] = firma

        db.session.add(Zakazka(
            zkratka=zkratka, nazev=nazev, typ_sluzby=_typ_sluzby(nazev),
            stav="aktivni", firma_id=firma.id,
        ))
        pocet += 1

    db.session.commit()
    print(f"[seed] Naimportováno {pocet} zakázek, {len(firmy_cache)} firem.")
