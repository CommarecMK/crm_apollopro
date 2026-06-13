"""
routes/main.py — přihlášení + kokpit "Stav zakázek".
"""
import os
import calendar
from datetime import datetime, timezone
from flask import (Blueprint, render_template, request, redirect,
                   url_for, session, flash, jsonify)

from ..extensions import db, ADMIN_PASSWORD
from ..models import Zakazka, Firma, Kontakt
from ..auth import login_required
from ..services import clockify, firmy as firmy_service

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


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("heslo") == ADMIN_PASSWORD:
            session["prihlasen"] = True
            return redirect(url_for("main.prehled"))
        flash("Nesprávné heslo.", "error")
    return render_template("login.html")


@bp.route("/auth")
def sso_vstup():
    """Přijme podepsaný SSO token z Apollo Pro portálu (stejné jako CRM/Brain).
    Token nese id/name/role a je podepsaný sdíleným SSO_SECRET — nepotřebuje
    sahat do databáze CRM."""
    from ..sso import over_token
    udaje = over_token(request.args.get("token", ""))
    portal = os.environ.get("PORTAL_URL", "https://apollopro.io")
    if not udaje:
        return redirect(portal + "/login")
    session["user_id"]   = udaje.get("id")
    session["user_name"] = udaje.get("name")
    session["user_role"] = udaje.get("role")
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


def _vyhodnot_riziko(z):
    """Vrátí (stav_indikatoru, popis) podle čerpání hodin vs rozpočet."""
    rozpocet = z.rozpocet_hodin_efekt
    if not rozpocet:
        return ("neutral", "Bez rozpočtu hodin")
    pct = round(100 * (z.hodiny or 0) / rozpocet)
    if pct >= 100:
        return ("cervena", f"Přečerpáno ({pct} %)")
    if pct >= 85:
        return ("oranzova", f"Blízko rozpočtu ({pct} %)")
    return ("zelena", f"V rozpočtu ({pct} %)")


@bp.route("/")
@login_required
def prehled():
    """Celofiremní přehledový dashboard (úvodní stránka)."""
    now = datetime.now(timezone.utc)
    zakazky = (Zakazka.query.join(Firma)
               .filter(Zakazka.aktivni.is_(True), Firma.aktivni.is_(True)).all())
    data = clockify.prehled_vse(now.year, now.month)
    bzm = data["bill_zkr_mesic"]
    sazby = {z.zkratka: (z.hodinova_sazba or 0) for z in zakazky}

    # YTD hodnoty na zakázku → KPI + rozpad
    for z in zakazky:
        z.hodiny_bill = round(sum(bzm.get(z.zkratka, {}).values()), 1)
        z.hodiny = z.hodiny_bill  # pro KPI stačí fakturovatelné
        z._pocet_mesicu = sum(1 for m in range(1, now.month + 1)
                              if not z.datum_od or (now.year, m) >= (z.datum_od.year, z.datum_od.month))

    # Měsíční tržby (hodiny × sazba) napříč firmou
    trzby_mesic = []
    for m, _, _ in data["serie"]:
        s = sum(bzm.get(zk, {}).get(m, 0) * sazby.get(zk, 0) for zk in bzm)
        trzby_mesic.append(round(s))

    kpi = {
        "trzby": round(sum(z.trzba_skutecnost for z in zakazky)),
        "hodin_bill": round(sum(z.hodiny_bill for z in zakazky), 1),
        "potencial": round(sum(z.nenaplneny_potencial for z in zakazky)),
        "zakazek": len(zakazky),
        "klientu": len({z.firma_id for z in zakazky}),
        "clockify_ok": clockify.je_nakonfigurovano(),
    }
    # Rozpad tržeb dle typu služby
    rozpad = {}
    for z in zakazky:
        rozpad[z.typ_sluzby or "—"] = rozpad.get(z.typ_sluzby or "—", 0) + z.trzba_skutecnost
    rozpad = sorted([(t, round(v)) for t, v in rozpad.items() if v], key=lambda x: -x[1])
    # Top klienti dle tržeb
    klienti = {}
    for z in zakazky:
        klienti[z.firma.nazev] = klienti.get(z.firma.nazev, 0) + z.trzba_skutecnost
    top_klienti = sorted([(n, round(v)) for n, v in klienti.items() if v], key=lambda x: -x[1])[:8]

    graf = {
        "labels": [MESICE_CZ[m][:3] for m, _, _ in data["serie"]],
        "bill": [b for _, _, b in data["serie"]],
        "trzby": trzby_mesic,
    }
    return render_template("prehled.html", kpi=kpi, graf=graf, rozpad=rozpad,
                           top_klienti=top_klienti, rok=now.year)


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
    periods = []
    for r, m in months:
        posl = calendar.monthrange(r, m)[1]
        periods.append((f"{r}-{m:02d}-01T00:00:00Z", f"{r}-{m:02d}-{posl:02d}T23:59:59Z"))

    q = Zakazka.query.join(Firma)
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

    clockify.obohat_zakazky_obdobi(zakazky, periods)

    def _aktivni_mesic(r, m, od_d, do_d):
        if od_d and (r, m) < (od_d.year, od_d.month):
            return False
        end = do_d or now.date()
        if (r, m) > (end.year, end.month):
            return False
        return True
    for z in zakazky:
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
                           mesice=_seznam_mesicu(), f_mesice=f_mesice, obdobi_popis=obdobi_popis)


# ─── Firmy (databáze klientů + MERK/ARES) ──────────────────────────
@bp.route("/firmy")
@login_required
def firmy():
    hledat = request.args.get("q", "").strip()
    q = Firma.query
    if hledat:
        q = q.filter(db.or_(Firma.nazev.ilike(f"%{hledat}%"), Firma.ico.ilike(f"%{hledat}%")))
    seznam = q.order_by(Firma.nazev).all()
    return render_template("firmy.html", firmy=seznam, hledat=hledat)


@bp.route("/firmy/<int:id>")
@login_required
def firma_detail(id):
    firma = Firma.query.get_or_404(id)
    now = datetime.now(timezone.utc)
    zkratky = {z.zkratka for z in firma.zakazky}
    prehled = clockify.firma_prehled(zkratky, now.year, now.month)
    perz = prehled["zakazky"]
    for z in firma.zakazky:
        v = perz.get(z.zkratka) or {}
        z.hodiny = v.get("celkem", 0.0)
        z.hodiny_bill = v.get("bill", 0.0)
        z.hodiny_nonbill = v.get("nonbill", 0.0)
        z._pocet_mesicu = sum(1 for m in range(1, now.month + 1)
                              if not z.datum_od or (now.year, m) >= (z.datum_od.year, z.datum_od.month))
    serie = prehled["serie"]

    def _akt_v_mesici(z, m):
        if z.datum_od and (now.year, m) < (z.datum_od.year, z.datum_od.month):
            return False
        if z.datum_do and (now.year, m) > (z.datum_do.year, z.datum_do.month):
            return False
        return True
    # Rozpočet hodin dle smlouvy = měsíční kontrahované hodiny (typ "mesicni") aktivní v daném měsíci
    rozpocet_mesic = []
    for m, _, _ in serie:
        rozpocet_mesic.append(round(sum((z.rozpocet_hodin_mesic or 0) for z in firma.zakazky
                                        if z.typ_rozpoctu == "mesicni" and _akt_v_mesici(z, m))))
    graf = {"labels": [MESICE_CZ[m][:3] for m, _, _ in serie],
            "celkem": [t for _, t, _ in serie],
            "bill": [b for _, _, b in serie],
            "rozpocet": rozpocet_mesic}
    kpi = {"pocet": len(firma.zakazky),
           "hodin_bill": round(sum(z.hodiny_bill for z in firma.zakazky), 1),
           "rozpocet_h": round(sum(rozpocet_mesic)),
           "trzby": round(sum(z.trzba_skutecnost for z in firma.zakazky)),
           "potencial": round(sum(z.nenaplneny_potencial for z in firma.zakazky))}
    od_rok = f"{now.year}-01-01T00:00:00Z"
    do_ted = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    uzivatele = clockify.hodiny_uzivatele_firma(zkratky, od_rok, do_ted)
    return render_template("firma_detail.html", firma=firma, graf=graf, kpi=kpi,
                           rok=now.year, uzivatele=uzivatele)


@bp.route("/firmy/<int:id>/nacist", methods=["POST"])
@login_required
def firma_nacist(id):
    firma = Firma.query.get_or_404(id)
    ok, zdroj = firmy_service.obohat_firmu(firma)
    flash(f"Načteno z {zdroj}." if ok else "Nepodařilo se načíst data (zkontroluj IČO / MERK klíč).",
          "info" if ok else "error")
    return redirect(url_for("main.firma_detail", id=id))


@bp.route("/firmy/<int:id>/toggle", methods=["POST"])
@login_required
def firma_toggle(id):
    f = Firma.query.get_or_404(id)
    f.aktivni = not f.aktivni
    db.session.commit()
    return redirect(url_for("main.firma_detail", id=id))


@bp.route("/zakazka/<int:id>/toggle", methods=["POST"])
@login_required
def zakazka_toggle(id):
    z = Zakazka.query.get_or_404(id)
    z.aktivni = not z.aktivni
    db.session.commit()
    return redirect(url_for("main.firma_detail", id=z.firma_id))


@bp.route("/firmy/<int:id>/upravit", methods=["GET", "POST"])
@login_required
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
@login_required
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
@login_required
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
    serie = clockify.mesicni_serie(z.zkratka, now.year, now.month)
    od_rok = f"{now.year}-01-01T00:00:00Z"
    do_ted = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    uzivatele = clockify.hodiny_dle_uzivatelu(z.zkratka, od_rok, do_ted)
    z._pocet_mesicu = now.month
    z.hodiny = round(sum(s[1] for s in serie), 1)
    z.hodiny_bill = round(sum(s[2] for s in serie), 1)
    z.hodiny_nonbill = round(z.hodiny - z.hodiny_bill, 1)
    def _rozp_mesic(m):
        if z.typ_rozpoctu != "mesicni" or not z.rozpocet_hodin_mesic:
            return 0
        if z.datum_od and (now.year, m) < (z.datum_od.year, z.datum_od.month):
            return 0
        if z.datum_do and (now.year, m) > (z.datum_do.year, z.datum_do.month):
            return 0
        return round(z.rozpocet_hodin_mesic)
    graf = {
        "labels": [MESICE_CZ[m][:3] for m, _, _ in serie],
        "celkem": [tot for _, tot, _ in serie],
        "bill": [bil for _, _, bil in serie],
        "rozpocet": [_rozp_mesic(m) for m, _, _ in serie],
        "trzba": [round((bil) * (z.hodinova_sazba or 0)) for _, _, bil in serie],
    }
    return render_template("zakazka_detail.html", z=z, graf=graf, rok=now.year, uzivatele=uzivatele)


@bp.route("/zakazka/<int:id>/upravit", methods=["GET", "POST"])
@login_required
def zakazka_upravit(id):
    z = Zakazka.query.get_or_404(id)
    if request.method == "POST":
        def _num(pole):
            v = request.form.get(pole, "").strip().replace(",", ".").replace(" ", "")
            try:
                return float(v) if v else None
            except ValueError:
                return None
        z.projektovy_manazer = request.form.get("projektovy_manazer", "").strip() or None
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
@login_required
def kontakt_smazat(id):
    k = Kontakt.query.get_or_404(id)
    fid = k.firma_id
    db.session.delete(k)
    db.session.commit()
    flash("Kontakt smazán.", "info")
    return redirect(url_for("main.firma_detail", id=fid))
