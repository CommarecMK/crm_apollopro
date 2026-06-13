"""
routes/main.py — přihlášení + kokpit "Stav zakázek".
"""
import os
from datetime import datetime, timezone
from flask import (Blueprint, render_template, request, redirect,
                   url_for, session, flash, jsonify)

from ..extensions import db, ADMIN_PASSWORD
from ..models import Zakazka
from ..auth import login_required
from ..services import clockify

bp = Blueprint("main", __name__)


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
    f_stav = request.args.get("stav", "")
    f_typ = request.args.get("typ", "")
    hledat = request.args.get("q", "").strip()

    q = Zakazka.query
    if f_stav:
        q = q.filter(Zakazka.stav == f_stav)
    if f_typ:
        q = q.filter(Zakazka.typ_sluzby == f_typ)
    if hledat:
        like = f"%{hledat}%"
        q = q.filter(db.or_(Zakazka.nazev.ilike(like), Zakazka.zkratka.ilike(like)))
    zakazky = q.order_by(Zakazka.nazev).all()

    # Hodiny z Clockify za aktuální rok
    rok = datetime.now(timezone.utc).year
    od = f"{rok}-01-01T00:00:00Z"
    do = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    clockify.obohat_zakazky(zakazky, od, do)

    for z in zakazky:
        z.riziko, z.riziko_popis = _vyhodnot_riziko(z)

    typy = sorted({z.typ_sluzby for z in Zakazka.query.all() if z.typ_sluzby})
    souhrn = {
        "pocet": len(zakazky),
        "celkem_hodin": round(sum(z.hodiny for z in zakazky), 1),
        "celkem_bill": round(sum(z.hodiny_bill for z in zakazky), 1),
        "celkem_nonbill": round(sum(z.hodiny_nonbill for z in zakazky), 1),
        "pocet_firem": len({z.firma_id for z in zakazky}),
        "clockify_ok": clockify.je_nakonfigurovano(),
    }
    return render_template("stav_zakazek.html", zakazky=zakazky, souhrn=souhrn,
                           typy=typy, f_stav=f_stav, f_typ=f_typ, hledat=hledat,
                           rok=rok)
