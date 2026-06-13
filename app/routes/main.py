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
    """Z parametru 'YYYY-MM' vrátí (od, do, popis). Prázdné = celý aktuální rok."""
    now = datetime.now(timezone.utc)
    if mesic and "-" in mesic:
        rok, m = (int(x) for x in mesic.split("-"))
        posl = calendar.monthrange(rok, m)[1]
        od = f"{rok}-{m:02d}-01T00:00:00Z"
        do = f"{rok}-{m:02d}-{posl:02d}T23:59:59Z"
        return od, do, f"{MESICE_CZ[m]} {rok}"
    return (f"{now.year}-01-01T00:00:00Z",
            now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            f"rok {now.year}")


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
            return redirect(url_for("main.dashboard"))
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
    return redirect(url_for("main.dashboard"))


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
    """Vrátí (stav_indikatoru, popis). Bez rozpočtu zatím neutrální."""
    if not z.rozpocet_hodin:
        return ("neutral", "Bez rozpočtu hodin")
    pct = round(100 * z.hodiny / z.rozpocet_hodin) if z.rozpocet_hodin else 0
    if pct >= 100:
        return ("cervena", f"Přečerpáno ({pct} %)")
    if pct >= 85:
        return ("oranzova", f"Blízko rozpočtu ({pct} %)")
    return ("zelena", f"V rozpočtu ({pct} %)")


@bp.route("/")
@login_required
def dashboard():
    # Filtry
    f_aktivita = request.args.get("aktivita", "aktivni")  # výchozí: jen aktivní
    f_typ = request.args.get("typ", "")
    hledat = request.args.get("q", "").strip()

    q = Zakazka.query.join(Firma)
    if f_aktivita == "aktivni":   # aktivní zakázka i aktivní klient
        q = q.filter(Zakazka.aktivni.is_(True), Firma.aktivni.is_(True))
    elif f_aktivita == "neaktivni":  # zakázka NEBO klient neaktivní
        q = q.filter(db.or_(Zakazka.aktivni.is_(False), Firma.aktivni.is_(False)))
    if f_typ:
        q = q.filter(Zakazka.typ_sluzby == f_typ)
    if hledat:
        like = f"%{hledat}%"
        q = q.filter(db.or_(Zakazka.nazev.ilike(like), Zakazka.zkratka.ilike(like)))
    zakazky = q.order_by(Zakazka.nazev).all()

    # Hodiny z Clockify za zvolené období (měsíc nebo celý rok)
    mesic = request.args.get("mesic", "")
    od, do, obdobi_popis = _obdobi(mesic)
    clockify.obohat_zakazky(zakazky, od, do)

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
        "pocet_firem": len({z.firma_id for z in zakazky}),
        "clockify_ok": clockify.je_nakonfigurovano(),
    }
    return render_template("stav_zakazek.html", zakazky=zakazky, souhrn=souhrn,
                           typy=typy, f_aktivita=f_aktivita, f_typ=f_typ, hledat=hledat,
                           mesice=_seznam_mesicu(), mesic=mesic, obdobi_popis=obdobi_popis)


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
    return render_template("firma_detail.html", firma=firma)


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


@bp.route("/zakazka/<int:id>/upravit", methods=["GET", "POST"])
@login_required
def zakazka_upravit(id):
    z = Zakazka.query.get_or_404(id)
    if request.method == "POST":
        rh = request.form.get("rozpocet_hodin", "").strip().replace(",", ".")
        try:
            z.rozpocet_hodin = float(rh) if rh else None
        except ValueError:
            z.rozpocet_hodin = z.rozpocet_hodin
        z.stav = request.form.get("stav", z.stav)
        for pole in ("datum_od", "datum_do"):
            val = request.form.get(pole, "").strip()
            try:
                setattr(z, pole, datetime.strptime(val, "%Y-%m-%d").date() if val else None)
            except ValueError:
                pass
        db.session.commit()
        flash("Zakázka uložena.", "info")
        return redirect(url_for("main.dashboard"))
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
