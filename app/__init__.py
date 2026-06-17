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
        _seed_vozidla()           # jednorázově naplní vozový park z dat Fleet
        _seed_historie_jizd()     # jednorázově naimportuje historii jízd 2026

    return app


def _seed_vozidla():
    """Jednorázově naplní/doplní vozidla z firemního Fleetu (data z leasingových smluv,
    předávacích protokolů a Souhrn aut.xlsx). Spustí se jen jednou (příznak v Nastaveni),
    aby nepřepisoval pozdější ruční úpravy. Upsert dle SPZ — existující jen doplní."""
    from datetime import date
    from .models import Vozidlo, Nastaveni
    PRIZNAK = "seed_vozidla_v1"
    try:
        if Nastaveni.query.get(PRIZNAK):
            return
        # SPZ, model, palivo, splátka, nájem_od, nájem_do, nájezd_limit, servis_km, VIN, řidič
        data = [
            ("1AE N958", "KIA Sportage V 1.6 T-GDi (šedá)", "benzin", 11406, date(2024, 8, 13), date(2026, 8, 13), 60000, 15000, None, "Steiner"),
            ("1AE U402", "KIA Sportage V 1.6 T-GDi (zelená)", "benzin", 9266, date(2024, 9, 29), date(2026, 9, 29), 60000, 15000, "U5YPV81BHRL280518", "Matějka"),
            ("1AI N520", "Ford Puma 1.0 EcoBoost mHEV Titanium", "benzin", 6250, date(2025, 3, 27), date(2027, 3, 27), 40000, 30000, "WF02XXERK2RT51673", None),
            ("1AJ E369", "VW Tayron 2.0 TDI 4MOTION R-Line", "nafta", 18766, date(2025, 6, 18), date(2027, 6, 18), 60000, 30000, "WVGZZZR47SW022175", "Komárek"),
            ("1AJ T473", "Škoda Kodiaq (modrý)", "nafta", 11720, date(2025, 6, 26), date(2027, 6, 26), 60000, 30000, None, "Bezděk"),
            ("1AM Z598", "KIA K4 1.6 T-GDI Exclusive (červená)", "benzin", 10042, date(2026, 2, 26), date(2028, 2, 26), 60000, 15000, "3KPFX51C0TE233772", "Hlavatý"),
            ("1AP J946", "Škoda Kodiaq (šedá)", "nafta", 13145, date(2026, 6, 2), date(2028, 6, 2), 60000, 30000, None, "Matějka"),
        ]
        existujici = {(v.spz or "").replace(" ", "").upper(): v for v in Vozidlo.query.all()}
        zalozeno = doplneno = 0
        for spz, model, palivo, splatka, n_od, n_do, najezd, servis, vin, ridic in data:
            v = existujici.get(spz.replace(" ", "").upper())
            novy = v is None
            if novy:
                v = Vozidlo(spz=spz, aktivni=True, tachometr_pocatek=0)
                db.session.add(v)
            # model přepiš jen je-li prázdný nebo placeholder z CCS
            if novy or not v.model or "faktur" in (v.model or "").lower():
                v.model = model
            if novy:
                v.palivo = palivo
            for pole, hod in (("splatka", splatka), ("najem_od", n_od), ("najem_do", n_do),
                              ("najezd_limit", najezd), ("servis_interval_km", servis),
                              ("vin", vin), ("ridic", ridic)):
                if getattr(v, pole) in (None, "") and hod is not None:
                    setattr(v, pole, hod)
            zalozeno += novy
            doplneno += (not novy)
            if novy:
                existujici[spz.replace(" ", "").upper()] = v
        db.session.add(Nastaveni(klic=PRIZNAK, hodnota="hotovo"))
        db.session.commit()
        print(f"[seed] Vozidla: založeno {zalozeno}, doplněno {doplneno}.")
    except Exception as e:
        db.session.rollback()
        print(f"[seed] vozidla: {e}")


def _seed_historie_jizd():
    """Jednorázově naimportuje historii jízd 2026 z bundlovaného JSON (data z měsíčních
    excelů kniha jízd) → Jizda + TachometrStav. Spustí se jen jednou (příznak v Nastaveni).
    Vozidla páruje dle SPZ; měsíc přepíše jen pokud pro něj ještě jízdy nemá."""
    import os
    import json
    from datetime import datetime
    from .models import Vozidlo, Jizda, TachometrStav, Nastaveni
    PRIZNAK = "seed_historie_jizd_v1"
    cesta = os.path.join(os.path.dirname(__file__), "data", "kniha_jizd_2026.json")
    try:
        if Nastaveni.query.get(PRIZNAK) or not os.path.exists(cesta):
            return
        with open(cesta, encoding="utf-8") as f:
            zaznamy = json.load(f)
        vozidla = {(v.spz or "").replace(" ", "").upper(): v for v in Vozidlo.query.all()}
        naimport = 0
        for z in zaznamy:
            v = vozidla.get((z["spz"] or "").replace(" ", "").upper())
            if not v:
                continue
            rok, mesic = z["rok"], z["mesic"]
            # přeskoč, pokud už pro tento měsíc jízdy existují (neduplikuj)
            if Jizda.query.filter_by(vozidlo_id=v.id, rok=rok, mesic=mesic).first():
                continue
            # počáteční tachometr auta = začátek nejstaršího importovaného měsíce
            prvni_zac = next((j["zac"] for j in z["jizdy"] if j.get("zac") is not None), None)
            if prvni_zac is not None and (not v.tachometr_pocatek or v.tachometr_pocatek == 0):
                v.tachometr_pocatek = int(prvni_zac)
            if z.get("ridic") and not v.ridic:
                v.ridic = z["ridic"][:120]
            for j in z["jizdy"]:
                d = None
                if j.get("datum"):
                    try:
                        d = datetime.strptime(j["datum"][:10], "%Y-%m-%d").date()
                    except ValueError:
                        pass
                db.session.add(Jizda(vozidlo_id=v.id, rok=rok, mesic=mesic, datum=d,
                                     odkud=(j.get("odkud") or "")[:300], kam=(j.get("kam") or "")[:300],
                                     km=float(j.get("km") or 0),
                                     ucel=(j.get("ucel") or "")[:300], soukroma=False))
            # stav tachometru na konci měsíce
            if z.get("tacho_konec") is not None:
                ts = TachometrStav.query.filter_by(vozidlo_id=v.id, rok=rok, mesic=mesic).first()
                if ts:
                    ts.stav_km = int(z["tacho_konec"])
                else:
                    db.session.add(TachometrStav(vozidlo_id=v.id, rok=rok, mesic=mesic,
                                                 stav_km=int(z["tacho_konec"])))
            naimport += 1
        db.session.add(Nastaveni(klic=PRIZNAK, hodnota="hotovo"))
        db.session.commit()
        print(f"[seed] Historie jízd 2026: naimportováno {naimport} měsíčních záznamů.")
    except Exception as e:
        db.session.rollback()
        print(f"[seed] historie jízd: {e}")


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
                "najem_od": "DATE", "najem_do": "DATE",
                "splatka": "FLOAT", "najezd_limit": "INTEGER",
                "ridic": "VARCHAR(120)",
            },
            "tankovani": {
                "druh": "VARCHAR(60)", "kategorie": "VARCHAR(20) DEFAULT 'phm'",
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
