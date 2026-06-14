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


def _datum(v):
    """Z Freelo pole vytáhne datum (může být string nebo {'date': ...})."""
    if isinstance(v, dict):
        return (v.get("date") or "")[:10]
    return (v or "")[:10] if isinstance(v, str) else ""


def _uorm(t, hotovo):
    return {"id": t.get("id"), "nazev": t.get("name", ""), "hotovo": hotovo,
            "freelo_url": f"https://app.freelo.io/task/{t.get('id')}",
            "termin": _datum(t.get("due_date")),
            "resitel": ((t.get("worker") or {}).get("fullname")
                        or (t.get("assignee") or {}).get("fullname") or "")}


def ukol_detail(task_id):
    """Detail úkolu z Freelo: název, popis, stav, datumy, řešitel, projekt/tasklist."""
    if not je_nakonfigurovano() or not task_id:
        return None
    try:
        r = _get(f"/task/{task_id}")
        if r.status_code != 200:
            return None
        d = r.json()
        t = d.get("data", d)
        return {
            "id": t.get("id"),
            "nazev": t.get("name", ""),
            "popis": (t.get("comment") or {}).get("content", "") if isinstance(t.get("comment"), dict) else (t.get("description") or ""),
            "stav": (t.get("state") or {}).get("state") if isinstance(t.get("state"), dict) else (t.get("state") or ""),
            "zadan": _datum(t.get("date_add")),
            "termin": _datum(t.get("due_date")),
            "posledni": _datum(t.get("date_edited_at") or t.get("date_edited")),
            "resitel": ((t.get("worker") or {}).get("fullname") or (t.get("assignee") or {}).get("fullname") or ""),
            "project_id": t.get("project", {}).get("id") if isinstance(t.get("project"), dict) else t.get("project_id"),
            "tasklist_id": t.get("tasklist", {}).get("id") if isinstance(t.get("tasklist"), dict) else t.get("tasklist_id"),
            "freelo_url": f"https://app.freelo.io/task/{t.get('id')}",
        }
    except Exception as e:
        print(f"[freelo] ukol_detail: {e}")
        return None


def workers(project_id):
    """Řešitelé v projektu → [(id, fullname)]."""
    if not je_nakonfigurovano() or not project_id:
        return []
    try:
        r = _get(f"/project/{project_id}/workers")
        if r.status_code != 200:
            return []
        ws = r.json().get("data", {}).get("workers", []) or r.json().get("workers", [])
        return [(w.get("id"), w.get("fullname", "")) for w in ws]
    except Exception as e:
        print(f"[freelo] workers: {e}")
        return []


def komentare(task_id):
    """Komentáře/aktivita úkolu → [{autor, datum, text}] (nejnovější první)."""
    if not je_nakonfigurovano() or not task_id:
        return []
    try:
        r = _get(f"/task/{task_id}/comments")
        if r.status_code != 200:
            return []
        data = r.json().get("data", {}).get("comments", []) or r.json().get("comments", [])
        out = []
        for c in data:
            out.append({"autor": (c.get("author") or {}).get("fullname", ""),
                        "datum": _datum(c.get("date_add")),
                        "text": c.get("content", "")})
        return out
    except Exception as e:
        print(f"[freelo] komentare: {e}")
        return []


def priradit(task_id, worker_id):
    try:
        r = requests.post(f"{BASE}/task/{task_id}", auth=(FREELO_EMAIL, FREELO_API_KEY),
                          headers={"Content-Type": "application/json"},
                          json={"worker_id": int(worker_id)}, timeout=TIMEOUT)
        return r.status_code in (200, 201)
    except Exception as e:
        print(f"[freelo] priradit: {e}")
        return False


def pridej_komentar(task_id, text):
    try:
        r = requests.post(f"{BASE}/task/{task_id}/comments", auth=(FREELO_EMAIL, FREELO_API_KEY),
                          headers={"Content-Type": "application/json"},
                          json={"content": text}, timeout=TIMEOUT)
        return r.status_code in (200, 201)
    except Exception as e:
        print(f"[freelo] komentar: {e}")
        return False


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


def diagnostika(tasklist_id=None, task_id=None):
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
            out["tasklist_ukazka"] = (rt.text or "")[:400]
        if task_id:
            rk = _get(f"/task/{task_id}")
            out["task_status"] = rk.status_code
            out["task_ukazka"] = (rk.text or "")[:600]
    except Exception as e:
        out["chyba"] = str(e)
    return out
