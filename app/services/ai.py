"""
services/ai.py — AI odpovědi nad dokumenty klienta (RAG + Claude, s citacemi).
Vyžaduje ANTHROPIC_API_KEY (firemní). Retrieval řeší embeddings.hledat_relevantni.
"""
import os

# Pořadí modelů k vyzkoušení (první funkční se zapamatuje). Lze přebít env ANTHROPIC_MODEL.
# Default = Opus 4.8 (nejchytřejší), fallback na Sonnet.
MODELY = [m for m in [os.environ.get("ANTHROPIC_MODEL"),
                      "claude-opus-4-8", "claude-sonnet-4-6",
                      "claude-sonnet-4-20250514", "claude-haiku-4-5-20251001"] if m]
MODEL = MODELY[0]
_FUNKCNI_MODEL = None


def ma_ai():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _claude(system, user, max_tokens=1500):
    """Vrátí (text, chyba). Zkusí modely z MODELY, první funkční si zapamatuje."""
    global _FUNKCNI_MODEL
    try:
        import anthropic
    except Exception as e:
        return None, f"chybí knihovna anthropic: {e}"
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    poradi = [_FUNKCNI_MODEL] if _FUNKCNI_MODEL else MODELY
    posledni_chyba = None
    for model in poradi:
        try:
            msg = client.messages.create(model=model, max_tokens=max_tokens,
                                         system=system, messages=[{"role": "user", "content": user}])
            _FUNKCNI_MODEL = model
            return msg.content[0].text, None
        except Exception as e:
            posledni_chyba = f"{model}: {e}"
            if "not_found" not in str(e) and "404" not in str(e):
                break  # jiná chyba než neznámý model → nemá smysl zkoušet dál
    print(f"[ai] {posledni_chyba}")
    return None, posledni_chyba


def test_volani():
    """Zkušební volání pro diagnostiku."""
    if not ma_ai():
        return {"ma_klic": False, "model": MODEL}
    txt, chyba = _claude("Odpovídej česky.", "Napiš jen slovo: OK", max_tokens=20)
    return {"ma_klic": True, "model": _FUNKCNI_MODEL or MODEL,
            "zkousene": MODELY, "vystup": txt, "chyba": chyba}


def _parse_json(raw):
    import json
    import re
    if not raw:
        return None
    t = re.sub(r"^```(json)?\s*", "", raw.strip())
    t = re.sub(r"\s*```$", "", t).strip()
    try:
        return json.loads(t)
    except Exception:
        m = re.search(r"\[[\s\S]*\]", t)
        if m:
            try:
                return json.loads(re.sub(r",(\s*[}\]])", r"\1", m.group()))
            except Exception:
                return None
    return None


def navrhni_ukoly(firma, text):
    """Z textu (zápis/dokument/analýza) navrhne konkrétní úkoly + doporučené kroky.
    Vrací {ukoly:[{nazev,popis,termin}], chyba}."""
    text = (text or "").strip()
    if len(text) < 30:
        return {"ukoly": [], "chyba": "Z dokumentu se nepodařilo přečíst text (možná naskenované PDF/obrázek nebo prázdný soubor). Zkus vložit text ručně."}
    if not ma_ai():
        return {"ukoly": [], "chyba": "Chybí firemní ANTHROPIC_API_KEY."}
    # kontext klienta (otevřené úkoly) — ať nenavrhuje duplicity a je v obraze
    freelo_ctx = _freelo_kontext(firma)
    system = ("Jsi zkušený projektový konzultant logistiky ve firmě Commarec. Z dodaného dokumentu "
              "(zápis z jednání, analýza dat, report, nabídka) navrhni KONKRÉTNÍ úkoly a doporučené další kroky "
              "pro klienta. Buď chytrý a proaktivní: i z analytického/datového dokumentu odvoď smysluplné akce "
              "(např. ověřit kapacitu regálů dle dat, připravit návrh layoutu, doplnit chybějící podklad). "
              "Nevymýšlej si fakta. Vyhni se úkolům, které už jsou mezi otevřenými. "
              "Vrať POUZE JSON pole objektů ve tvaru "
              '[{"nazev":"krátký název","popis":"1–2 věty co a proč udělat","termin":"YYYY-MM-DD nebo null"}]. '
              "Žádný jiný text. Max 15 úkolů, jen reálné akce. Pokud opravdu nic akčního není, vrať [].")
    user = f"Klient: {firma.nazev}\n\n"
    if freelo_ctx:
        user += f"Už otevřené úkoly (nenavrhuj duplicity):\n{freelo_ctx[:2000]}\n\n"
    user += f"Dokument:\n{text[:14000]}"
    odp, chyba = _claude(system, user, max_tokens=2500)
    if not odp:
        return {"ukoly": [], "chyba": f"AI chyba: {chyba}"}
    data = _parse_json(odp)
    if not isinstance(data, list):
        return {"ukoly": [], "chyba": "AI nevrátila platný seznam úkolů. Zkus to prosím znovu."}
    ukoly = []
    for u in data:
        if isinstance(u, dict) and u.get("nazev"):
            ukoly.append({"nazev": str(u.get("nazev"))[:200],
                          "popis": str(u.get("popis") or "")[:1000],
                          "termin": (u.get("termin") or "")[:10] if u.get("termin") else ""})
    return {"ukoly": ukoly, "chyba": None}


def navrhni_slozku(filename, cesty):
    """Navrhne, do které složky (z `cesty`) soubor patří. Vrací cestu (str) nebo ''."""
    if not filename or not cesty:
        return ""
    if not ma_ai():
        return ""
    seznam = "\n".join(cesty[:80])
    system = ("Vyber NEJVHODNĚJŠÍ složku pro daný soubor podle jeho názvu. "
              "Odpověz POUZE přesnou cestou složky ze seznamu, nic jiného. Když si nejsi jistý, vrať '/ (kořen)'.")
    odp, _ = _claude(system, f"Soubor: {filename}\n\nSložky:\n{seznam}", max_tokens=60)
    odp = (odp or "").strip().splitlines()[0].strip() if odp else ""
    return odp if odp in cesty else ""


def _freelo_kontext(firma):
    """Textový souhrn úkolů klienta z Freela (pro AI) + zda jsou nějaké."""
    from . import freelo
    if not firma.freelo_tasklist_id:
        return ""
    try:
        data = freelo.ukoly_klienta(firma.freelo_tasklist_id)
    except Exception:
        return ""
    radky = []
    for u in data.get("aktivni", []):
        cast = [f"- {u['nazev']}", f"řešitel: {u['resitel'] or '—'}"]
        if u.get("termin"):
            cast.append(f"termín: {u['termin']}")
        if u.get("dni_od_iterace") is not None:
            cast.append(f"poslední reakce před {u['dni_od_iterace']} dny")
        radky.append(", ".join(cast))
    if not radky and not data.get("hotove"):
        return ""
    hotovo = len(data.get("hotove", []))
    return (f"OTEVŘENÉ ÚKOLY VE FREELU ({len(radky)}), hotových {hotovo}:\n" + "\n".join(radky)) if radky \
        else f"Žádné otevřené úkoly ve Freelu (hotových {hotovo})."


def odpoved_na_dotaz(firma, dotaz):
    """Vrátí {odpoved, zdroje:[{nazev,web_url}], chyba}. Kontext = dokumenty (RAG) + Freelo úkoly."""
    from . import embeddings
    dotaz = (dotaz or "").strip()
    if not dotaz:
        return {"odpoved": None, "zdroje": [], "chyba": None}
    if not ma_ai():
        return {"odpoved": None, "zdroje": [], "chyba": "Chybí firemní ANTHROPIC_API_KEY (doplň na Railway)."}
    chunky = embeddings.hledat_relevantni(firma.id, dotaz, top_k=12)
    freelo_ctx = _freelo_kontext(firma)
    if not chunky and not freelo_ctx:
        return {"odpoved": "Nemám k tomuto klientovi žádná data — načti dokumenty do databáze a/nebo napoj Freelo.",
                "zdroje": [], "chyba": None}
    casti = []
    if freelo_ctx:
        casti.append("=== ÚKOLY (FREELO) ===\n" + freelo_ctx)
    if chunky:
        casti.append("=== DOKUMENTY ===\n" + "\n\n".join(
            f"[Zdroj {i + 1}: {c['nazev']}]\n{c['text']}" for i, c in enumerate(chunky)))
    system = ("Jsi asistent poradenské firmy Commarec. Odpovídej česky, věcně a stručně. "
              "Vycházej VÝHRADNĚ z poskytnutých podkladů (úkoly z Freela + úryvky z dokumentů klienta). "
              "Můžeš kombinovat obojí. Pokud odpověď v podkladech není, jasně to napiš. "
              "U klíčových tvrzení z dokumentů odkazuj na zdroj (název souboru). "
              "Na úplný konec přidej na samostatný řádek oddělovač '===NAVRHY===' a pod něj 3 stručné "
              "návazné otázky (každou na svůj řádek, bez číslování), které dávají smysl k prohloubení tématu u tohoto klienta.")
    user = f"Klient: {firma.nazev}\n\nDotaz: {dotaz}\n\n" + "\n\n".join(casti)
    odp, chyba = _claude(system, user, max_tokens=1800)
    if not odp:
        return {"odpoved": None, "zdroje": [], "navrhy": [], "chyba": f"AI se nepodařilo zavolat: {chyba or 'neznámá chyba'}"}
    navrhy = []
    if "===NAVRHY===" in odp:
        odp, raw = odp.split("===NAVRHY===", 1)
        for line in raw.strip().splitlines():
            q = line.strip().lstrip("0123456789.-•* ").strip()
            if len(q) > 4:
                navrhy.append(q)
        odp = odp.strip()
    zdroje, videno = [], set()
    for c in chunky:
        if c["nazev"] not in videno:
            videno.add(c["nazev"])
            zdroje.append({"nazev": c["nazev"], "web_url": c["web_url"]})
    return {"odpoved": odp, "zdroje": zdroje[:8], "navrhy": navrhy[:3], "chyba": None}
