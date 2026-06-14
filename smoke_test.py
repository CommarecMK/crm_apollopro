"""
smoke_test.py — rychlá kontrola, že se appka spustí a všechny stránky se načtou.
Spuštění:  python smoke_test.py
Když něco vrátí chybu (>=500), test selže a vypíše kde.
"""
import os
import re
import tempfile

# Testovací prostředí (dočasná DB, ať nezasáhne ostrá data)
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(tempfile.gettempdir(), "smoke.db"))
os.environ.setdefault("ADMIN_EMAIL", "smoke@test.cz")
os.environ.setdefault("ADMIN_PASSWORD", "smoke")
os.environ.setdefault("SECRET_KEY", "smoke")

from app import create_app  # noqa: E402
from app.models import Zakazka  # noqa: E402

app = create_app()
c = app.test_client()


def _tok():
    return re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1)


c.post("/login", data={"email": "smoke@test.cz", "heslo": "smoke", "csrf_token": _tok()})
with app.app_context():
    fid = Zakazka.query.first().firma_id
    zid = Zakazka.query.first().id

CESTY = ["/", "/zakazky", "/firmy", f"/firmy/{fid}", f"/zakazka/{zid}",
         "/interni", "/cashflow", "/pm", "/pm/— bez PM —",
         "/operativa", f"/operativa/{fid}", "/admin", "/admin/uzivatele",
         "/operativa/ukol/1", "/operativa/resitele", "/muj-ucet", "/operativa/dokumenty"]

chyby = []
for p in CESTY:
    kod = c.get(p).status_code
    znak = "OK " if kod < 400 else "!! "
    print(f"{znak}{kod}  {p}")
    if kod >= 500:
        chyby.append(p)

if chyby:
    print("\nSELHALO:", chyby)
    raise SystemExit(1)
print("\nVše OK — můžeš nasadit.")
