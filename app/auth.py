"""
auth.py — přihlášení a role/oprávnění.

Role:
  admin   — vše + správa uživatelů
  editor  — úpravy zakázek, rozpočty, aktivita, Obnovit (+ vše co majitel)
  majitel — čtení vč. financí + MERK/kontakty
  interim — čtení BEZ financí (hodiny, fond, konec projektu, alerty); pro Freelo/zápisy
"""
from functools import wraps
from flask import session, redirect, url_for, abort

ROLE_FINANCE = ("admin", "editor", "majitel")     # vidí částky
ROLE_KLIENT  = ("admin", "editor", "majitel")     # smí MERK/kontakty
ROLE_ZAKAZKY = ("admin", "editor")                # smí parametry zakázek/Obnovit


def _r():
    return session.get("user_role")


def login_required(f):
    @wraps(f)
    def d(*a, **k):
        if not session.get("user_id"):
            return redirect(url_for("main.login"))
        return f(*a, **k)
    return d


def klient_required(f):
    @wraps(f)
    def d(*a, **k):
        if not session.get("user_id"):
            return redirect(url_for("main.login"))
        if _r() not in ROLE_KLIENT:
            return abort(403)
        return f(*a, **k)
    return d


def zakazky_required(f):
    @wraps(f)
    def d(*a, **k):
        if not session.get("user_id"):
            return redirect(url_for("main.login"))
        if _r() not in ROLE_ZAKAZKY:
            return abort(403)
        return f(*a, **k)
    return d


def admin_required(f):
    @wraps(f)
    def d(*a, **k):
        if not session.get("user_id"):
            return redirect(url_for("main.login"))
        if _r() != "admin":
            return abort(403)
        return f(*a, **k)
    return d


# Pomocné pro šablony
def vidi_finance(): return _r() in ROLE_FINANCE
def smi_klient():   return _r() in ROLE_KLIENT
def smi_zakazky():  return _r() in ROLE_ZAKAZKY
def je_admin():     return _r() == "admin"
