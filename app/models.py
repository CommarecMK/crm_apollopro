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
    rucne_upraveno = db.Column(db.Boolean, default=False)  # zámek proti přepisu z MERK
    aktivni      = db.Column(db.Boolean, default=True)     # aktivní / neaktivní klient

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

    @property
    def smi_zakazky(self):
        return self.role in ("admin", "editor")

    @property
    def je_admin(self):
        return self.role == "admin"


class Snapshot(db.Model):
    """Denní snímek dat z Clockify (JSON). Stránky čtou odtud → rychlé načítání."""
    __tablename__ = "snapshot"
    id      = db.Column(db.Integer, primary_key=True)
    updated = db.Column(db.String(40))
    data    = db.Column(db.Text)


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
    rozpocet_hodin_mesic = db.Column(db.Float, nullable=True)  # měsíční rozpočet hodin
    budget_castka  = db.Column(db.Float, nullable=True)      # pevná částka (projekt) / cena analýzy
    analyza_zaloha    = db.Column(db.Boolean, default=False)  # uhrazeno 40 % předem
    analyza_odevzdano = db.Column(db.Boolean, default=False)  # odevzdáno → doplatek 60 %

    firma_id    = db.Column(db.Integer, db.ForeignKey("firma.id"), nullable=False)
    firma       = db.relationship("Firma", back_populates="zakazky", lazy="joined")

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
    def _mesicu(self):
        """Počet měsíců zvoleného období (1 pro měsíc, N pro rok). Nastaví route."""
        return getattr(self, "_pocet_mesicu", 1) or 1

    @property
    def rozpocet_hodin_efekt(self):
        """Rozpočet hodin za zvolené období. Měsíční × počet měsíců; u projektového
        buď zadaný rozpočet hodin, nebo dopočet z pevné částky ÷ hodinová sazba."""
        if self.typ_rozpoctu == "mesicni":
            return (self.rozpocet_hodin_mesic or 0) * self._mesicu if self.rozpocet_hodin_mesic else None
        if self.rozpocet_hodin:
            return self.rozpocet_hodin
        if self.budget_castka and self.hodinova_sazba:
            return round(self.budget_castka / self.hodinova_sazba, 1)
        return None

    @property
    def efekt_sazba(self):
        """Efektivní sazba Kč/h. Když není zadaná hodinová sazba, ale u projektového je
        pevná částka + odhad hodin, dopočítá se jako částka ÷ rozpočet hodin (odkrajuje z částky)."""
        if self.hodinova_sazba:
            return self.hodinova_sazba
        if self.typ_rozpoctu == "projektovy" and self.budget_castka and self.rozpocet_hodin:
            return self.budget_castka / self.rozpocet_hodin
        return 0

    @property
    def trzba_plan(self):
        """Plánovaná tržba za zvolené období. Měsíční × počet měsíců, jinak celková (fond)."""
        if self.typ_rozpoctu == "mesicni":
            return (self.rozpocet_hodin_mesic or 0) * self._mesicu * (self.hodinova_sazba or 0)
        if self.typ_rozpoctu == "analyza":
            return self.budget_castka or 0
        return self.budget_castka if self.budget_castka else (self.rozpocet_hodin or 0) * (self.hodinova_sazba or 0)

    @property
    def trzba_skutecnost(self):
        """Skutečná (vyčerpaná) tržba = FAKTUROVATELNÉ hodiny × efektivní sazba.
        U projektového s pevnou částkou se odkrajuje z částky (max do její výše).
        U analýzy dle milníků 40/60."""
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
