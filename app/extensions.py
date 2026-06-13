"""
extensions.py — sdílené instance a env proměnné. Nevytváří Flask app.
"""
import os
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# ─── Env proměnné ───────────────────────────────────────────────
ADMIN_PASSWORD        = os.environ.get("ADMIN_PASSWORD", "admin")
CLOCKIFY_API_KEY      = os.environ.get("CLOCKIFY_API_KEY", "")
CLOCKIFY_WORKSPACE_ID = os.environ.get("CLOCKIFY_WORKSPACE_ID", "")
ANTHROPIC_API_KEY     = os.environ.get("ANTHROPIC_API_KEY", "")
