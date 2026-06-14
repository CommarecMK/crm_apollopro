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


def odpoved_na_dotaz(firma, dotaz):
    """Vrátí {odpoved, zdroje:[{nazev,web_url}], chyba}."""
    from . import embeddings
    dotaz = (dotaz or "").strip()
    if not dotaz:
        return {"odpoved": None, "zdroje": [], "chyba": None}
    if not ma_ai():
        return {"odpoved": None, "zdroje": [], "chyba": "Chybí firemní ANTHROPIC_API_KEY (doplň na Railway)."}
    chunky = embeddings.hledat_relevantni(firma.id, dotaz, top_k=12)
    if not chunky:
        return {"odpoved": "V dokumentech klienta jsem nenašel nic relevantního. Jsou dokumenty načtené do databáze?",
                "zdroje": [], "chyba": None}
    kontext = "\n\n".join(f"[Zdroj {i + 1}: {c['nazev']}]\n{c['text']}" for i, c in enumerate(chunky))
    system = ("Jsi asistent poradenské firmy Commarec. Odpovídej česky, věcně a stručně. "
              "Vycházej VÝHRADNĚ z poskytnutých úryvků z dokumentů klienta. "
              "Pokud odpověď v podkladech není, jasně to napiš. "
              "U klíčových tvrzení odkazuj na zdroj formou (zdroj: název souboru).")
    user = f"Klient: {firma.nazev}\n\nDotaz: {dotaz}\n\nÚryvky z dokumentů klienta:\n{kontext}"
    odp = _claude(system, user)
    zdroje, videno = [], set()
    for c in chunky:
        if c["nazev"] not in videno:
            videno.add(c["nazev"])
            zdroje.append({"nazev": c["nazev"], "web_url": c["web_url"]})
    return {"odpoved": odp or "AI se nepodařilo zavolat.", "zdroje": zdroje[:8], "chyba": None}
