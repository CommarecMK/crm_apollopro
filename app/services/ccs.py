"""
services/ccs.py — deterministické parsování faktury CCS (palivová karta).

Struktura PDF: souhrnné strany + strany přílohy po jednotlivých kartách.
Každá strana přílohy má v hlavičce právě jednu SPZ; transakce na ní patří k té SPZ.
Sloupce řádku jsou kotvené slovem "Kč": ... <jedn.bezDPH> <jedn.sDPH> Kč <Celkem> <BezDPH> <DPH> <%>
Ověřeno proti reálné faktuře (součty litrů sedí na desetinu se souhrnem dokladu).
"""
import re

SPZ_RE = re.compile(r"\b\d[A-Z]{1,2}\s?[A-Z0-9]{3,4}\b")
# pořadí důležité: "Natural +" se musí zkontrolovat dřív než "Natural"
DRUHY = [
    ("Natural +", "natural_plus"), ("Natural+", "natural_plus"),
    ("Nafta plus", "nafta_plus"), ("Diesel+", "nafta_plus"),
    ("Nafta", "nafta"), ("Diesel", "nafta"), ("Natural", "natural"),
    ("AdBlue", "adblue"), ("CNG", "cng"), ("LPG", "lpg"),
]
PHM_PREFIXY = ("Nafta", "Natural", "Diesel")

RADEK_RE = re.compile(
    r"^(\d\d\.\d\d\.\d\d)\s+(\d\d\.\d\d)\s+\wR\s+(.*?Kč)\s+([\-\d.]+)\s+([\-\d.]+)\s+([\-\d.]+)\s+([\d.]+)\s*$"
)
MID_RE = re.compile(
    r"^(.*?)\s+(\d)\s+([A-Za-zÁ-ž][\w.,+ \-]*?)\s+([\-\d.]+)\s+(?:[\-\d.]+\s+)?([\-\d.]+)\s+([\-\d.]+)$"
)


def _je_phm(nazev):
    return any(nazev.startswith(p) for p in PHM_PREFIXY)


def _druh(nazev):
    for prefix, kod in DRUHY:
        if nazev.startswith(prefix):
            return kod
    return None


def _datum_iso(d):
    """'04.05.26' -> '2026-05-04'"""
    try:
        dd, mm, yy = d.split(".")
        return f"20{yy}-{mm}-{dd}"
    except ValueError:
        return ""


def parsuj_text(stranky):
    """Z listu textů stran vytvoří řádky tankování. Vrací list dictů."""
    radky = []
    for t in stranky:
        spzs = set(SPZ_RE.findall(t))
        # detailní karta = právě jedna SPZ; přeskoč souhrn (víc SPZ) i strany bez SPZ
        spz = list(spzs)[0].replace("  ", " ").strip() if len(spzs) == 1 else None
        for ln in t.splitlines():
            m = RADEK_RE.match(ln)
            if not m:
                continue
            datum, _cas, mid, celkem, _bez, _dph, _proc = m.groups()
            mm = MID_RE.match(mid[:-2].strip())  # uřízni koncové "Kč"
            if not mm:
                continue
            misto, _ink, nazev, mnoz, _jb, _js = mm.groups()
            nazev = nazev.strip()
            phm = _je_phm(nazev)

            def _f(v):
                try:
                    return round(float(v), 2)
                except (ValueError, TypeError):
                    return None
            radky.append({
                "spz": spz or "",
                "datum": _datum_iso(datum),
                "misto": misto.strip()[:300],
                "nazev": nazev[:60],
                "druh": _druh(nazev) if phm else nazev[:60],
                "kategorie": "phm" if phm else "ostatni",
                "litry": _f(mnoz) if phm else None,
                "castka": _f(celkem),
            })
    return radky


def parsuj_pdf(pdf_bytes):
    """Vytáhne řádky z PDF bytů. Vrací {radky, chyba}."""
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            return {"radky": [], "chyba": "Chybí knihovna pypdf."}
    import io
    try:
        r = PdfReader(io.BytesIO(pdf_bytes))
        stranky = [(p.extract_text() or "") for p in r.pages]
    except Exception as e:
        return {"radky": [], "chyba": f"PDF se nepodařilo přečíst: {e}"}
    radky = parsuj_text(stranky)
    if not radky:
        return {"radky": [], "chyba": "V faktuře se nenašly žádné transakce (zkontroluj formát CCS)."}
    return {"radky": radky, "chyba": None}
