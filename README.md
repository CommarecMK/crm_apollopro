# Kokpit zakázek — Commarec (MVP)

Konverzační kokpit nad zakázkami. Tato verze (MVP) ukazuje **Stav zakázek** —
seznam zakázek s firmou, typem služby a hodinami z Clockify. Postaveno na
Flasku, bezpečnost řešená od začátku (CSRF, klíče jen v `.env`).

## Spuštění na Macu (lokálně)

```bash
cd 02-novy-projekt
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # a vyplň hodnoty (hlavně ADMIN_PASSWORD)
python run.py
```

Otevři http://localhost:5000 → přihlas se heslem z `ADMIN_PASSWORD`.
Při prvním startu se z `data/zakazky.xlsx` naimportuje 54 zakázek (31 firem).

## Napojení Clockify (hodiny)

1. V Clockify: Profile Settings → API → vygeneruj klíč (stačí read-only).
2. Vlož ho do `.env` jako `CLOCKIFY_API_KEY=...` a restartuj.
3. Hodiny se párují na zakázky podle **názvu projektu** ("Klient - Typ služby").
   Pokud se některý projekt nespáruje, doladíme mapování.

> Klíč nikam neukládáme do kódu ani gitu — žije jen v `.env` (lokálně) nebo
> v proměnných na Railway (produkce).

## Struktura

```
run.py                 vstupní bod
app/
  __init__.py          factory: DB, CSRF, seed
  extensions.py        db + env proměnné
  models.py            Firma, Zakazka
  auth.py              přihlášení (MVP), sso.py = připraveno na portál
  services/clockify.py read-only Clockify
  routes/main.py       login + kokpit "Stav zakázek"
templates/             base + login + stav_zakazek (Commarec brand)
data/zakazky.xlsx      zdroj zakázek
```

## Co dál (fáze 2)

- Rozpočty hodin z přijatých nabídek → barevný indikátor přečerpání.
- Poslední aktivita na zakázce (kdy se naposled dělalo).
- Konverzační vrstva: chat „co je v ohrožení", čísla počítaná z dat (tool-calling).
- Cashflow + ziskovost přes ABRA Flexi.
- Nasazení na Railway pod SSO portálu.

---
*Pozn. k fontům: nadpisy používají Druk Condensed Super; pokud není v systému
nainstalovaný, prohlížeč použije Montserrat ExtraBold (vizuálně blízké).*
