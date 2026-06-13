"""
models.py — datové modely kokpitu.

Zakazka je páteř systému. Mapování na Clockify jde přes `nazev`
(název Clockify projektu = "Klient - Typ služby") a/nebo `zkratka`.
"""
from datetime import datetime
from .extensions import db


class Firma(db.Model):
    __tablename__ = "firma"
    id      = db.Column(db.Integer, primary_key=True)
    nazev   = db.Column(db.String(200), nullable=False, unique=True)
    zakazky = db.relationship("Zakazka", back_populates="firma", lazy=True)


class Zakazka(db.Model):
    __tablename__ = "zakazka"
    id          = db.Column(db.Integer, primary_key=True)
    zkratka     = db.Column(db.String(40), nullable=False, unique=True, index=True)  # např. 02023024_1
    nazev       = db.Column(db.String(300), nullable=False)   # "Canna B2B - Interim"
    typ_sluzby  = db.Column(db.String(60))                    # Interim / Professional / Obaly ...
    stav        = db.Column(db.String(20), default="aktivni") # aktivni / pozastaveno / dokonceno
    datum_od    = db.Column(db.Date, nullable=True)
    datum_do    = db.Column(db.Date, nullable=True)
    rozpocet_hodin = db.Column(db.Float, nullable=True)       # plán hodin (z nabídky), doplníme později

    firma_id    = db.Column(db.Integer, db.ForeignKey("firma.id"), nullable=False)
    firma       = db.relationship("Firma", back_populates="zakazky", lazy="joined")

    # Vyplní se runtime z Clockify (neukládá se do DB)
    @property
    def display_klient(self):
        return self.nazev.split(" - ")[0].strip()

    @property
    def ico(self):
        """IČO firmy = část zkratky před podtržítkem (pro pozdější napojení MERK)."""
        return self.zkratka.split("_")[0] if self.zkratka else ""
