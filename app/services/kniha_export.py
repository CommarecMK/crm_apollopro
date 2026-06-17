"""
services/kniha_export.py — export knihy jízd do Excelu (styl pro FÚ).

Hlavička s údaji o vozidle, tabulka jízd (datum, odkud, kam, účel, km, soukromá),
součty, stav tachometru. Vrací bytes .xlsx.
"""
import io

NAVY = "173767"
NAVY_TXT = "FFFFFF"
SVETLA = "F3F5F7"
CZ_MESICE = ["", "leden", "únor", "březen", "duben", "květen", "červen",
             "červenec", "srpen", "září", "říjen", "listopad", "prosinec"]


def _den(d):
    return d.strftime("%-d. %-m. %Y") if d else ""


def export_xlsx(vozidlo, rok, mesic, jizdy, stav_zacatek=None, stav_konec=None):
    """jizdy = list objektů Jizda (řazené). Vrací bytes .xlsx."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"{mesic:02d}-{rok}"

    nadpis_font = Font(name="Calibri", bold=True, size=14, color=NAVY)
    hl_font = Font(name="Calibri", bold=True, color=NAVY_TXT)
    hl_fill = PatternFill("solid", fgColor=NAVY)
    zebra = PatternFill("solid", fgColor=SVETLA)
    tenky = Side(style="thin", color="C9D2DE")
    ramecek = Border(left=tenky, right=tenky, top=tenky, bottom=tenky)
    center = Alignment(horizontal="center", vertical="center")
    levy = Alignment(horizontal="left", vertical="center", wrap_text=True)

    # ── hlavička ──
    ws["A1"] = "KNIHA JÍZD"
    ws["A1"].font = nadpis_font
    ws["A2"] = f"{CZ_MESICE[mesic]} {rok}"
    ws["A2"].font = Font(bold=True, size=12)

    info = [
        ("Vozidlo:", vozidlo.model or ""),
        ("SPZ:", vozidlo.spz or ""),
        ("Palivo:", vozidlo.palivo or ""),
        ("Průměrná spotřeba (l/100 km):", vozidlo.spotreba if vozidlo.spotreba is not None else ""),
        ("Stav tachometru na začátku:", stav_zacatek if stav_zacatek is not None else ""),
        ("Stav tachometru na konci:", stav_konec if stav_konec is not None else ""),
    ]
    r = 4
    for popis, hod in info:
        ws.cell(row=r, column=1, value=popis).font = Font(bold=True)
        ws.cell(row=r, column=2, value=hod)
        r += 1

    # ── tabulka jízd ──
    hlavicka = ["Datum", "Odkud", "Kam", "Účel cesty", "Km", "Soukromá"]
    hr = r + 1
    for c, h in enumerate(hlavicka, start=1):
        bunka = ws.cell(row=hr, column=c, value=h)
        bunka.font = hl_font
        bunka.fill = hl_fill
        bunka.alignment = center
        bunka.border = ramecek

    soucet = soucet_sluzba = 0.0
    rr = hr + 1
    for i, j in enumerate(jizdy):
        km = float(j.km or 0)
        soucet += km
        if not j.soukroma:
            soucet_sluzba += km
        radek = [_den(j.datum), j.odkud or "", j.kam or "", j.ucel or "",
                 km, "ano" if j.soukroma else "ne"]
        for c, val in enumerate(radek, start=1):
            bunka = ws.cell(row=rr, column=c, value=val)
            bunka.border = ramecek
            bunka.alignment = center if c in (1, 5, 6) else levy
            if i % 2 == 1:
                bunka.fill = zebra
        rr += 1

    # ── součty ──
    ws.cell(row=rr, column=4, value="Celkem km:").font = Font(bold=True)
    ws.cell(row=rr, column=5, value=round(soucet, 1)).font = Font(bold=True)
    ws.cell(row=rr + 1, column=4, value="Z toho služebně:").font = Font(bold=True)
    ws.cell(row=rr + 1, column=5, value=round(soucet_sluzba, 1)).font = Font(bold=True)
    ws.cell(row=rr + 2, column=4, value="Z toho soukromě:").font = Font(bold=True)
    ws.cell(row=rr + 2, column=5, value=round(soucet - soucet_sluzba, 1)).font = Font(bold=True)

    # ── šířky sloupců ──
    for col, w in {"A": 14, "B": 26, "C": 26, "D": 34, "E": 9, "F": 11}.items():
        ws.column_dimensions[col].width = w

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
