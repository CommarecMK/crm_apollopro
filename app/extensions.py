"""
extensions.py — sdílené instance a env proměnné. Nevytváří Flask app.
"""
import os
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# ─── Env proměnné ───────────────────────────────────────────────
ADMIN_PASSWORD        = os.environ.get("ADMIN_PASSWORD", "admin")
ADMIN_EMAIL           = os.environ.get("ADMIN_EMAIL", "martin.komarek@commarec.cz")
COMPANY_ICO           = os.environ.get("COMPANY_ICO", "21836256")  # Commarec s.r.o. = interní zakázky
CLOCKIFY_API_KEY      = os.environ.get("CLOCKIFY_API_KEY", "")
CLOCKIFY_WORKSPACE_ID = os.environ.get("CLOCKIFY_WORKSPACE_ID", "")
ANTHROPIC_API_KEY     = os.environ.get("ANTHROPIC_API_KEY", "")
FREELO_EMAIL          = os.environ.get("FREELO_EMAIL", "")
FREELO_API_KEY        = os.environ.get("FREELO_API_KEY", "")
CRON_KEY              = os.environ.get("CRON_KEY", "")  # token pro denní obnovu snapshotu
PORTAL_URL            = os.environ.get("PORTAL_URL", "https://apollopro.io")
BRAIN_URL             = os.environ.get("BRAIN_URL", "https://brain.apollopro.io")
FREELO_APP_URL        = os.environ.get("FREELO_APP_URL", "https://app.freelo.io")
# Microsoft Graph (OneDrive/SharePoint) — app-only přístup k dokumentům klientů
GRAPH_TENANT_ID       = os.environ.get("GRAPH_TENANT_ID", "")
GRAPH_CLIENT_ID       = os.environ.get("GRAPH_CLIENT_ID", "")
GRAPH_CLIENT_SECRET   = os.environ.get("GRAPH_CLIENT_SECRET", "")
