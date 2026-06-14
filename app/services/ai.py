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
              "U klíčových tvrzení z dokumentů odkazuj na zdroj (název souboru).")
    user = f"Klient: {firma.nazev}\n\nDotaz: {dotaz}\n\n" + "\n\n".join(casti)
    odp, chyba = _claude(system, user)
    if not odp:
        return {"odpoved": None, "zdroje": [], "chyba": f"AI se nepodařilo zavolat: {chyba or 'neznámá chyba'}"}
    zdroje, videno = [], set()
    for c in chunky:
        if c["nazev"] not in videno:
            videno.add(c["nazev"])
            zdroje.append({"nazev": c["nazev"], "web_url": c["web_url"]})
    return {"odpoved": odp, "zdroje": zdroje[:8], "chyba": None}
