"""
routes/main.py — přihlášení + kokpit "Stav zakázek".
"""
import os
import calendar
from datetime import datetime, timezone
from flask import (Blueprint, render_template, request, redirect,
                   url_for, session, flash, jsonify)

from werkzeug.security import check_password_hash, generate_password_hash
from ..extensions import db, COMPANY_ICO
from ..models import Zakazka, Firma, Kontakt, User


def _bez_internich(q):
    """Odfiltruje interní zakázky (pod IČO Commarecu)."""
    return q.filter(db.or_(Firma.ico.is_(None), Firma.ico != COMPANY_ICO))


def _jen_interni(q):
    return q.filter(Firma.ico == COMPANY_ICO)
from ..auth import login_required, klient_required, zakazky_required, admin_required
from ..services import clockify, firmy as firmy_service, snapshot

bp = Blueprint("main", __name__)

MESICE_CZ = ["", "leden", "únor", "březen", "duben", "květen", "červen",
             "červenec", "srpen", "září", "říjen", "listopad", "prosinec"]


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
    u = User.query.filter_by(sso_id=udaje.get("id")).first()
    if not u:
        u = User(sso_id=udaje.get("id"), jmeno=udaje.get("name"), role="majitel", aktivni=True)
        db.session.add(u)
        db.session.commit()
    if not u.aktivni:
        return redirect(portal + "/login")
    _prihlas(u)
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
    """Ruční obnova dat z Clockify (snapshot)."""
    ok, info = snapshot.obnov()
    flash("Data z Clockify obnovena." if ok else f"Chyba obnovy: {info}", "info" if ok else "error")
    return redirect(request.referrer or url_for("main.prehled"))


@bp.route("/cron/obnovit")
def cron_obnovit():
    """Denní automatická obnova — chráněno tokenem CRON_KEY (?key=...)."""
    from ..extensions import CRON_KEY
    if not CRON_KEY or request.args.get("key") != CRON_KEY:
        return ("Neautorizováno", 403)
    ok, info = snapshot.obnov()
    return (f"OK {info}", 200) if ok else (f"CHYBA {info}", 500)


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
        # 3) dlouho bez aktivity (poslední 2 měsíce 0 h)
        akt = sum(hod.get(z.zkratka, {}).get(mm, [0, 0])[0] for mm in posl2)
        if akt == 0:
            alerty.append({"barva": "neutral", "firma_id": z.firma_id, "zakazka": z.nazev,
                           "popis": "Žádná aktivita poslední 2 měsíce"})
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

    # Graf, KPI a hodiny lidí = JEN AKTIVNÍ zakázky
    aktivni = [z for z in firma.zakazky if z.je_aktivni]
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
        rozpocet_mesic.append(round(sum((z.rozpocet_hodin_mesic or 0) for z in aktivni
                                        if z.typ_rozpoctu == "mesicni" and _akt_v_mesici(z, m))))
    graf = {"labels": [MESICE_CZ[m][:3] for m in range(1, now.month + 1)],
            "celkem": serie_tot, "bill": serie_bill, "rozpocet": rozpocet_mesic}
    kpi = {"pocet": len(aktivni),
           "hodin_bill": round(sum(z.hodiny_bill for z in aktivni), 1),
           "rozpocet_h": round(sum(rozpocet_mesic)),
           "trzby": round(sum(z.trzba_skutecnost for z in aktivni)),
           "potencial": round(sum(z.nenaplneny_potencial for z in aktivni))}
    uzivatele = snapshot.uzivatele_zkr(snap, aktivni_zkr)
    pm_jmena = {z.projektovy_manazer.strip() for z in aktivni if z.projektovy_manazer}
    return render_template("firma_detail.html", firma=firma, graf=graf, kpi=kpi,
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
    # KPI/vyčerpání: u fixního projektového kumulativně od začátku projektu, jinak letošní rok
    z.hodiny, z.hodiny_bill = snapshot.hodiny_pro_zakazku(snap, z, [f"{now.year}-{m:02d}" for m in mesice])
    z.hodiny_nonbill = round(max(z.hodiny - z.hodiny_bill, 0), 1)

    def _rozp_mesic(m):
        if z.typ_rozpoctu != "mesicni" or not z.rozpocet_hodin_mesic:
            return 0
        if z.datum_od and (now.year, m) < (z.datum_od.year, z.datum_od.month):
            return 0
        if z.datum_do and (now.year, m) > (z.datum_do.year, z.datum_do.month):
            return 0
        return round(z.rozpocet_hodin_mesic)
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
        z.rozpocet_hodin_mesic = _num("rozpocet_hodin_mesic")
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
