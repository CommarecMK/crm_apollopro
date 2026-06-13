"""
sso.py — připraveno pro pozdější napojení na Apollo Pro portál.
Stejný princip jako stávající appky (sdílený SSO_SECRET, token platný 120 s).
Zatím se v MVP nepoužívá — přihlášení řeší auth.py.
"""
import os
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature

SSO_SECRET = os.environ.get("SSO_SECRET", "ZMENTE-TOTO-NA-RAILWAY")
TOKEN_PLATNOST = 120


def vytvor_token(user_id, user_name, user_role):
    s = URLSafeTimedSerializer(SSO_SECRET)
    return s.dumps({"id": user_id, "name": user_name, "role": user_role}, salt="sso-prechod")


def over_token(token):
    s = URLSafeTimedSerializer(SSO_SECRET)
    try:
        return s.loads(token, salt="sso-prechod", max_age=TOKEN_PLATNOST)
    except (SignatureExpired, BadSignature):
        return None
