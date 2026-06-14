"""
routes/main.py — přihlášení + kokpit "Stav zakázek".
"""
import os
import calendar
from datetime import datetime, timezone, date
from flask import (Blueprint, render_template, request, redirect,
                   url_for, session, flash, jsonify, current_app)

from werkzeug.security import check_password_hash, generate_password_hash
from ..extensions import db, COMPANY_ICO
from ..models import Zakazka, Firma, Kontakt, User, Faktura


def _napln_faktury(zakazky, mesic_keys, vse=False):
    """U zakázek typu 'jednorazovy' nastaví ._faktury = součet faktur.
    vse=False → jen faktury ve zvoleném období (pro roční reporty);
    vse=True  → všechny faktury zakázky (kumulativně, pro detail zakázky/firmy)."""
    keys = set(mesic_keys)
    for z in zakazky:
        if z.typ_rozpoctu == "jednorazovy":
            z._faktury = round(sum(f.castka for f in z.faktury
                                   if f.castka and (vse or (f.datum and f.datum.strftime("%Y-%m") in keys))))


def _mesicu_mezi(od, do):
    return (do.year - od.year) * 12 + (do.month - od.month) + 1


def _tydny_v_mesici(z, y, m):
    """Počet týdnů, kdy je zakázka v daném měsíci aktivní (dle datum_od/do). Aktivní dny / 7."""
    first = date(y, m, 1)
    last = date(y, m, calendar.monthrange(y, m)[1])
    start = max(first, z.datum_od) if z.datum_od else first
    end = min(last, z.datum_do) if z.datum_do else last
    if end < start:
        return 0
    return ((end - start).days + 1) / 7.0


def _tydny_obdobi(z, months):
    """Součet týdnů přes seznam měsíců (y,m)."""
    return sum(_tydny_v_mesici(z, y, m) for (y, m) in months)


def _plan_mesic(z, ym):
    """Plánovaná fakturace zakázky v daném měsíci ym=(rok,měsíc)."""
    if z.datum_od and ym < (z.datum_od.year, z.datum_od.month):
        return 0
    if z.datum_do and ym > (z.datum_do.year, z.datum_do.month):
        return 0
    if z.typ_rozpoctu == "mesicni":
        return (z.rozpocet_hodin_tyden or 0) * _tydny_v_mesici(z, ym[0], ym[1]) * (z.hodinova_sazba or 0)
    if z.typ_rozpoctu == "analyza":
        c = z.budget_castka or 0
        if z.datum_od and ym == (z.datum_od.year, z.datum_od.month):
            return 0.4 * c
        if z.datum_do and ym == (z.datum_do.year, z.datum_do.month):
            return 0.6 * c
        return 0
    if z.typ_rozpoctu == "jednorazovy":
        return 0  # nárazové prodeje neplánujeme
    total = z.budget_castka or (z.rozpocet_hodin or 0) * (z.hodinova_sazba or 0)
    if total and z.datum_od and z.datum_do:
        n = _mesicu_mezi(z.datum_od, z.datum_do)
        return total / n if n > 0 else 0
    return 0


def _fin_mesicne(z, hod, do_mesic, rok):
    """Měsíční finance zakázky: vrátí (fakturováno, nenaplněný_potenciál, plán) sečtené po měsících.
    Potenciál se počítá v každém měsíci zvlášť (přeplněný měsíc nevynuluje nedotažený)."""
    h = (hod or {}).get(z.zkratka, {})
    fakt = pot = plan = 0
    for m in range(1, do_mesic + 1):
        ym, key = (rok, m), f"{rok}-{m:02d}"
        p = _plan_mesic(z, ym)
        f = h.get(key, [0, 0])[1] * z.efekt_sazba
        f += sum(fa.castka for fa in z.faktury if fa.datum and fa.datum.strftime("%Y-%m") == key)
        fakt += f
        plan += p
        pot += max(p - f, 0)
    return round(fakt), round(pot), round(plan)


def _okno_mesicu(now, dopredu=6):
    """Měsíce od ledna aktuálního roku po (aktuální měsíc + dopředu)."""
    def add(y, m, d):
        idx = y * 12 + (m - 1) + d
        return idx // 12, idx % 12 + 1
    konec = add(now.year, now.month, dopredu)
    okno, cur = [], (now.year, 1)
    while cur <= konec:
        okno.append(cur)
        cur = add(cur[0], cur[1], 1)
    return okno


def _fin_serie(zakazky, hod, okno, ted):
    """3-segmentová měsíční řada: fakturováno (zelená, minulost) + potenciál (červená, minulost)
    + budoucí plán (modrá). Vrací dict pro graf."""
    labels, fakt, pot, budouci = [], [], [], []
    for (y, m) in okno:
        ym, key = (y, m), f"{y}-{m:02d}"
        plan_m = sum(_plan_mesic(z, ym) for z in zakazky)
        fakt_m = sum(hod.get(z.zkratka, {}).get(key, [0, 0])[1] * z.efekt_sazba for z in zakazky)
        fakt_m += sum(fa.castka for z in zakazky for fa in z.faktury
                      if fa.datum and fa.datum.strftime("%Y-%m") == key)
        labels.append(f"{MESICE_ZKR[m]} {str(y)[2:]}")
        if ym <= ted:
            fakt.append(round(fakt_m)); pot.append(round(max(plan_m - fakt_m, 0))); budouci.append(0)
        else:
            fakt.append(0); pot.append(0); budouci.append(round(plan_m))
    return {"labels": labels, "fakt": fakt, "pot": pot, "budouci": budouci}


def _bez_internich(q):
    """Odfiltruje interní zakázky (pod IČO Commarecu)."""
    return q.filter(db.or_(Firma.ico.is_(None), Firma.ico != COMPANY_ICO))


def _jen_interni(q):
    return q.filter(Firma.ico == COMPANY_ICO)
from ..auth import (login_required, klient_required, zakazky_required,
                    admin_required, finance_required)
from ..services import (clockify, firmy as firmy_service, snapshot,
                        freelo as freelo_service, snapshot_freelo, onedrive as onedrive_service,
                        dokumenty as dokumenty_service, ai as ai_service)

bp = Blueprint("main", __name__)

MESICE_CZ = ["", "leden", "únor", "březen", "duben", "květen", "červen",
             "červenec", "srpen", "září", "říjen", "listopad", "prosinec"]
MESICE_ZKR = ["", "led", "úno", "bře", "dub", "kvě", "čvn",
              "čvc", "srp", "zář", "říj", "lis", "pro"]


def _obdobi(mesic):
    """Z parametru 'YYYY-MM' vrátí (od, do, popis, pocet_mesicu).
    Prázdné = celý aktuální rok (od ledna do teď)."""
    now = datetime.now(timezone.utc)
    if mesic and "-" in mesic:
        rok, m = (int(x) for x in mesic.split("-"))
        posl = calendar.monthrange(rok, m)[1]
        od = f"{rok}-{m:02d}-01T00:00:00Z"
        do = f"{rok}-{m:02d}-{posl:02d}T23:59:59Z"
        return od, do, f"{MESICE_CZ[m]} {rok}", 1
    # Celý rok = leden až aktuální měsíc → počet měsíců = číslo aktuálního měsíce
    return (f"{now.year}-01-01T00:00:00Z",
            now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            f"rok {now.year}", now.month)


def _seznam_mesicu(pocet=14):
    """Vrátí list (hodnota, popis) posledních N měsíců pro rozbalovátko."""
    now = datetime.now(timezone.utc)
    out = []
    r, m = now.year, now.month
    for _ in range(pocet):
        out.append((f"{r}-{m:02d}", f"{MESICE_CZ[m]} {r}"))
        m -= 1
        if m == 0:
            m, r = 12, r - 1
    return out


def _prihlas(u):
    session["user_id"] = u.id
    session["user_name"] = u.jmeno or u.email
    session["user_role"] = u.role


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        heslo = request.form.get("heslo", "")
        u = User.query.filter_by(email=email).first()
        if u and u.aktivni and u.password_hash and check_password_hash(u.password_hash, heslo):
            _prihlas(u)
            return redirect(url_for("main.prehled"))
        flash("Nesprávný e-mail nebo heslo.", "error")
    return render_template("login.html")


@bp.route("/auth")
def sso_vstup():
    """SSO z Apollo Pro portálu. Najde/vytvoří uživatele kokpitu (nový = role 'majitel')."""
    from ..sso import over_token
    udaje = over_token(request.args.get("token", ""))
    portal = os.environ.get("PORTAL_URL", "https://apollopro.io")
    if not udaje:
        return redirect(portal + "/login")
    je_superadmin = udaje.get("role") == "superadmin"
    sso_id = udaje.get("id")
    email = (udaje.get("email") or "").strip().lower()
    try:
        # 1) přednostně spáruj podle e-mailu (kanonický účet), jinak podle sso_id
        u = User.query.filter_by(email=email).first() if email else None
        if not u and sso_id is not None:
            u = User.query.filter_by(sso_id=sso_id).first()
        # 2) když nic nenajdeme, založ nový
        if not u:
            u = User(jmeno=udaje.get("name"), email=email or None,
                     role="admin" if je_superadmin else "majitel", aktivni=True)
            db.session.add(u)
            db.session.flush()
        # 3) sso_id přiřaď výhradně tomuto účtu — uvolni ho od případného duplikátu
        #    (jinak by spadlo na unikátní omezení sso_id)
        if sso_id is not None:
            for jiny in User.query.filter(User.sso_id == sso_id, User.id != u.id).all():
                jiny.sso_id = None
            db.session.flush()
            u.sso_id = sso_id
        # 4) doplň chybějící údaje + promotion superadmina (roli nedegradujeme)
        if email and not u.email:
            u.email = email
        if not u.jmeno:
            u.jmeno = udaje.get("name")
        if je_superadmin and u.role != "admin":
            u.role = "admin"
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[sso] chyba párování: {e}")
        return redirect(portal + "/login")
    if not u.aktivni:
        return redirect(portal + "/login")
    _prihlas(u)
    nxt = request.args.get("next", "")
    if nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
    return redirect(url_for("main.prehled"))


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("main.login"))


@bp.route("/diagnostika/clockify")
@login_required
def diagnostika_clockify():
    """Ukáže, co Clockify reálně vrací — pro doladění párování zakázek.
    Otevři /diagnostika/clockify po přihlášení."""
    rok = datetime.now(timezone.utc).year
    od = f"{rok}-01-01T00:00:00Z"
    do = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return jsonify(clockify.diagnostika(od, do))


@bp.route("/diagnostika/merk")
@login_required
def diagnostika_merk():
    """Test MERK napojení. Použij ?ico=...&nazev=... pro konkrétní test."""
    return jsonify(firmy_service.merk_diagnostika(
        request.args.get("ico", ""), request.args.get("nazev", "")))


@bp.route("/obnovit", methods=["POST"])
@zakazky_required
def obnovit():
    """Ruční obnova dat z Clockify i Freela (snapshoty)."""
    ok_c, info_c = snapshot.obnov()
    ok_f, info_f = snapshot_freelo.obnov()
    if ok_c and ok_f:
        flash("Data z Clockify i Freela obnovena.", "info")
    elif ok_c:
        flash(f"Clockify obnoveno; Freelo chyba: {info_f}", "error")
    elif ok_f:
        flash(f"Freelo obnoveno; Clockify chyba: {info_c}", "error")
    else:
        flash(f"Chyba obnovy — Clockify: {info_c}; Freelo: {info_f}", "error")
    return redirect(request.referrer or url_for("main.prehled"))


@bp.route("/cron/obnovit")
def cron_obnovit():
    """Denní automatická obnova — chráněno tokenem CRON_KEY (?key=...)."""
    from ..extensions import CRON_KEY
    if not CRON_KEY or request.args.get("key") != CRON_KEY:
        return ("Neautorizováno", 403)
    ok_c, info_c = snapshot.obnov()
    ok_f, info_f = snapshot_freelo.obnov()
    return (f"OK clockify={info_c} freelo={info_f}", 200) if (ok_c and ok_f) \
        else (f"CHYBA clockify={info_c} freelo={info_f}", 500)


def _vyhodnot_riziko(z):
    """Vrátí (stav_indikatoru, popis) podle čerpání hodin vs rozpočet."""
    rozpocet = z.rozpocet_hodin_efekt
    if not rozpocet:
        return ("neutral", "Bez rozpočtu hodin")
    pct = round(100 * (z.hodiny_bill or 0) / rozpocet)  # jen fakturovatelné
    if pct >= 100:
        return ("cervena", f"Přečerpáno ({pct} %)")
    if pct >= 85:
        return ("oranzova", f"Blízko rozpočtu ({pct} %)")
    return ("zelena", f"V rozpočtu ({pct} %)")


@bp.route("/")
@login_required
def prehled():
    """Celofiremní přehledový dashboard (úvodní stránka) — čte ze snapshotu."""
    now = datetime.now(timezone.utc)
    snap, updated = snapshot.nacti()
    hod = snap.get("hodiny", {})
    zakazky = _bez_internich(Zakazka.query.join(Firma)
               .filter(Zakazka.aktivni.is_(True), Firma.aktivni.is_(True))).all()
    mesice_rok = [f"{now.year}-{m:02d}" for m in range(1, now.month + 1)]
    sazby = {z.zkratka: z.efekt_sazba for z in zakazky}

    for z in zakazky:
        _, z.hodiny_bill = snapshot.hodiny_pro_zakazku(snap, z, mesice_rok)
        z.hodiny = z.hodiny_bill
        z._pocet_mesicu = sum(1 for m in range(1, now.month + 1)
                              if not z.datum_od or (now.year, m) >= (z.datum_od.year, z.datum_od.month))
        z._tydny = _tydny_obdobi(z, [(now.year, mm) for mm in range(1, now.month + 1)])
    _napln_faktury(zakazky, mesice_rok)

    aktivni_zkr = {z.zkratka for z in zakazky}
    bill_mesic, trzby_mesic = [], []
    for mm in mesice_rok:
        bill_mesic.append(round(sum(hod.get(zk, {}).get(mm, [0, 0])[1] for zk in aktivni_zkr), 1))
        trzby_mesic.append(round(sum(hod.get(zk, {}).get(mm, [0, 0])[1] * sazby.get(zk, 0) for zk in aktivni_zkr)))

    kpi = {
        "trzby": round(sum(z.trzba_skutecnost for z in zakazky)),
        "hodin_bill": round(sum(z.hodiny_bill for z in zakazky), 1),
        "potencial": round(sum(z.nenaplneny_potencial for z in zakazky)),
        "zakazek": len(zakazky),
        "klientu": len({z.firma_id for z in zakazky}),
        "clockify_ok": clockify.je_nakonfigurovano(),
    }
    rozpad = {}
    for z in zakazky:
        rozpad[z.typ_sluzby or "—"] = rozpad.get(z.typ_sluzby or "—", 0) + z.trzba_skutecnost
    rozpad = sorted([(t, round(v)) for t, v in rozpad.items() if v], key=lambda x: -x[1])
    klienti = {}
    for z in zakazky:
        klienti[z.firma.nazev] = klienti.get(z.firma.nazev, 0) + z.trzba_skutecnost
    top_klienti = sorted([(n, round(v)) for n, v in klienti.items() if v], key=lambda x: -x[1])[:8]

    graf = {"labels": [MESICE_CZ[int(mm[5:7])][:3] for mm in mesice_rok],
            "bill": bill_mesic, "trzby": trzby_mesic}

    # ── Alerty: co vyžaduje pozornost (bez financí, viditelné všem) ──
    today = now.date()
    posl2 = mesice_rok[-2:]  # poslední dva měsíce
    alerty = []
    for z in zakazky:
        # 1) konec zakázky — blíží se / uplynul (běží dál)
        if z.datum_do:
            dnu = (z.datum_do - today).days
            if dnu < 0:
                alerty.append({"barva": "cervena", "firma_id": z.firma_id, "zakazka": z.nazev,
                               "popis": f"Termín uplynul ({z.datum_do.strftime('%-d. %-m. %Y')}) — protáhnout nebo uzavřít"})
            elif dnu <= 30:
                alerty.append({"barva": "oranzova", "firma_id": z.firma_id, "zakazka": z.nazev,
                               "popis": f"Blíží se konec: {z.datum_do.strftime('%-d. %-m. %Y')}"})
        # 2) přečerpané hodiny vs rozpočet
        rozp = z.rozpocet_hodin_efekt
        if rozp and z.hodiny_bill >= rozp:
            alerty.append({"barva": "cervena", "firma_id": z.firma_id, "zakazka": z.nazev,
                           "popis": f"Přečerpané hodiny ({round(z.hodiny_bill)} / {round(rozp)} h)"})
    # 3) Nečinnost klienta — aktivní klient bez záznamu v Clockify > 14 dní (z poslední aktivity)
    posledni = snap.get("posledni", {})
    fdata = {}
    for z in zakazky:
        e = fdata.setdefault(z.firma_id, {"nazev": z.firma.nazev, "id": z.firma_id, "last": None})
        d = posledni.get(z.zkratka)
        if d and (e["last"] is None or d > e["last"]):
            e["last"] = d
    for e in fdata.values():
        if e["last"]:
            dni = (today - date.fromisoformat(e["last"])).days
            if dni > 14:
                alerty.append({"barva": "cervena", "firma_id": e["id"], "zakazka": e["nazev"],
                               "popis": f"Klient bez aktivity {dni} dní (poslední práce {e['last']})"})
    poradi = {"cervena": 0, "oranzova": 1, "neutral": 2}
    alerty.sort(key=lambda a: poradi.get(a["barva"], 3))

    return render_template("prehled.html", kpi=kpi, graf=graf, rozpad=rozpad,
                           top_klienti=top_klienti, rok=now.year, updated=updated, alerty=alerty)


@bp.route("/zakazky")
@login_required
def dashboard():
    now = datetime.now(timezone.utc)
    # Filtry
    f_aktivita = request.args.get("aktivita", "aktivni")  # výchozí: jen aktivní
    f_typy = request.args.getlist("typ")        # více typů služeb
    f_mesice = request.args.getlist("mesic")    # více měsíců nebo ['vse']
    hledat = request.args.get("q", "").strip()

    # Měsíce → seznam (rok, měsíc). Výchozí = aktuální měsíc.
    if not f_mesice:
        f_mesice = [now.strftime("%Y-%m")]
    if "vse" in f_mesice:
        months = [(now.year, m) for m in range(1, now.month + 1)]
        obdobi_popis = f"rok {now.year}"
    else:
        months = []
        for s in f_mesice:
            try:
                r, m = (int(x) for x in s.split("-"))
                months.append((r, m))
            except ValueError:
                pass
        if not months:
            months = [(now.year, now.month)]
        obdobi_popis = ", ".join(f"{MESICE_CZ[m]} {r}" for r, m in sorted(months))
    mesic_keys = [f"{r}-{m:02d}" for r, m in months]
    snap, updated = snapshot.nacti()

    q = _bez_internich(Zakazka.query.join(Firma))
    if f_aktivita == "aktivni":   # aktivní zakázka i aktivní klient
        q = q.filter(Zakazka.aktivni.is_(True), Firma.aktivni.is_(True))
    elif f_aktivita == "neaktivni":  # zakázka NEBO klient neaktivní
        q = q.filter(db.or_(Zakazka.aktivni.is_(False), Firma.aktivni.is_(False)))
    if f_typy:
        q = q.filter(Zakazka.typ_sluzby.in_(f_typy))
    if hledat:
        like = f"%{hledat}%"
        q = q.filter(db.or_(Zakazka.nazev.ilike(like), Zakazka.zkratka.ilike(like)))
    zakazky = q.order_by(Zakazka.nazev).all()

    def _aktivni_mesic(r, m, od_d, do_d):
        if od_d and (r, m) < (od_d.year, od_d.month):
            return False
        end = do_d or now.date()
        if (r, m) > (end.year, end.month):
            return False
        return True
    for z in zakazky:
        tot, bill = snapshot.hodiny_pro_zakazku(snap, z, mesic_keys)
        z.hodiny, z.hodiny_bill = tot, bill
        z.hodiny_nonbill = round(max(tot - bill, 0), 1)
        # měsíční rozpočet = počet aktivních měsíců projektu (od datum_od) ve zvoleném období
        z._pocet_mesicu = sum(1 for (r, m) in months if _aktivni_mesic(r, m, z.datum_od, z.datum_do))
        z._tydny = _tydny_obdobi(z, months)
    _napln_faktury(zakazky, mesic_keys)
    for z in zakazky:
        z.riziko, z.riziko_popis = _vyhodnot_riziko(z)

    typy = sorted({z.typ_sluzby for z in Zakazka.query.all() if z.typ_sluzby})
    souhrn = {
        "pocet": len(zakazky),
        "celkem_hodin": round(sum(z.hodiny for z in zakazky), 1),
        "celkem_bill": round(sum(z.hodiny_bill for z in zakazky), 1),
        "celkem_nonbill": round(sum(z.hodiny_nonbill for z in zakazky), 1),
        "klientske_hodin": round(sum(z.hodiny for z in zakazky if not z.je_interni), 1),
        "interni_hodin": round(sum(z.hodiny for z in zakazky if z.je_interni), 1),
        "trzby_skutecnost": round(sum(z.trzba_skutecnost for z in zakazky)),
        "potencial": round(sum(z.nenaplneny_potencial for z in zakazky)),
        "pocet_firem": len({z.firma_id for z in zakazky}),
        "clockify_ok": clockify.je_nakonfigurovano(),
    }
    return render_template("stav_zakazek.html", zakazky=zakazky, souhrn=souhrn,
                           typy=typy, f_aktivita=f_aktivita, f_typy=f_typy, hledat=hledat,
                           mesice=_seznam_mesicu(), f_mesice=f_mesice, obdobi_popis=obdobi_popis,
                           updated=updated)


@bp.route("/interni")
@login_required
def interni():
    """Interní hodiny (projekty pod IČO Commarecu) — samostatná karta."""
    now = datetime.now(timezone.utc)
    snap, updated = snapshot.nacti()
    hod = snap.get("hodiny", {})
    mesice_rok = [f"{now.year}-{m:02d}" for m in range(1, now.month + 1)]
    zakazky = _jen_interni(Zakazka.query.join(Firma).filter(Zakazka.aktivni.is_(True))).all()
    for z in zakazky:
        tot, bill = snapshot.hodiny_zkr(snap, z.zkratka, mesice_rok)
        z.hodiny, z.hodiny_bill = tot, bill
    zkr_set = {z.zkratka for z in zakazky}
    serie = [round(sum(hod.get(zk, {}).get(mm, [0, 0])[0] for zk in zkr_set), 1) for mm in mesice_rok]
    graf = {"labels": [MESICE_CZ[int(mm[5:7])][:3] for mm in mesice_rok], "hodiny": serie}
    uzivatele = snapshot.uzivatele_zkr(snap, zkr_set)
    celkem = round(sum(z.hodiny for z in zakazky), 1)
    zakazky.sort(key=lambda z: -z.hodiny)
    return render_template("interni.html", zakazky=zakazky, graf=graf, uzivatele=uzivatele,
                           celkem=celkem, rok=now.year, updated=updated)


@bp.route("/pm")
@finance_required
def pm_prehled():
    """Přehled podle projektových manažerů — kdo co táhne (hodiny, fakturováno, potenciál)."""
    now = datetime.now(timezone.utc)
    snap, updated = snapshot.nacti()
    hod = snap.get("hodiny", {})
    mesice_rok = [f"{now.year}-{m:02d}" for m in range(1, now.month + 1)]
    f_aktivita = request.args.get("aktivita", "aktivni")
    q = _bez_internich(Zakazka.query.join(Firma))
    if f_aktivita == "aktivni":
        q = q.filter(Zakazka.aktivni.is_(True), Firma.aktivni.is_(True))
    elif f_aktivita == "neaktivni":
        q = q.filter(db.or_(Zakazka.aktivni.is_(False), Firma.aktivni.is_(False)))
    zakazky = q.all()
    pm = {}
    for z in zakazky:
        _, z.hodiny_bill = snapshot.hodiny_pro_zakazku(snap, z, mesice_rok)
        z.hodiny = z.hodiny_bill
        fakt, pot, plan = _fin_mesicne(z, hod, now.month, now.year)
        key = z.efekt_pm or "— bez PM —"
        d = pm.setdefault(key, {"pm": key, "zakazek": 0, "hodin": 0, "trzby": 0, "potencial": 0, "plan": 0})
        d["zakazek"] += 1
        d["hodin"] += z.hodiny_bill
        d["trzby"] += fakt
        d["potencial"] += pot
        d["plan"] += plan
    radky = sorted(pm.values(), key=lambda x: -x["plan"])
    for r in radky:
        r["hodin"] = round(r["hodin"], 1)
        r["trzby"] = round(r["trzby"])
        r["potencial"] = round(r["potencial"])
        r["plan"] = round(r["plan"])
        r["naplneni"] = round(100 * r["trzby"] / r["plan"]) if r["plan"] else 0
    graf = {"labels": [r["pm"] for r in radky],
            "trzby": [r["trzby"] for r in radky],
            "potencial": [r["potencial"] for r in radky]}
    return render_template("pm.html", radky=radky, graf=graf, rok=now.year, updated=updated,
                           f_aktivita=f_aktivita)


@bp.route("/pm/<path:jmeno>")
@finance_required
def pm_detail(jmeno):
    """Profil projektového manažera — jeho zakázky, klienti, plán/fakturováno/potenciál."""
    now = datetime.now(timezone.utc)
    snap, updated = snapshot.nacti()
    hod = snap.get("hodiny", {})
    mesice_rok = [f"{now.year}-{m:02d}" for m in range(1, now.month + 1)]
    f_aktivita = request.args.get("aktivita", "aktivni")
    q = _bez_internich(Zakazka.query.join(Firma))
    if f_aktivita == "aktivni":
        q = q.filter(Zakazka.aktivni.is_(True), Firma.aktivni.is_(True))
    elif f_aktivita == "neaktivni":
        q = q.filter(db.or_(Zakazka.aktivni.is_(False), Firma.aktivni.is_(False)))
    vsechny = q.all()
    if jmeno == "— bez PM —":
        zakazky = [z for z in vsechny if not z.efekt_pm]
    else:
        zakazky = [z for z in vsechny if z.efekt_pm == jmeno]

    for z in zakazky:
        _, z.hodiny_bill = snapshot.hodiny_pro_zakazku(snap, z, mesice_rok)
        z.hodiny = z.hodiny_bill
        z._pocet_mesicu = sum(1 for m in range(1, now.month + 1)
                              if not z.datum_od or (now.year, m) >= (z.datum_od.year, z.datum_od.month))
        z._tydny = _tydny_obdobi(z, [(now.year, mm) for mm in range(1, now.month + 1)])
        z._fakt, z._pot, z._plan = _fin_mesicne(z, hod, now.month, now.year)
        z.riziko, z.riziko_popis = _vyhodnot_riziko(z)
    zakazky.sort(key=lambda z: -z._plan)

    # Měsíční stacked vč. budoucnosti
    graf = _fin_serie(zakazky, hod, _okno_mesicu(now), (now.year, now.month))
    kpi = {"zakazek": len(zakazky), "klientu": len({z.firma_id for z in zakazky}),
           "hodin": round(sum(z.hodiny_bill for z in zakazky), 1),
           "plan": sum(z._plan for z in zakazky),
           "trzby": sum(graf["fakt"]), "potencial": sum(graf["pot"])}
    return render_template("pm_detail.html", jmeno=jmeno, zakazky=zakazky, graf=graf,
                           kpi=kpi, rok=now.year, updated=updated, f_aktivita=f_aktivita)


@bp.route("/cashflow")
@finance_required
def cashflow():
    """Cashflow: historie (skutečně fakturováno + potenciál), aktuální měsíc, výhled (plán)."""
    now = datetime.now(timezone.utc)
    snap, updated = snapshot.nacti()
    hod = snap.get("hodiny", {})
    # Cashflow počítá i s neaktivními projekty (historie fakturace patří do cashflow)
    zakazky = _bez_internich(Zakazka.query.join(Firma)).all()
    teď = (now.year, now.month)

    def _add(y, m, delta):
        idx = y * 12 + (m - 1) + delta
        return idx // 12, idx % 12 + 1

    # Okno: 6 měsíců zpět … aktuální … 6 měsíců vpřed
    okno = [_add(now.year, now.month, d) for d in range(-6, 7)]

    bez_terminu = []
    for z in zakazky:
        if z.typ_rozpoctu in ("projektovy", "analyza") and (z.budget_castka or z.rozpocet_hodin) and not (z.datum_od and z.datum_do):
            bez_terminu.append(z.nazev)

    radky = []
    for ym in okno:
        y, m = ym
        key = f"{y}-{m:02d}"
        je_minulost_nebo_ted = ym <= teď
        plan = sum(_plan_mesic(z, ym) for z in zakazky)
        # skutečně fakturováno = fakturovatelné hodiny × efektivní sazba (jen minulost + aktuální měsíc)
        fakt = 0
        if je_minulost_nebo_ted:
            fakt = sum(hod.get(z.zkratka, {}).get(key, [0, 0])[1] * z.efekt_sazba for z in zakazky)
            # jednorázové faktury (nárazový prodej) v daném měsíci
            fakt += sum(f.castka for z in zakazky for f in z.faktury
                        if f.datum and f.datum.strftime("%Y-%m") == key)
        potencial = max(plan - fakt, 0) if je_minulost_nebo_ted else 0
        radky.append({
            "label": f"{MESICE_CZ[m]} {y}", "kratky": f"{MESICE_ZKR[m]} {str(y)[2:]}",
            "obdobi": "minulost" if ym < teď else ("ted" if ym == teď else "vyhled"),
            "plan": round(plan), "fakt": round(fakt), "potencial": round(potencial),
        })

    kpi = {
        "fakt_minulost": round(sum(r["fakt"] for r in radky if r["obdobi"] != "vyhled")),
        "vyhled": round(sum(r["plan"] for r in radky if r["obdobi"] == "vyhled")),
        "potencial": round(sum(r["potencial"] for r in radky)),
    }
    # Graf: minulost = fakturováno (zelená) + nenaplněný potenciál (červená);
    #       budoucnost = plán, kolik můžeme udělat (modrá)
    graf = {
        "labels": [r["kratky"] for r in radky],
        "fakt": [r["fakt"] if r["obdobi"] != "vyhled" else 0 for r in radky],
        "pot": [r["potencial"] if r["obdobi"] != "vyhled" else 0 for r in radky],
        "budouci": [r["plan"] if r["obdobi"] == "vyhled" else 0 for r in radky],
    }
    return render_template("cashflow.html", radky=radky, graf=graf, kpi=kpi,
                           bez_terminu=bez_terminu, updated=updated)


# ─── Operativa (klient: úkoly, dokumenty, zápisy, LOE) ─────────────
@bp.route("/operativa")
@login_required
def operativa():
    hledat = request.args.get("q", "").strip()
    f_aktivita = request.args.get("aktivita", "aktivni")
    q = _bez_internich(Firma.query)
    if f_aktivita == "aktivni":
        q = q.filter(Firma.aktivni.is_(True))
    elif f_aktivita == "neaktivni":
        q = q.filter(Firma.aktivni.is_(False))
    if hledat:
        q = q.filter(Firma.nazev.ilike(f"%{hledat}%"))
    seznam = q.order_by(Firma.nazev).all()

    # Souhrny pro dlaždice (ze snapshotu) + počet dokumentů v DB na klienta
    souhrny = {}
    snap_data, _ = snapshot_freelo.nacti()
    if snap_data.get("tasklisty"):
        for f in seznam:
            if f.freelo_tasklist_id:
                s = freelo_service.souhrn_tasklistu(f.freelo_tasklist_id, jen_snapshot=True)
                if s["open"] or s["hotovo"]:
                    souhrny[f.id] = s
    from ..models import KlientDokument
    from sqlalchemy import func
    db_pocet = dict(db.session.query(KlientDokument.firma_id, func.count(KlientDokument.id))
                    .group_by(KlientDokument.firma_id).all())
    return render_template("operativa.html", firmy=seznam, hledat=hledat, f_aktivita=f_aktivita,
                           souhrny=souhrny, db_pocet=db_pocet,
                           freelo_ok=freelo_service.je_nakonfigurovano())


@bp.route("/operativa/freelo")
@login_required
def operativa_freelo():
    souhrny, top_overdue = {}, []
    dash = {"open": 0, "bez_reakce": 0, "po_terminu": 0, "max_zpozdeni": 0, "napojeno": 0}
    snap_data, fre_updated = snapshot_freelo.nacti()
    snapshot_chybi = freelo_service.je_nakonfigurovano() and not (snap_data.get("tasklisty"))
    if snap_data.get("tasklisty"):
        napojene = _bez_internich(Firma.query).filter(Firma.freelo_tasklist_id.isnot(None)).all()
        for f in napojene:
            s = freelo_service.souhrn_tasklistu(f.freelo_tasklist_id, jen_snapshot=True)
            if not s["open"] and not s["hotovo"]:
                continue
            souhrny[f.id] = s
            dash["open"] += s["open"]
            dash["bez_reakce"] += s["bez_reakce"]
            dash["po_terminu"] += s["po_terminu"]
            dash["max_zpozdeni"] = max(dash["max_zpozdeni"], s["max_zpozdeni"])
            dash["napojeno"] += 1
            for ot in s["overdue_tasks"]:
                top_overdue.append({**ot, "firma": f.nazev, "firma_id": f.id})
        top_overdue.sort(key=lambda x: -x["zpozdeni"])
        top_overdue = top_overdue[:12]
    return render_template("operativa_freelo.html", dash=dash, top_overdue=top_overdue,
                           freelo_ok=freelo_service.je_nakonfigurovano(),
                           snapshot_chybi=snapshot_chybi, fre_updated=fre_updated)


@bp.route("/operativa/resitele")
@login_required
def operativa_resitele():
    if not freelo_service.je_nakonfigurovano():
        flash("Freelo není nakonfigurované.", "error")
        return redirect(url_for("main.operativa"))
    napojene = _bez_internich(Firma.query).filter(Firma.freelo_tasklist_id.isnot(None)).all()
    lide = freelo_service.prehled_resitelu([(f.id, f.nazev, f.freelo_tasklist_id) for f in napojene])
    radky = sorted(lide.values(), key=lambda x: (-x["po_terminu"], -x["open"]))
    return render_template("operativa_resitele.html", radky=radky)


@bp.route("/operativa/resitel/<path:jmeno>")
@login_required
def operativa_resitel(jmeno):
    if not freelo_service.je_nakonfigurovano():
        flash("Freelo není nakonfigurované.", "error")
        return redirect(url_for("main.operativa"))
    napojene = _bez_internich(Firma.query).filter(Firma.freelo_tasklist_id.isnot(None)).all()
    lide = freelo_service.prehled_resitelu([(f.id, f.nazev, f.freelo_tasklist_id) for f in napojene])
    osoba = lide.get(jmeno)
    if not osoba:
        flash("Řešitel nenalezen nebo nemá otevřené úkoly.", "info")
        return redirect(url_for("main.operativa_resitele"))
    ukoly = sorted(osoba["ukoly"], key=lambda x: -x["zpozdeni"])
    return render_template("operativa_resitel.html", osoba=osoba, ukoly=ukoly)


@bp.route("/operativa/<int:id>")
@login_required
def operativa_klient(id):
    firma = Firma.query.get_or_404(id)
    ukoly = freelo_service.ukoly_klienta(firma.freelo_tasklist_id)
    projekty = freelo_service.projekty_s_tasklisty() if not firma.freelo_tasklist_id else []
    # Dokumenty z OneDrive (pokud je napojeno a klient má odkaz)
    dok = None
    if onedrive_service.je_nakonfigurovano() and firma.onedrive_odkaz:
        dok = onedrive_service.vypis_slozky_klienta(firma.onedrive_odkaz, request.args.get("slozka"))
    idx_pocet, idx_updated = dokumenty_service.stav(firma.id)
    nalezeno = dokumenty_service.hledej(request.args.get("hledat_dok", ""), firma.id) \
        if request.args.get("hledat_dok") else None
    souhrn = freelo_service.souhrn_tasklistu(firma.freelo_tasklist_id) if firma.freelo_tasklist_id else None
    return render_template("operativa_klient.html", firma=firma, ukoly=ukoly,
                           projekty=projekty, freelo_ok=freelo_service.je_nakonfigurovano(),
                           onedrive_ok=onedrive_service.je_nakonfigurovano(), dok=dok,
                           podslozka=request.args.get("slozka"),
                           idx_pocet=idx_pocet, idx_updated=idx_updated,
                           hledat_dok=request.args.get("hledat_dok", ""), nalezeno=nalezeno,
                           ai_ok=ai_service.ma_ai(), souhrn=souhrn)


@bp.route("/operativa/<int:id>/chat", methods=["POST"])
@login_required
def operativa_chat(id):
    firma = Firma.query.get_or_404(id)
    dotaz = (request.get_json(silent=True) or {}).get("dotaz", "")
    vysledek = ai_service.odpoved_na_dotaz(firma, dotaz)
    return jsonify(vysledek)


@bp.route("/operativa/<int:id>/index-dokumenty", methods=["POST"])
@login_required
def operativa_index_dokumenty(id):
    import threading
    firma = Firma.query.get_or_404(id)
    if not firma.onedrive_odkaz:
        flash("Klient nemá napojenou OneDrive složku.", "error")
        return redirect(url_for("main.operativa_klient", id=id))
    if firma.dok_index_bezi:
        flash("Indexace už probíhá — za chvíli obnov stránku.", "info")
        return redirect(url_for("main.operativa_klient", id=id))
    app_obj = current_app._get_current_object()
    threading.Thread(target=dokumenty_service.index_klienta_async,
                     args=(app_obj, firma.id), daemon=True).start()
    flash("Indexace spuštěna na pozadí. Počet načtených dokumentů poroste — stránka se sama obnovuje.", "info")
    return redirect(url_for("main.operativa_klient", id=id))


@bp.route("/operativa/<int:id>/index-reset", methods=["POST"])
@login_required
def operativa_index_reset(id):
    firma = Firma.query.get_or_404(id)
    firma.dok_index_bezi = False
    firma.dok_index_progress = None
    db.session.commit()
    flash("Stav indexace resetován. Můžeš spustit znovu.", "info")
    return redirect(url_for("main.operativa_klient", id=id))


@bp.route("/operativa/dokumenty")
@login_required
def operativa_dokumenty():
    dotaz = request.args.get("q", "").strip()
    vysledky = dokumenty_service.hledej(dotaz) if dotaz else None
    return render_template("operativa_dokumenty.html", dotaz=dotaz, vysledky=vysledky)


@bp.route("/operativa/<int:id>/onedrive", methods=["POST"])
@klient_required
def operativa_onedrive(id):
    firma = Firma.query.get_or_404(id)
    firma.onedrive_odkaz = request.form.get("odkaz", "").strip() or None
    db.session.commit()
    flash("Odkaz na OneDrive uložen." if firma.onedrive_odkaz else "Odkaz odebrán.", "info")
    return redirect(url_for("main.operativa_klient", id=id))


@bp.route("/diagnostika/onedrive")
@login_required
def diagnostika_onedrive():
    return jsonify(onedrive_service.diagnostika(request.args.get("odkaz", "")))


@bp.route("/operativa/<int:id>/freelo", methods=["POST"])
@klient_required
def operativa_freelo_napojit(id):
    firma = Firma.query.get_or_404(id)
    tl = request.form.get("tasklist_id", "").strip()
    firma.freelo_tasklist_id = int(tl) if tl.isdigit() else None
    db.session.commit()
    flash("Freelo tasklist napojen." if firma.freelo_tasklist_id else "Napojení zrušeno.", "info")
    return redirect(url_for("main.operativa_klient", id=id))


@bp.route("/operativa/ukol/<int:task_id>")
@login_required
def ukol_detail(task_id):
    detail = freelo_service.ukol_detail(task_id)
    if not detail:
        flash("Úkol se nepodařilo načíst z Freelo.", "error")
        return redirect(url_for("main.operativa"))
    firma = Firma.query.filter_by(freelo_tasklist_id=detail.get("tasklist_id")).first()
    reseni = freelo_service.workers(detail.get("project_id"))
    podukoly = freelo_service.subtasks(task_id)
    return render_template("ukol_detail.html", u=detail, firma=firma, reseni=reseni,
                           komentare=detail.get("komentare", []), podukoly=podukoly)


def _moje_freelo():
    """(email, key) přihlášeného uživatele pro zápisy pod jeho autorstvím; jinak None (sdílený klíč)."""
    u = User.query.get(session.get("user_id"))
    if u and u.freelo_email and u.freelo_api_key:
        return (u.freelo_email, u.freelo_api_key)
    return None


@bp.route("/operativa/ukol/<int:task_id>/prirad", methods=["POST"])
@login_required
def ukol_prirad(task_id):
    ok = freelo_service.priradit(task_id, request.form.get("worker_id", ""), auth=_moje_freelo())
    flash("Řešitel přiřazen." if ok else "Přiřazení se nepovedlo (ověř Freelo).", "info" if ok else "error")
    return redirect(url_for("main.ukol_detail", task_id=task_id))


@bp.route("/operativa/ukol/<int:task_id>/dokoncit", methods=["POST"])
@login_required
def ukol_dokoncit(task_id):
    ok = freelo_service.dokoncit(task_id, auth=_moje_freelo())
    flash("Úkol označen jako hotový." if ok else "Úkol se nepovedlo uzavřít.", "info" if ok else "error")
    return redirect(url_for("main.ukol_detail", task_id=task_id))


@bp.route("/operativa/ukol/<int:task_id>/otevrit", methods=["POST"])
@login_required
def ukol_otevrit(task_id):
    ok = freelo_service.znovu_otevrit(task_id, auth=_moje_freelo())
    flash("Úkol znovu otevřen." if ok else "Úkol se nepovedlo otevřít.", "info" if ok else "error")
    return redirect(url_for("main.ukol_detail", task_id=task_id))


@bp.route("/operativa/ukol/<int:task_id>/komentar", methods=["POST"])
@login_required
def ukol_komentar(task_id):
    text = request.form.get("text", "").strip()
    if text:
        ok = freelo_service.pridej_komentar(task_id, text, auth=_moje_freelo())
        flash("Komentář přidán." if ok else "Komentář se nepovedl.", "info" if ok else "error")
    return redirect(url_for("main.ukol_detail", task_id=task_id))


@bp.route("/muj-ucet", methods=["GET", "POST"])
@login_required
def muj_ucet():
    u = User.query.get(session["user_id"])
    if request.method == "POST":
        u.freelo_email = request.form.get("freelo_email", "").strip() or None
        novy = request.form.get("freelo_api_key", "").strip()
        if request.form.get("smazat_klic"):
            u.freelo_api_key = None
        elif novy:
            u.freelo_api_key = novy
        db.session.commit()
        flash("Uloženo.", "info")
        return redirect(url_for("main.muj_ucet"))
    return render_template("muj_ucet.html", u=u)


@bp.route("/diagnostika/freelo")
@login_required
def diagnostika_freelo():
    return jsonify(freelo_service.diagnostika(request.args.get("tasklist", ""), request.args.get("task", "")))


# ─── Firmy (databáze klientů + MERK/ARES) ──────────────────────────
@bp.route("/firmy")
@login_required
def firmy():
    hledat = request.args.get("q", "").strip()
    f_typ = request.args.get("typ_subjektu", "")
    q = Firma.query
    if hledat:
        q = q.filter(db.or_(Firma.nazev.ilike(f"%{hledat}%"), Firma.ico.ilike(f"%{hledat}%")))
    if f_typ:
        q = q.filter(Firma.typ_subjektu == f_typ)
    seznam = q.order_by(Firma.nazev).all()
    return render_template("firmy.html", firmy=seznam, hledat=hledat, f_typ=f_typ)


@bp.route("/firmy/nova", methods=["POST"])
@klient_required
def firma_nova():
    nazev = request.form.get("nazev", "").strip()
    if not nazev:
        flash("Vyplň název firmy.", "error")
        return redirect(url_for("main.firmy"))
    if Firma.query.filter_by(nazev=nazev).first():
        flash("Firma s tímto názvem už existuje.", "error")
        return redirect(url_for("main.firmy"))
    f = Firma(nazev=nazev, ico=request.form.get("ico", "").strip() or None,
              typ_subjektu=request.form.get("typ_subjektu", "klient"))
    db.session.add(f)
    db.session.commit()
    flash("Firma přidána. Načti data z MERK na jejím detailu.", "info")
    return redirect(url_for("main.firma_detail", id=f.id))


@bp.route("/firmy/<int:id>/zakazka/nova", methods=["POST"])
@zakazky_required
def zakazka_nova(id):
    firma = Firma.query.get_or_404(id)
    nazev = request.form.get("nazev", "").strip()
    typ_sluzby = request.form.get("typ_sluzby", "").strip()
    if not nazev:
        flash("Vyplň název zakázky.", "error")
        return redirect(url_for("main.firma_detail", id=id))
    # zkratka = IČO_pořadí (další volné číslo)
    ico = firma.ico or "0"
    cisla = [int(z.zkratka.split("_")[1]) for z in firma.zakazky
             if "_" in z.zkratka and z.zkratka.split("_")[1].isdigit()]
    poradi = (max(cisla) + 1) if cisla else 1
    db.session.add(Zakazka(zkratka=f"{ico}_{poradi}", nazev=nazev, typ_sluzby=typ_sluzby,
                           firma_id=id, aktivni=True, typ_rozpoctu="projektovy"))
    db.session.commit()
    flash("Zakázka přidána.", "info")
    return redirect(url_for("main.firma_detail", id=id))


@bp.route("/firmy/<int:id>")
@login_required
def firma_detail(id):
    firma = Firma.query.get_or_404(id)
    now = datetime.now(timezone.utc)
    snap, updated = snapshot.nacti()
    hod = snap.get("hodiny", {})
    mesice_rok = [f"{now.year}-{m:02d}" for m in range(1, now.month + 1)]

    # Per-zakázka souhrn (fixní projektový = kumulativně od začátku, jinak YTD)
    for z in firma.zakazky:
        tot, bill = snapshot.hodiny_pro_zakazku(snap, z, mesice_rok)
        z.hodiny, z.hodiny_bill = tot, bill
        z.hodiny_nonbill = round(max(tot - bill, 0), 1)
        z._pocet_mesicu = sum(1 for m in range(1, now.month + 1)
                              if not z.datum_od or (now.year, m) >= (z.datum_od.year, z.datum_od.month))
        z._tydny = _tydny_obdobi(z, [(now.year, mm) for mm in range(1, now.month + 1)])
    _napln_faktury(firma.zakazky, mesice_rok, vse=True)

    # Graf, KPI a hodiny lidí = aktivní zakázky; u neaktivního klienta (žádná aktivní)
    # ukážeme celou historii (všechny zakázky), ať se hodiny i tržby zobrazí.
    aktivni = [z for z in firma.zakazky if z.je_aktivni] or list(firma.zakazky)
    aktivni_zkr = {z.zkratka for z in aktivni}

    def _akt_v_mesici(z, m):
        if z.datum_od and (now.year, m) < (z.datum_od.year, z.datum_od.month):
            return False
        if z.datum_do and (now.year, m) > (z.datum_do.year, z.datum_do.month):
            return False
        return True
    serie_bill, serie_tot, rozpocet_mesic = [], [], []
    for m in range(1, now.month + 1):
        mm = f"{now.year}-{m:02d}"
        serie_bill.append(round(sum(hod.get(zk, {}).get(mm, [0, 0])[1] for zk in aktivni_zkr), 1))
        serie_tot.append(round(sum(hod.get(zk, {}).get(mm, [0, 0])[0] for zk in aktivni_zkr), 1))
        rozpocet_mesic.append(round(sum((z.rozpocet_hodin_tyden or 0) * _tydny_v_mesici(z, now.year, m)
                                        for z in aktivni if z.typ_rozpoctu == "mesicni")))
    graf = {"labels": [MESICE_CZ[m][:3] for m in range(1, now.month + 1)],
            "celkem": serie_tot, "bill": serie_bill, "rozpocet": rozpocet_mesic}
    kpi = {"pocet": len(aktivni),
           "hodin_bill": round(sum(z.hodiny_bill for z in aktivni), 1),
           "rozpocet_h": round(sum(rozpocet_mesic)),
           "plan": round(sum(z.trzba_plan for z in aktivni)),
           "trzby": round(sum(z.trzba_skutecnost for z in aktivni)),
           "potencial": round(sum(z.nenaplneny_potencial for z in aktivni))}
    uzivatele = snapshot.uzivatele_zkr(snap, aktivni_zkr)
    pm_jmena = {z.efekt_pm for z in aktivni if z.efekt_pm}

    # Finanční stacked graf vč. budoucnosti (fakturováno + potenciál + budoucí plán)
    graf_fin = _fin_serie(aktivni, hod, _okno_mesicu(now), (now.year, now.month))
    # KPI sladit s grafem (minulý potenciál po měsících)
    kpi["trzby"] = sum(graf_fin["fakt"])
    kpi["potencial"] = sum(graf_fin["pot"])
    return render_template("firma_detail.html", firma=firma, graf=graf, graf_fin=graf_fin, kpi=kpi,
                           rok=now.year, uzivatele=uzivatele, pm_jmena=pm_jmena, updated=updated)


@bp.route("/firmy/<int:id>/nacist", methods=["POST"])
@klient_required
def firma_nacist(id):
    firma = Firma.query.get_or_404(id)
    ok, zdroj = firmy_service.obohat_firmu(firma)
    flash(f"Načteno z {zdroj}." if ok else "Nepodařilo se načíst data (zkontroluj IČO / MERK klíč).",
          "info" if ok else "error")
    return redirect(url_for("main.firma_detail", id=id))


@bp.route("/firmy/<int:id>/toggle", methods=["POST"])
@zakazky_required
def firma_toggle(id):
    f = Firma.query.get_or_404(id)
    f.aktivni = not f.aktivni
    db.session.commit()
    return redirect(url_for("main.firma_detail", id=id))


@bp.route("/zakazka/<int:id>/toggle", methods=["POST"])
@zakazky_required
def zakazka_toggle(id):
    z = Zakazka.query.get_or_404(id)
    z.aktivni = not z.aktivni
    db.session.commit()
    return redirect(url_for("main.firma_detail", id=z.firma_id))


@bp.route("/firmy/<int:id>/pm", methods=["POST"])
@login_required
def firma_pm(id):
    f = Firma.query.get_or_404(id)
    f.projektovy_manazer = request.form.get("jmeno", "").strip() or None
    db.session.commit()
    flash(f"PM klienta nastaven: {f.projektovy_manazer or '—'}", "info")
    return redirect(url_for("main.firma_detail", id=id))


@bp.route("/firmy/<int:id>/upravit", methods=["GET", "POST"])
@klient_required
def firma_upravit(id):
    firma = Firma.query.get_or_404(id)
    if request.method == "POST":
        firma.nazev = request.form.get("nazev", firma.nazev).strip() or firma.nazev
        firma.ico = request.form.get("ico", "").strip()
        firma.dic = request.form.get("dic", "").strip()
        firma.adresa = request.form.get("adresa", "").strip()
        firma.web = request.form.get("web", "").strip()
        firma.obor = request.form.get("obor", "").strip()
        firma.zamestnanci = request.form.get("zamestnanci", "").strip()
        firma.obrat = request.form.get("obrat", "").strip()
        firma.rucne_upraveno = True  # zámek proti přepisu z MERK
        db.session.commit()
        flash("Firma uložena (ručně upraveno — MERK ji už nepřepíše).", "info")
        return redirect(url_for("main.firma_detail", id=id))
    return render_template("firma_upravit.html", firma=firma)


@bp.route("/firmy/<int:id>/kontakt/novy", methods=["POST"])
@klient_required
def kontakt_novy(id):
    Firma.query.get_or_404(id)
    db.session.add(Kontakt(
        firma_id=id, jmeno=request.form.get("jmeno", "").strip(),
        pozice=request.form.get("pozice", "").strip(),
        email=request.form.get("email", "").strip(),
        telefon=request.form.get("telefon", "").strip(),
        zdroj="rucne", rucne_upraveno=True))
    db.session.commit()
    flash("Kontakt přidán.", "info")
    return redirect(url_for("main.firma_detail", id=id))


@bp.route("/kontakt/<int:id>/upravit", methods=["GET", "POST"])
@klient_required
def kontakt_upravit(id):
    k = Kontakt.query.get_or_404(id)
    if request.method == "POST":
        k.jmeno = request.form.get("jmeno", "").strip()
        k.pozice = request.form.get("pozice", "").strip()
        k.email = request.form.get("email", "").strip()
        k.telefon = request.form.get("telefon", "").strip()
        k.rucne_upraveno = True
        db.session.commit()
        flash("Kontakt uložen.", "info")
        return redirect(url_for("main.firma_detail", id=k.firma_id))
    return render_template("kontakt_upravit.html", k=k)


@bp.route("/zakazka/<int:id>")
@login_required
def zakazka_detail(id):
    z = Zakazka.query.get_or_404(id)
    now = datetime.now(timezone.utc)
    snap, updated = snapshot.nacti()
    hod = (snap.get("hodiny") or {}).get(z.zkratka, {})
    mesice = list(range(1, now.month + 1))
    bill = [round(hod.get(f"{now.year}-{m:02d}", [0, 0])[1], 1) for m in mesice]
    celkem = [round(hod.get(f"{now.year}-{m:02d}", [0, 0])[0], 1) for m in mesice]
    uzivatele = snapshot.uzivatele_zkr(snap, [z.zkratka])
    z._pocet_mesicu = now.month
    z._tydny = _tydny_obdobi(z, [(now.year, mm) for mm in range(1, now.month + 1)])
    # KPI/vyčerpání: u fixního projektového kumulativně od začátku projektu, jinak letošní rok
    z.hodiny, z.hodiny_bill = snapshot.hodiny_pro_zakazku(snap, z, [f"{now.year}-{m:02d}" for m in mesice])
    z.hodiny_nonbill = round(max(z.hodiny - z.hodiny_bill, 0), 1)
    _napln_faktury([z], [f"{now.year}-{m:02d}" for m in mesice], vse=True)

    def _rozp_mesic(m):
        if z.typ_rozpoctu != "mesicni" or not z.rozpocet_hodin_tyden:
            return 0
        return round((z.rozpocet_hodin_tyden or 0) * _tydny_v_mesici(z, now.year, m))
    graf = {
        "labels": [MESICE_CZ[m][:3] for m in mesice],
        "celkem": celkem,
        "bill": bill,
        "rozpocet": [_rozp_mesic(m) for m in mesice],
        "trzba": [round(b * z.efekt_sazba) for b in bill],
    }
    return render_template("zakazka_detail.html", z=z, graf=graf, rok=now.year,
                           uzivatele=uzivatele, updated=updated)


@bp.route("/zakazka/<int:id>/pm", methods=["POST"])
@login_required
def zakazka_pm(id):
    z = Zakazka.query.get_or_404(id)
    z.projektovy_manazer = request.form.get("jmeno", "").strip() or None
    db.session.commit()
    flash(f"PM nastaven: {z.projektovy_manazer or '—'}", "info")
    return redirect(url_for("main.zakazka_detail", id=id))


@bp.route("/zakazka/<int:id>/faktura/nova", methods=["POST"])
@zakazky_required
def faktura_nova(id):
    Zakazka.query.get_or_404(id)
    castka = request.form.get("castka", "").strip().replace(",", ".").replace(" ", "")
    datum = request.form.get("datum", "").strip()
    try:
        d = datetime.strptime(datum, "%Y-%m-%d").date()
        c = float(castka)
    except ValueError:
        flash("Vyplň datum a částku.", "error")
        return redirect(url_for("main.zakazka_detail", id=id))
    db.session.add(Faktura(zakazka_id=id, datum=d, castka=c,
                           popis=request.form.get("popis", "").strip()))
    db.session.commit()
    flash("Faktura přidána.", "info")
    return redirect(url_for("main.zakazka_detail", id=id))


@bp.route("/faktura/<int:id>/smazat", methods=["POST"])
@zakazky_required
def faktura_smazat(id):
    f = Faktura.query.get_or_404(id)
    zid = f.zakazka_id
    db.session.delete(f)
    db.session.commit()
    flash("Faktura smazána.", "info")
    return redirect(url_for("main.zakazka_detail", id=zid))


@bp.route("/zakazka/<int:id>/upravit", methods=["GET", "POST"])
@zakazky_required
def zakazka_upravit(id):
    z = Zakazka.query.get_or_404(id)
    if request.method == "POST":
        def _num(pole):
            v = request.form.get(pole, "").strip().replace(",", ".").replace(" ", "")
            try:
                return float(v) if v else None
            except ValueError:
                return None
        typ = request.form.get("typ_rozpoctu", z.typ_rozpoctu)
        z.typ_rozpoctu = typ
        z.hodinova_sazba = _num("hodinova_sazba")
        z.rozpocet_hodin = _num("rozpocet_hodin")
        z.rozpocet_hodin_tyden = _num("rozpocet_hodin_tyden")
        # částka podle typu (různá pole ve formuláři)
        if typ == "analyza":
            z.budget_castka = _num("budget_castka_a")
        elif typ == "projektovy":
            z.budget_castka = _num("budget_castka_p")
        else:
            z.budget_castka = None
        z.analyza_zaloha = bool(request.form.get("analyza_zaloha"))
        z.analyza_odevzdano = bool(request.form.get("analyza_odevzdano"))
        for pole in ("datum_od", "datum_do"):
            val = request.form.get(pole, "").strip()
            try:
                setattr(z, pole, datetime.strptime(val, "%Y-%m-%d").date() if val else None)
            except ValueError:
                pass
        db.session.commit()
        flash("Zakázka uložena.", "info")
        return redirect(request.form.get("zpet") or url_for("main.dashboard"))
    return render_template("zakazka_upravit.html", z=z)


@bp.route("/kontakt/<int:id>/smazat", methods=["POST"])
@klient_required
def kontakt_smazat(id):
    k = Kontakt.query.get_or_404(id)
    fid = k.firma_id
    db.session.delete(k)
    db.session.commit()
    flash("Kontakt smazán.", "info")
    return redirect(url_for("main.firma_detail", id=fid))


# ─── Správa uživatelů (jen admin) ──────────────────────────────────
ROLE_POPIS = {"admin": "Admin (vše + uživatelé)", "editor": "Editor (zakázky, rozpočty, Obnovit)",
              "majitel": "Majitel (čtení + MERK/kontakty)", "interim": "Interim (čtení bez financí)"}


@bp.route("/admin")
@admin_required
def admin_hub():
    _, clk_updated = snapshot.nacti()
    _, fre_updated = snapshot_freelo.nacti()
    return render_template("admin.html",
                           freelo_ok=freelo_service.je_nakonfigurovano(),
                           clk_updated=clk_updated, fre_updated=fre_updated)


@bp.route("/admin/uzivatele")
@admin_required
def admin_uzivatele():
    uzivatele = User.query.order_by(User.role, User.jmeno).all()
    return render_template("admin_uzivatele.html", uzivatele=uzivatele, role_popis=ROLE_POPIS)


@bp.route("/admin/uzivatele/novy", methods=["POST"])
@admin_required
def admin_uzivatel_novy():
    email = request.form.get("email", "").strip().lower()
    if not email:
        flash("Vyplň e-mail.", "error")
        return redirect(url_for("main.admin_uzivatele"))
    if User.query.filter_by(email=email).first():
        flash("Uživatel s tímto e-mailem už existuje.", "error")
        return redirect(url_for("main.admin_uzivatele"))
    heslo = request.form.get("heslo", "").strip()
    db.session.add(User(
        email=email, jmeno=request.form.get("jmeno", "").strip(),
        role=request.form.get("role", "majitel"), aktivni=True,
        password_hash=generate_password_hash(heslo) if heslo else None))
    db.session.commit()
    flash("Uživatel přidán.", "info")
    return redirect(url_for("main.admin_uzivatele"))


@bp.route("/admin/uzivatele/<int:id>/upravit", methods=["POST"])
@admin_required
def admin_uzivatel_upravit(id):
    u = User.query.get_or_404(id)
    u.jmeno = request.form.get("jmeno", u.jmeno).strip()
    u.role = request.form.get("role", u.role)
    u.aktivni = bool(request.form.get("aktivni"))
    nove_heslo = request.form.get("heslo", "").strip()
    if nove_heslo:
        u.password_hash = generate_password_hash(nove_heslo)
    db.session.commit()
    flash("Uživatel uložen.", "info")
    return redirect(url_for("main.admin_uzivatele"))


@bp.route("/admin/uzivatele/<int:id>/smazat", methods=["POST"])
@admin_required
def admin_uzivatel_smazat(id):
    u = User.query.get_or_404(id)
    if u.id == session.get("user_id"):
        flash("Nemůžeš smazat sám sebe.", "error")
    else:
        db.session.delete(u)
        db.session.commit()
        flash("Uživatel smazán.", "info")
    return redirect(url_for("main.admin_uzivatele"))
