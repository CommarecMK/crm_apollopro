"""
services/jizdy_gen.py — generátor návrhu jízd pro knihu jízd (cestovní příkaz).

Logika (deterministická, obhajitelná):
1. KOTVY: každé tankování z faktury = prokazatelná zastávka daného dne → jízda
   z domovské báze přes stanici a zpět (reálné km z OSM, fallback z historie).
2. CÍL: nájezd za měsíc = aktuální tachometr − poslední známý stav.
3. VÝPLŇ: zbytek km do cíle se doplní replikováním REÁLNÝCH historických jízd
   téhož vozidla (kam skutečně jezdí), rozložených na pracovní dny v měsíci.
4. Poslední jízda se doladí na přesný součet, aby konec seděl na zadaný tachometr.
AI se nepoužívá — vše stojí na reálných datech vozidla.
"""
import calendar
import datetime


def _pracovni_dny(rok, mesic):
    """Vrátí seznam dat (po–pá) v měsíci."""
    posl = calendar.monthrange(rok, mesic)[1]
    dny = []
    for d in range(1, posl + 1):
        dt = datetime.date(rok, mesic, d)
        if dt.weekday() < 5:
            dny.append(dt)
    return dny


def _slova(s):
    """Významová slova (>3 znaky, bez diakritiky, lower) — pro rozpoznání domova."""
    s = (s or "").lower()
    for a, b in zip("áčďéěíňóřšťúůýž", "acdeeinorstuuyz"):
        s = s.replace(a, b)
    import re
    return {w for w in re.split(r"[^a-z0-9]+", s) if len(w) > 3}


def _historicke_okruhy(vozidlo_id, rok, mesic, domov=""):
    """Z historie vozidla (jiné měsíce) vytáhne typické okruhy (kam, km) z domova a zpět.
    Vrací list dvojic (kam, km_jeden_smer) seřazený dle četnosti. Domovské varianty vynechá."""
    from ..models import Jizda
    from collections import defaultdict, Counter
    km_dle_cile = defaultdict(list)
    cetnost = defaultdict(int)
    vyskyt = Counter()
    q = [j for j in Jizda.query.filter_by(vozidlo_id=vozidlo_id).all()
         if not (j.rok == rok and j.mesic == mesic) and j.km and j.km > 0 and (j.kam or "").strip()]
    for j in q:
        vyskyt[(j.odkud or "").strip()] += 1
        vyskyt[(j.kam or "").strip()] += 1
    # domovská slova: z konfigurované adresy + nejčastějšího místa v historii
    domov_slova = _slova(domov)
    if vyskyt:
        domov_slova |= _slova(vyskyt.most_common(1)[0][0])
    for j in q:
        cil = (j.kam or "").strip()
        if _slova(cil) & domov_slova:   # cíl je domov (i jiná varianta zápisu) → přeskoč
            continue
        km_dle_cile[cil].append(j.km)
        cetnost[cil] += 1

    def med(xs):
        xs = sorted(xs)
        return xs[len(xs) // 2]
    okruhy = [(cil, med(kms), cetnost[cil]) for cil, kms in km_dle_cile.items()]
    okruhy.sort(key=lambda x: -x[2])  # nejčastější první
    return okruhy


def _domov(vozidlo):
    return (vozidlo.domovska_adresa or "").strip() or "báze"


def navrhni(vozidlo, rok, mesic, cil_km, anchors):
    """anchors = list dict {datum: 'YYYY-MM-DD'|date, misto}. Vrací {jizdy, cil_km, soucet, kotvy}."""
    from . import osm as osm_service
    domov = _domov(vozidlo)
    okruhy = _historicke_okruhy(vozidlo.id, rok, mesic, domov)

    def km_na(cil):
        """Reálná vzdálenost domov→cíl: z historie, jinak OSM."""
        for c, km, _ in okruhy:
            if c.lower() == (cil or "").lower():
                return km
        if vozidlo.domovska_adresa:
            v = osm_service.vzdalenost_km(domov, cil)
            if v:
                return v
        return None

    jizdy = []

    # ── 1) kotvy z tankování (jisté jízdy) ──
    pouzite_dny = set()
    for a in sorted(anchors, key=lambda x: str(x.get("datum") or "")):
        misto = (a.get("misto") or "").strip()
        if not misto:
            continue
        d = a.get("datum")
        if isinstance(d, str):
            try:
                d = datetime.datetime.strptime(d[:10], "%Y-%m-%d").date()
            except ValueError:
                d = None
        km = km_na(misto) or 0
        jizdy.append({"datum": d.isoformat() if d else "", "odkud": domov, "kam": misto,
                      "km": round(km), "ucel": "služebně (tankování)"})
        jizdy.append({"datum": d.isoformat() if d else "", "odkud": misto, "kam": domov,
                      "km": round(km), "ucel": "služebně"})
        if d:
            pouzite_dny.add(d)

    soucet = sum(j["km"] for j in jizdy)
    zbytek = round(cil_km - soucet)

    # ── 2) výplň zbytku z historických okruhů na volné pracovní dny ──
    volne = [d for d in _pracovni_dny(rok, mesic) if d not in pouzite_dny]
    # okruhy použitelné k výplni (mají rozumné km)
    pouzitelne = [(c, km) for c, km, _ in okruhy if km and km > 0] or [("jednání", max(zbytek, 30))]
    i_dest = 0
    i_den = 0
    while zbytek > 15 and i_den < len(volne):
        cil, km1 = pouzitelne[i_dest % len(pouzitelne)]
        i_dest += 1
        okruh = 2 * km1  # tam i zpět
        if okruh > zbytek + 40 and len(pouzitelne) > 1:
            # zkus menší okruh, ať nepřestřelíme
            mensi = min(pouzitelne, key=lambda p: abs(2 * p[1] - zbytek))
            cil, km1 = mensi
            okruh = 2 * km1
        d = volne[i_den]
        i_den += 1
        # poslední doladění: pokud by okruh přestřelil, zkrať na zbytek (jednosměrná korekce)
        if okruh >= zbytek:
            jizdy.append({"datum": d.isoformat(), "odkud": domov, "kam": cil,
                          "km": round(zbytek), "ucel": "služebně"})
            zbytek = 0
            break
        jizdy.append({"datum": d.isoformat(), "odkud": domov, "kam": cil,
                      "km": round(km1), "ucel": "služebně"})
        jizdy.append({"datum": d.isoformat(), "odkud": cil, "kam": domov,
                      "km": round(km1), "ucel": "služebně"})
        zbytek -= okruh

    # zbylé drobné km přidej k poslední jízdě (přesné dorovnání na tachometr)
    if jizdy and abs(zbytek) > 0:
        jizdy[-1]["km"] = max(round(jizdy[-1]["km"] + zbytek), 0)

    jizdy.sort(key=lambda j: (j["datum"] or "9999"))
    soucet = sum(j["km"] for j in jizdy)
    return {"jizdy": jizdy, "cil_km": cil_km, "soucet": round(soucet),
            "kotvy": len([a for a in anchors if a.get("misto")]),
            "chyba": None if jizdy else "Nejsou kotvy ani historie — zadej aspoň jednu jízdu ručně."}
