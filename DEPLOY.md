# Nasazení kokpitu — přepis CRM repozitáře

Varianta: **nahradíme obsah stávajícího CRM repa** kódem kokpitu. Služba
`crm_apollopro.io` na Railway se sama přestaví a zdědí svoje proměnné i doménu.
Čas: ~10 minut.

---

## KROK 0 — Pojistka (2 min)

V Railway → projekt `apollo_v1` → klikni na **PostgreSQL** → **Backups** →
vytvoř zálohu. Databáze je sdílená, tak ať máš návrat.

---

## KROK 1 — Zjisti správné repo (1 min)

Railway → služba **crm_apollopro.io** → **Settings → Source**.
Poznamenej si název repa (např. `CommarecMK/apollopro_v01`). Ten budeme přepisovat.

---

## KROK 2 — Přepiš obsah repa (5 min)

1. Otevři ten repozitář na **github.com**.
2. Nejjednodušší cesta přes web:
   - Smaž staré soubory: u každého souboru/složky **„…" → Delete**, nebo
   - (rychleji) přejmenuj staré přes commit a nahraj nové.
   > Pohodlnější varianta: pošli mi, jestli máš GitHub Desktop nebo umíš `git` —
   > dám ti přesné příkazy na čistý přepis jedním commitem.
3. **Add file → Upload files** → otevři složku `02-novy-projekt`, označ **vše uvnitř**
   (Cmd+A) a přetáhni.
4. Dole **Commit changes**.

`.env` se nenahraje (je v `.gitignore`) — správně, tajné údaje jsou v Railway.

---

## KROK 3 — Doplň jednu proměnnou (2 min)

Služba už má `DATABASE_URL`, `SSO_SECRET`, `PORTAL_URL`, `SECRET_KEY`,
`ANTHROPIC_API_KEY`, `ADMIN_PASSWORD`. Chybí jen:

| Proměnná | Hodnota |
|---|---|
| `CLOCKIFY_API_KEY` | tvůj read-only klíč z Clockify (Profile → API) |

Přidej ji (Variables → New Variable). Pozor: `FLASK_DEBUG` nech vypnuté/smazané.

---

## KROK 4 — Ověř (1 min)

Po přestavění otevři `https://crm.apollopro.io` (přes portál nebo přímo
`/login`). Měl bys vidět kokpit „Stav zakázek" s 54 zakázkami.

---

## Co se stalo s databází

- Kokpit přidal tabulky `firma` a `zakazka` a naseil 54 zakázek.
- Staré tabulky zápisů (`zapis`, `projekt`…) zůstaly nedotčené, jen se nepoužívají.
- Uživatelská tabulka (přihlášení) beze změny.

## Když build spadne

Deployments → otevři poslední deploy → zkopíruj logy a pošli mi je.
Nejčastější: chybí závislost (vyřeší `requirements.txt`, který je součástí)
nebo se nepřipojí DB (zkontroluj `DATABASE_URL`).
