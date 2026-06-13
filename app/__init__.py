"""
__init__.py — application factory.
Bezpečnost: SECRET_KEY z env, CSRF ochrana zapnutá, žádné tajné údaje v kódu.
DB: lokálně SQLite (bez konfigurace), na Railway PostgreSQL přes DATABASE_URL.
"""
import os
from flask import Flask
from flask_wtf import CSRFProtect
from dotenv import load_dotenv

from .extensions import db

load_dotenv()  # načte .env lokálně (na Railway se ignoruje)
csrf = CSRFProtect()


def create_app():
    app = Flask(__name__, template_folder="../templates", static_folder="../static")

    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")

    # Databáze: Postgres na Railway, jinak lokální SQLite
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url or "sqlite:///kokpit.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)
    csrf.init_app(app)

    from .routes.main import bp as main_bp
    app.register_blueprint(main_bp)

    with app.app_context():
        db.create_all()           # vytvoří chybějící tabulky (i novou 'kontakt')
        _inline_migrace()         # doplní nové sloupce do existujících tabulek
        from .seed import seed_pokud_prazdno, backfill_ico
        seed_pokud_prazdno()
        backfill_ico()            # doplní IČO firmám z jejich zakázek

    return app


def _inline_migrace():
    """Bezpečně přidá nové sloupce do tabulky 'firma' na existující DB
    (create_all neumí ALTER). Funguje na PostgreSQL i SQLite."""
    from sqlalchemy import inspect, text
    try:
        insp = inspect(db.engine)
        if "firma" not in insp.get_table_names():
            return
        existujici = {c["name"] for c in insp.get_columns("firma")}
        nove = {
            "ico": "VARCHAR(20)", "dic": "VARCHAR(20)", "adresa": "VARCHAR(300)",
            "web": "VARCHAR(200)", "obor": "VARCHAR(200)", "zamestnanci": "VARCHAR(80)",
            "obrat": "VARCHAR(80)", "merk_nacteno": "VARCHAR(40)",
        }
        for col, typ in nove.items():
            if col not in existujici:
                db.session.execute(text(f"ALTER TABLE firma ADD COLUMN {col} {typ}"))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[migrace] {e}")
