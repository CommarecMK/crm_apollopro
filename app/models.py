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
    # Obohacení z MERK / ARES (podle IČO)
    ico          = db.Column(db.String(20), index=True)
    dic          = db.Column(db.String(20))
    adresa       = db.Column(db.String(300))
    web          = db.Column(db.String(200))
    obor         = db.Column(db.String(200))   # NACE / industry
    zamestnanci  = db.Column(db.String(80))    # rozsah z MERK
    obrat        = db.Column(db.String(80))     # rozsah z MERK
    merk_nacteno = db.Column(db.String(40))    # datum posledního natažení

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
    firma_id  = db.Column(db.Integer, db.ForeignKey("firma.id"), nullable=False)
    firma     = db.relationship("Firma", back_populates="kontakty", lazy="joined")


class Zakazka(db.Model):
    __tablename__ = "zakazka"
    id          = db.Column(db.Integer, primary_key=True)
    zkratka     = db.Column(db.String(40), nullable=False, unique=True, index=True)  # např. 02023024_1
    nazev       = db.Column(db.String(300), nullable=False)   # "Canna B2B - Interim"
    typ_sluzby  = db.Column(db.String(60))                    # Interim / Professional / Obaly ...
    stav        = db.Column(db.String(20), default="aktivni") # aktivni / pozastaveno / dokonceno
    datum_od    = db.Column(db.Date, nullable=True)
    datum_do    = db.Column(db.Date, nullable=True)
    rozpocet_hodin = db.Column(db.Float, nullable=True)       # plán hodin (z nabídky)

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
