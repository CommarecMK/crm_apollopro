"""
auth.py — jednoduché přihlášení pro MVP (heslo z ADMIN_PASSWORD).
Později nahradíme SSO přes Apollo Pro portál (sso.py je připravené).
"""
from functools import wraps
from flask import session, redirect, url_for


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # Přihlášení přes portál (SSO) NEBO lokální heslo
        if not (session.get("prihlasen") or session.get("user_id")):
            return redirect(url_for("main.login"))
        return f(*args, **kwargs)
    return decorated
