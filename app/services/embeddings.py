"""
services/embeddings.py — RAG nad dokumenty klienta.
Chunking (z Brainu) + embeddingy z OpenAI (text-embedding-3-small), uložené jako JSON.
Podobnost se počítá v Pythonu (numpy) — scope je jeden klient, takže to stačí a je to jednoduché.
"""
import os
import re
import json

CHUNK_SIZE_SMALL = 1500
CHUNK_SIZE_MEDIUM = 3000
CHUNK_SIZE_LARGE = 6000
CHUNK_OVERLAP = 200
MAX_CHUNKS_PER_DOC = 150
EMB_MODEL = "text-embedding-3-small"


def ma_embeddings():
    return bool(os.environ.get("OPENAI_API_KEY"))


def rozdelit_na_chunky(text, nazev=""):
    if not text or not text.strip():
        return []
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    text = re.sub(r" {3,}", "  ", text)
    delka = len(text)
    chunk_size = CHUNK_SIZE_LARGE if delka > 200000 else CHUNK_SIZE_MEDIUM if delka > 50000 else CHUNK_SIZE_SMALL
    chunks, start = [], 0
    while start < len(text):
        end = start + chunk_size
        if end < len(text):
            for sep in ["\n\n", "\n", ". ", " "]:
                idx = text.rfind(sep, start + chunk_size // 2, end)
                if idx > start:
                    end = idx + len(sep)
                    break
        kus = text[start:end].strip()
        if kus and len(kus) > 50:
            chunks.append(kus)
        start = end - CHUNK_OVERLAP
        if start >= len(text):
            break
    if len(chunks) > MAX_CHUNKS_PER_DOC:
        step = len(chunks) / MAX_CHUNKS_PER_DOC
        chunks = [chunks[int(i * step)] for i in range(MAX_CHUNKS_PER_DOC)]
    return chunks


def vytvorit_embedding(text):
    if not ma_embeddings():
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        resp = client.embeddings.create(model=EMB_MODEL, input=text[:8000])
        return resp.data[0].embedding
    except Exception as e:
        print(f"[embeddings] {e}")
        return None


def vytvorit_embeddings_davka(texty):
    """Hromadně (šetří dotazy). Vrátí list embeddingů (nebo None při chybě)."""
    if not ma_embeddings() or not texty:
        return [None] * len(texty)
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        resp = client.embeddings.create(model=EMB_MODEL, input=[t[:8000] for t in texty])
        return [d.embedding for d in resp.data]
    except Exception as e:
        print(f"[embeddings] davka: {e}")
        return [None] * len(texty)


def reindex_dokument(dok):
    """Smaže staré chunky dokumentu a vytvoří nové vč. embeddingů. Vrací počet chunků."""
    from ..extensions import db
    from ..models import DokumentChunk
    DokumentChunk.query.filter_by(dokument_id=dok.id).delete()
    kusy = rozdelit_na_chunky(dok.text or "", dok.nazev or "")
    if not kusy:
        return 0
    embs = vytvorit_embeddings_davka(kusy)
    for i, (kus, emb) in enumerate(zip(kusy, embs)):
        db.session.add(DokumentChunk(
            dokument_id=dok.id, firma_id=dok.firma_id, nazev=dok.nazev, web_url=dok.web_url,
            pozice=i, text=kus, embedding=json.dumps(emb) if emb is not None else None))
    return len(kusy)


def hledat_relevantni(firma_id, dotaz, top_k=12):
    """Vrátí top-K relevantních chunků [{text, nazev, web_url}] pro dotaz.
    Sémanticky (embeddingy) když jsou k dispozici, jinak fulltext."""
    from ..models import DokumentChunk
    chunky = DokumentChunk.query.filter_by(firma_id=firma_id).all()
    if not chunky:
        return []
    q_emb = vytvorit_embedding(dotaz)
    s_emby = [c for c in chunky if c.embedding]
    if q_emb is not None and s_emby:
        import numpy as np
        q = np.array(q_emb, dtype="float32")
        qn = q / (np.linalg.norm(q) + 1e-9)
        skore = []
        for c in s_emby:
            try:
                v = np.array(json.loads(c.embedding), dtype="float32")
                skore.append((float(np.dot(v / (np.linalg.norm(v) + 1e-9), qn)), c))
            except Exception:
                continue
        skore.sort(key=lambda x: -x[0])
        return [{"text": c.text, "nazev": c.nazev, "web_url": c.web_url} for _, c in skore[:top_k]]
    # Fulltext fallback
    slova = [s.lower() for s in re.findall(r"\w{3,}", dotaz)]
    scored = []
    for c in chunky:
        tl = c.text.lower()
        sk = sum(1 for s in slova if s in tl)
        if sk:
            scored.append((sk, c))
    scored.sort(key=lambda x: -x[0])
    vyber = [c for _, c in scored[:top_k]] or chunky[:top_k]
    return [{"text": c.text, "nazev": c.nazev, "web_url": c.web_url} for c in vyber]
