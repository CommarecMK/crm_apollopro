"""
services/ai.py — AI odpovědi nad dokumenty klienta (RAG + Claude, s citacemi).
Vyžaduje ANTHROPIC_API_KEY (firemní). Retrieval řeší embeddings.hledat_relevantni.
"""
import os

MODEL = "claude-sonnet-4-20250514"


def ma_ai():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _claude(system, user, max_tokens=1500):
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        msg = client.messages.create(model=MODEL, max_tokens=max_tokens,
                                     system=system, messages=[{"role": "user", "content": user}])
        return msg.content[0].text
    except Exception as e:
        print(f"[ai] {e}")
        return None


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
    odp = _claude(system, user)
    zdroje, videno = [], set()
    for c in chunky:
        if c["nazev"] not in videno:
            videno.add(c["nazev"])
            zdroje.append({"nazev": c["nazev"], "web_url": c["web_url"]})
    return {"odpoved": odp or "AI se nepodařilo zavolat.", "zdroje": zdroje[:8], "chyba": None}
