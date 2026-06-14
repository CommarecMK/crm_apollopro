"""
services/freelo.py — napojení na Freelo (úkoly klienta).
Auth: HTTP Basic (FREELO_EMAIL + FREELO_API_KEY). Read-only použití.
Klient ↔ Freelo tasklist (Firma.freelo_tasklist_id).
"""
import requests
from ..extensions import FREELO_EMAIL, FREELO_API_KEY

BASE = "https://api.freelo.io/v1"
TIMEOUT = 20


def je_nakonfigurovano():
    return bool(FREELO_EMAIL and FREELO_API_KEY)


def _get(path, params=None):
    return requests.get(f"{BASE}{path}", auth=(FREELO_EMAIL, FREELO_API_KEY),
                        headers={"Content-Type": "application/json"}, params=params, timeout=TIMEOUT)


def seznam_tasklistu():
    """Vrátí [(id, 'Projekt — Tasklist')] ze všech Freelo projektů (pro výběr u klienta)."""
    if not je_nakonfigurovano():
        return []
    try:
        r = _get("/projects")
        if r.status_code != 200:
            return []
        raw = r.json()
        projekty = raw if isinstance(raw, list) else raw.get("data", raw.get("projects", []))
        out = []
        for p in projekty:
            pnaz = p.get("name", "")
            for tl in p.get("tasklists", []):
                out.append((tl.get("id"), f"{pnaz} — {tl.get('name', '')}"))
        return out
    except Exception as e:
        print(f"[freelo] tasklisty: {e}")
        return []


def projekty_s_tasklisty():
    """Vrátí [{id, nazev, tasklisty:[{id,nazev}]}] pro dvoukrokový výběr (projekt → tasklist)."""
    if not je_nakonfigurovano():
        return []
    try:
        r = _get("/projects")
        if r.status_code != 200:
            return []
        raw = r.json()
        projekty = raw if isinstance(raw, list) else raw.get("data", raw.get("projects", []))
        out = []
        for p in projekty:
            tl = [{"id": t.get("id"), "nazev": t.get("name", "")} for t in p.get("tasklists", [])]
            if tl:
                out.append({"id": p.get("id"), "nazev": p.get("name", ""), "tasklisty": tl})
        return sorted(out, key=lambda x: x["nazev"].lower())
    except Exception as e:
        print(f"[freelo] projekty: {e}")
        return []


def _uorm(t, hotovo):
    return {"nazev": t.get("name", ""), "hotovo": hotovo,
            "url": f"https://app.freelo.io/task/{t.get('id')}",
            "resitel": ((t.get("worker") or {}).get("fullname")
                        or (t.get("assignee") or {}).get("fullname") or "")}


def ukoly_klienta(tasklist_id):
    """Vrátí dict {aktivni:[...], hotove:[...], open_count:n} pro daný tasklist."""
    prazdny = {"aktivni": [], "hotove": [], "open_count": 0}
    if not je_nakonfigurovano() or not tasklist_id:
        return prazdny
    try:
        aktivni, hotove = [], []
        r = _get(f"/tasklist/{tasklist_id}")
        if r.status_code == 200:
            d = r.json()
            tasks = d.get("tasks") or d.get("data", {}).get("tasks", []) or []
            aktivni = [_uorm(t, False) for t in tasks]
        rf = _get(f"/tasklist/{tasklist_id}/finished-tasks")
        if rf.status_code == 200:
            df = rf.json()
            ft = df.get("data", {}).get("finished_tasks", []) or df.get("finished_tasks", []) or []
            hotove = [_uorm(t, True) for t in ft]
        return {"aktivni": aktivni, "hotove": hotove, "open_count": len(aktivni)}
    except Exception as e:
        print(f"[freelo] ukoly: {e}")
        return prazdny


def diagnostika(tasklist_id=None):
    out = {"nakonfigurovano": je_nakonfigurovano(), "email_len": len(FREELO_EMAIL), "klic_len": len(FREELO_API_KEY)}
    if not je_nakonfigurovano():
        return out
    try:
        r = _get("/projects")
        out["projects_status"] = r.status_code
        out["pocet_projektu"] = len(r.json() if isinstance(r.json(), list) else r.json().get("data", []))
        if tasklist_id:
            rt = _get(f"/tasklist/{tasklist_id}")
            out["tasklist_status"] = rt.status_code
            out["tasklist_ukazka"] = (rt.text or "")[:300]
    except Exception as e:
        out["chyba"] = str(e)
    return out
