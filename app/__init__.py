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

    # Oprávnění + odkazy na ostatní aplikace dostupné v šablonách
    from .auth import smi_zakazky, smi_klient, vidi_finance, je_admin
    from .extensions import PORTAL_URL, BRAIN_URL, FREELO_APP_URL
    app.context_processor(lambda: {"smi_zakazky": smi_zakazky, "smi_klient": smi_klient,
                                   "vidi_finance": vidi_finance, "je_admin": je_admin,
                                   "PORTAL_URL": PORTAL_URL, "BRAIN_URL": BRAIN_URL,
                                   "FREELO_APP_URL": FREELO_APP_URL})

    with app.app_context():
        db.create_all()           # vytvoří chybějící tabulky
        _inline_migrace()         # doplní nové sloupce do existujících tabulek
        from .seed import seed_pokud_prazdno, backfill_ico
        seed_pokud_prazdno()
        backfill_ico()            # doplní IČO firmám z jejich zakázek
        _backfill_tyden()         # převede staré měsíční rozpočty na týdenní
        _bootstrap_admin()        # založí/aktualizuje hlavního admina z env
        _reset_indexace()         # po restartu uvolní zaseknuté příznaky indexace

    return app


def _reset_indexace():
    """Po restartu appky (deploy) vlákna indexace nepřežijí → uvolni zaseknuté příznaky."""
    from .models import Firma
    try:
        zmeneno = Firma.query.filter_by(dok_index_bezi=True).update(
            {"dok_index_bezi": False, "dok_index_progress": None})
        if zmeneno:
            db.session.commit()
            print(f"[start] Uvolněno {zmeneno} zaseknutých indexací.")
    except Exception as e:
        db.session.rollback()
        print(f"[start] reset indexace: {e}")


def _backfill_tyden():
    """Převede starý měsíční rozpočet hodin na týdenní (děleno 4.33), kde týdenní chybí."""
    from .models import Zakazka
    zmeneno = 0
    for z in Zakazka.query.filter(Zakazka.typ_rozpoctu == "mesicni",
                                  Zakazka.rozpocet_hodin_mesic.isnot(None),
                                  Zakazka.rozpocet_hodin_tyden.is_(None)).all():
        z.rozpocet_hodin_tyden = round((z.rozpocet_hodin_mesic or 0) / 4.33, 1)
        zmeneno += 1
    if zmeneno:
        db.session.commit()
        print(f"[backfill] Převedeno {zmeneno} měsíčních rozpočtů na týdenní.")


def _bootstrap_admin():
    """Zajistí, že hlavní admin (ADMIN_EMAIL) existuje a má roli admin — nelze se zamknout."""
    from werkzeug.security import generate_password_hash
    from .extensions import ADMIN_EMAIL, ADMIN_PASSWORD
    from .models import User
    if not ADMIN_EMAIL:
        return
    u = User.query.filter_by(email=ADMIN_EMAIL).first()
    if not u:
        u = User(email=ADMIN_EMAIL, jmeno="Admin", role="admin", aktivni=True,
                 password_hash=generate_password_hash(ADMIN_PASSWORD))
        db.session.add(u)
    else:
        u.role = "admin"
        u.aktivni = True
    db.session.commit()


def _inline_migrace():
    """Bezpečně přidá nové sloupce do tabulky 'firma' na existující DB
    (create_all neumí ALTER). Funguje na PostgreSQL i SQLite."""
    from sqlalchemy import inspect, text
    try:
        insp = inspect(db.engine)
        tabulky = insp.get_table_names()
        plan = {
            "firma": {
                "typ_subjektu": "VARCHAR(20) DEFAULT 'klient'",
                "ico": "VARCHAR(20)", "dic": "VARCHAR(20)", "adresa": "VARCHAR(300)",
                "web": "VARCHAR(200)", "obor": "VARCHAR(200)", "zamestnanci": "VARCHAR(80)",
                "obrat": "VARCHAR(80)", "merk_nacteno": "VARCHAR(40)",
                "rucne_upraveno": "BOOLEAN DEFAULT FALSE",
                "aktivni": "BOOLEAN DEFAULT TRUE",
                "projektovy_manazer": "VARCHAR(120)",
                "freelo_tasklist_id": "INTEGER",
                "onedrive_odkaz": "VARCHAR(800)",
                "dok_index_bezi": "BOOLEAN DEFAULT FALSE",
                "dok_index_progress": "VARCHAR(60)",
                "dok_index_celkem": "INTEGER DEFAULT 0",
            },
            "kontakt": {
                "rucne_upraveno": "BOOLEAN DEFAULT FALSE",
            },
            "uzivatel": {
                "freelo_email": "VARCHAR(160)",
                "freelo_api_key": "VARCHAR(255)",
            },
            "klient_dokument": {
                "soubor_zmeneno": "VARCHAR(40)",
            },
            "vozidlo": {
                "vin": "VARCHAR(40)", "rok_vyroby": "INTEGER",
                "servis_interval_km": "INTEGER", "posledni_servis_km": "INTEGER",
                "posledni_servis_datum": "DATE", "stk_do": "DATE",
            },
            "zakazka": {
                "aktivni": "BOOLEAN DEFAULT TRUE",
                "projektovy_manazer": "VARCHAR(120)",
                "typ_rozpoctu": "VARCHAR(20) DEFAULT 'projektovy'",
                "hodinova_sazba": "FLOAT",
                "rozpocet_hodin_mesic": "FLOAT",
                "rozpocet_hodin_tyden": "FLOAT",
                "budget_castka": "FLOAT",
                "analyza_zaloha": "BOOLEAN DEFAULT FALSE",
                "analyza_odevzdano": "BOOLEAN DEFAULT FALSE",
            },
        }
        for tab, sloupce in plan.items():
            if tab not in tabulky:
                continue
            existujici = {c["name"] for c in insp.get_columns(tab)}
            for col, typ in sloupce.items():
                if col not in existujici:
                    db.session.execute(text(f"ALTER TABLE {tab} ADD COLUMN {col} {typ}"))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[migrace] {e}")
