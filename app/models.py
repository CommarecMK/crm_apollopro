"""
models.py — datové modely kokpitu.

Zakazka je páteř systému. Mapování na Clockify jde přes `zkratka`
(uloženou v poli Note u Clockify klienta). Firma se obohacuje z MERK/ARES podle IČO.
"""
from datetime import datetime
from .extensions import db


class Firma(db.Model):
    __tablename__ = "firma"
    id      = db.Column(db.Integer, primary_key=True)
    nazev   = db.Column(db.String(200), nullable=False, unique=True)
    typ_subjektu = db.Column(db.String(20), default="klient")  # klient | dodavatel
    # Obohacení z MERK / ARES (podle IČO)
    ico          = db.Column(db.String(20), index=True)
    dic          = db.Column(db.String(20))
    adresa       = db.Column(db.String(300))
    web          = db.Column(db.String(200))
    obor         = db.Column(db.String(200))   # NACE / industry
    zamestnanci  = db.Column(db.String(80))    # rozsah z MERK
    obrat        = db.Column(db.String(80))     # rozsah z MERK
    merk_nacteno = db.Column(db.String(40))    # datum posledního natažení
    projektovy_manazer = db.Column(db.String(120))  # PM klienta (dědí se na zakázky)
    freelo_tasklist_id = db.Column(db.Integer, nullable=True)  # napojení na Freelo tasklist
    rucne_upraveno = db.Column(db.Boolean, default=False)  # zámek proti přepisu z MERK
    aktivni      = db.Column(db.Boolean, default=True)     # aktivní / neaktivní klient
    onedrive_odkaz = db.Column(db.String(800), nullable=True)  # odkaz na složku klienta v OneDrive/SharePoint
    dok_index_bezi = db.Column(db.Boolean, default=False)      # probíhá indexace dokumentů na pozadí
    dok_index_progress = db.Column(db.String(60), nullable=True)  # postup, např. "12 / 80"

    zakazky  = db.relationship("Zakazka", back_populates="firma", lazy=True)
    kontakty = db.relationship("Kontakt", back_populates="firma", lazy=True,
                               cascade="all, delete-orphan")

    @property
    def je_interni(self):
        from .extensions import COMPANY_ICO
        return (self.ico or "") == COMPANY_ICO


class Kontakt(db.Model):
    __tablename__ = "kontakt"
    id        = db.Column(db.Integer, primary_key=True)
    jmeno     = db.Column(db.String(160))
    email     = db.Column(db.String(160))
    telefon   = db.Column(db.String(60))
    pozice    = db.Column(db.String(120))
    zdroj     = db.Column(db.String(20), default="rucne")  # merk / rucne
    rucne_upraveno = db.Column(db.Boolean, default=False)  # zámek proti přepisu z MERK
    firma_id  = db.Column(db.Integer, db.ForeignKey("firma.id"), nullable=False)
    firma     = db.relationship("Firma", back_populates="kontakty", lazy="joined")


class User(db.Model):
    """Uživatel kokpitu. Role: admin / editor / majitel."""
    __tablename__ = "uzivatel"
    id            = db.Column(db.Integer, primary_key=True)
    sso_id        = db.Column(db.Integer, unique=True, nullable=True)  # id z portálu (SSO)
    jmeno         = db.Column(db.String(120))
    email         = db.Column(db.String(160), unique=True, nullable=True)
    password_hash = db.Column(db.String(255), nullable=True)
    role          = db.Column(db.String(20), default="majitel")  # admin | editor | majitel
    aktivni       = db.Column(db.Boolean, default=True)
    # Vlastní Freelo přístup (zápisy pod správným autorem). Klíč je osobní tajemství.
    freelo_email   = db.Column(db.String(160), nullable=True)
    freelo_api_key = db.Column(db.String(255), nullable=True)

    @property
    def ma_freelo(self):
        return bool(self.freelo_email and self.freelo_api_key)

    @property
    def smi_zakazky(self):
        return self.role in ("admin", "editor")

    @property
    def je_admin(self):
        return self.role == "admin"


class Faktura(db.Model):
    """Jednotlivá faktura / nárazový prodej u zakázky typu 'jednorazovy'."""
    __tablename__ = "faktura"
    id         = db.Column(db.Integer, primary_key=True)
    zakazka_id = db.Column(db.Integer, db.ForeignKey("zakazka.id"), nullable=False)
    datum      = db.Column(db.Date, nullable=False)
    castka     = db.Column(db.Float, nullable=False, default=0)
    popis      = db.Column(db.String(200))


class Snapshot(db.Model):
    """Denní snímek dat z Clockify (JSON). Stránky čtou odtud → rychlé načítání."""
    __tablename__ = "snapshot"
    id      = db.Column(db.Integer, primary_key=True)
    updated = db.Column(db.String(40))
    data    = db.Column(db.Text)


class FreeloSnapshot(db.Model):
    """Denní snímek úkolů z Freela (JSON). Operativa čte odtud → rychlé a 100% přesné."""
    __tablename__ = "freelo_snapshot"
    id      = db.Column(db.Integer, primary_key=True)
    updated = db.Column(db.String(40))
    data    = db.Column(db.Text)


class KlientDokument(db.Model):
    """Indexovaný dokument klienta z OneDrive — extrahovaný text + metadata (pro hledání a AI)."""
    __tablename__ = "klient_dokument"
    id        = db.Column(db.Integer, primary_key=True)
    firma_id  = db.Column(db.Integer, db.ForeignKey("firma.id"), nullable=False, index=True)
    item_id   = db.Column(db.String(255), index=True)   # OneDrive driveItem id
    drive_id  = db.Column(db.String(255))
    nazev     = db.Column(db.String(400))
    cesta     = db.Column(db.String(800))               # podsložka v rámci klienta
    web_url   = db.Column(db.String(800))
    velikost  = db.Column(db.Integer, default=0)
    text      = db.Column(db.Text)                      # extrahovaný text
    updated   = db.Column(db.String(40))
    firma     = db.relationship("Firma")
    chunky    = db.relationship("DokumentChunk", backref="dokument", lazy=True,
                                cascade="all, delete-orphan")


class DokumentChunk(db.Model):
    """Část dokumentu (chunk) + embedding (JSON) pro sémantické hledání nad dokumenty klienta."""
    __tablename__ = "dokument_chunk"
    id          = db.Column(db.Integer, primary_key=True)
    dokument_id = db.Column(db.Integer, db.ForeignKey("klient_dokument.id"), nullable=False, index=True)
    firma_id    = db.Column(db.Integer, index=True)
    nazev       = db.Column(db.String(400))   # název zdrojového souboru (pro citaci)
    web_url     = db.Column(db.String(800))
    pozice      = db.Column(db.Integer, default=0)
    text        = db.Column(db.Text, nullable=False)
    embedding   = db.Column(db.Text)          # JSON list floatů (z OpenAI), nebo None


class Zakazka(db.Model):
    __tablename__ = "zakazka"
    id          = db.Column(db.Integer, primary_key=True)
    zkratka     = db.Column(db.String(40), nullable=False, unique=True, index=True)  # např. 02023024_1
    nazev       = db.Column(db.String(300), nullable=False)   # "Canna B2B - Interim"
    typ_sluzby  = db.Column(db.String(60))                    # Interim / Professional / Obaly ...
    stav        = db.Column(db.String(20), default="aktivni") # ponecháno pro detail (fáze)
    aktivni     = db.Column(db.Boolean, default=True)         # aktivní / neaktivní zakázka
    datum_od    = db.Column(db.Date, nullable=True)
    datum_do    = db.Column(db.Date, nullable=True)

    # ── Rozpočet / fakturační model ──────────────────────────────
    projektovy_manazer = db.Column(db.String(120), nullable=True)  # PM zakázky
    typ_rozpoctu   = db.Column(db.String(20), default="projektovy")  # mesicni | projektovy | analyza
    hodinova_sazba = db.Column(db.Float, nullable=True)      # Kč/h
    rozpocet_hodin       = db.Column(db.Float, nullable=True)  # projektový rozpočet hodin
    rozpocet_hodin_mesic = db.Column(db.Float, nullable=True)  # (legacy) měsíční rozpočet hodin
    rozpocet_hodin_tyden = db.Column(db.Float, nullable=True)  # týdenní rozpočet hodin (interim)
    budget_castka  = db.Column(db.Float, nullable=True)      # pevná částka (projekt) / cena analýzy
    analyza_zaloha    = db.Column(db.Boolean, default=False)  # uhrazeno 40 % předem
    analyza_odevzdano = db.Column(db.Boolean, default=False)  # odevzdáno → doplatek 60 %

    firma_id    = db.Column(db.Integer, db.ForeignKey("firma.id"), nullable=False)
    firma       = db.relationship("Firma", back_populates="zakazky", lazy="joined")
    faktury     = db.relationship("Faktura", backref="zakazka", lazy=True,
                                  cascade="all, delete-orphan",
                                  order_by="Faktura.datum.desc()")

    @property
    def display_klient(self):
        return self.nazev.split(" - ")[0].strip()

    @property
    def ico(self):
        """IČO firmy = část zkratky před podtržítkem (klíč pro MERK/ARES)."""
        return self.zkratka.split("_")[0] if self.zkratka else ""

    @property
    def je_interni(self):
        from .extensions import COMPANY_ICO
        return self.ico == COMPANY_ICO

    @property
    def je_aktivni(self):
        """Efektivní aktivita: zakázka je aktivní jen když je aktivní i klient."""
        return bool(self.aktivni and (self.firma.aktivni if self.firma else True))

    @property
    def efekt_pm(self):
        """PM zakázky: vlastní (override), jinak PM klienta."""
        return (self.projektovy_manazer or "").strip() or \
               ((self.firma.projektovy_manazer or "").strip() if self.firma else "")

    @property
    def _mesicu(self):
        """Počet měsíců zvoleného období (1 pro měsíc, N pro rok). Nastaví route."""
        return getattr(self, "_pocet_mesicu", 1) or 1

    @property
    def rozpocet_hodin_efekt(self):
        """Rozpočet hodin za zvolené období. Měsíční × počet měsíců; u projektového
        buď zadaný rozpočet hodin, nebo dopočet z pevné částky ÷ hodinová sazba.
        U jednorázové fakturace rozpočet hodin není (hodiny = náklad)."""
        if self.typ_rozpoctu == "jednorazovy":
            return None
        if self.typ_rozpoctu == "mesicni":
            w = self.rozpocet_hodin_tyden or 0
            return round(w * getattr(self, "_tydny", 0), 1) if w else None
        if self.rozpocet_hodin:
            return self.rozpocet_hodin
        if self.budget_castka and self.hodinova_sazba:
            return round(self.budget_castka / self.hodinova_sazba, 1)
        return None

    @property
    def efekt_sazba(self):
        """Efektivní sazba Kč/h. Když není zadaná hodinová sazba, ale u projektového je
        pevná částka + odhad hodin, dopočítá se jako částka ÷ rozpočet hodin (odkrajuje z částky)."""
        if self.typ_rozpoctu == "jednorazovy":
            return 0  # tržba je z faktur, ne z hodin (hodiny = náklad)
        if self.hodinova_sazba:
            return self.hodinova_sazba
        if self.typ_rozpoctu == "projektovy" and self.budget_castka and self.rozpocet_hodin:
            return self.budget_castka / self.rozpocet_hodin
        return 0

    @property
    def trzba_plan(self):
        """Plánovaná tržba za zvolené období. Měsíční × počet měsíců, jinak celková (fond)."""
        if self.typ_rozpoctu == "jednorazovy":
            return 0  # nárazové prodeje neplánujeme
        if self.typ_rozpoctu == "mesicni":
            return (self.rozpocet_hodin_tyden or 0) * getattr(self, "_tydny", 0) * (self.hodinova_sazba or 0)
        if self.typ_rozpoctu == "analyza":
            return self.budget_castka or 0
        return self.budget_castka if self.budget_castka else (self.rozpocet_hodin or 0) * (self.hodinova_sazba or 0)

    @property
    def trzba_skutecnost(self):
        """Skutečná (vyčerpaná) tržba = FAKTUROVATELNÉ hodiny × efektivní sazba.
        U projektového s pevnou částkou se odkrajuje z částky (max do její výše).
        U analýzy dle milníků 40/60."""
        if self.typ_rozpoctu == "jednorazovy":
            return getattr(self, "_faktury", 0) or 0  # součet faktur za období (nastaví route)
        if self.typ_rozpoctu == "analyza":
            c = self.budget_castka or 0
            return (0.4 if self.analyza_zaloha else 0) * c + (0.6 if self.analyza_odevzdano else 0) * c
        hod_bill = getattr(self, "hodiny_bill", 0) or 0
        t = hod_bill * self.efekt_sazba
        if self.typ_rozpoctu == "projektovy" and self.budget_castka:
            return min(t, self.budget_castka)   # nepřekročí pevnou částku
        return t

    @property
    def nenaplneny_potencial(self):
        """Kolik z plánu ještě chybí (plán − skutečnost), min. 0."""
        return max((self.trzba_plan or 0) - (self.trzba_skutecnost or 0), 0)
