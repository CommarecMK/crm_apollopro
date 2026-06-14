"""
services/freelo.py — napojení na Freelo (úkoly klienta).
Auth: HTTP Basic (FREELO_EMAIL + FREELO_API_KEY). Read-only použití.
Klient ↔ Freelo tasklist (Firma.freelo_tasklist_id).
"""
import time
from datetime import date, datetime
import requests
from ..extensions import FREELO_EMAIL, FREELO_API_KEY

BASE = "https://api.freelo.io/v1"
TIMEOUT = 20

# Krátkodobá cache (zrychluje dashboard a seznam klientů)
_CACHE = {}
CACHE_TTL = 300  # s


def _cache_get(k):
    v = _CACHE.get(k)
    if v and (time.time() - v[0]) < CACHE_TTL:
        return v[1]
    return None


def _cache_set(k, val):
    _CACHE[k] = (time.time(), val)
    return val


def je_nakonfigurovano():
    return bool(FREELO_EMAIL and FREELO_API_KEY)


USER_AGENT = "ApolloPro (martin.komarek@commarec.cz)"  # Freelo vyžaduje User-Agent


def _hlavicky():
    return {"Content-Type": "application/json", "User-Agent": USER_AGENT}


def _get(path, params=None):
    return requests.get(f"{BASE}{path}", auth=(FREELO_EMAIL, FREELO_API_KEY),
                        headers=_hlavicky(), params=params, timeout=TIMEOUT)


def _json(path):
    """GET s krátkou cache → (status_code, parsed_json|None). Šetří opakované dotazy."""
    ck = f"json:{path}"
    c = _cache_get(ck)
    if c is not None:
        return c
    try:
        r = _get(path)
        try:
            data = r.json()
        except Exception:
            data = None
        return _cache_set(ck, (r.status_code, data))
    except Exception as e:
        print(f"[freelo] GET {path}: {e}")
        return (0, None)


def _vytahni_seznam(obj, klice):
    """Najde v odpovědi Freela seznam položek, ať je zanořený jakkoli.
    Freelo někdy vrací {'data':[...]}, jindy {'data':{'comments':[...]}} nebo {'comments':[...]}."""
    if isinstance(obj, list):
        return obj
    if not isinstance(obj, dict):
        return []
    for k in klice:
        v = obj.get(k)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            vnoreno = _vytahni_seznam(v, klice)
            if vnoreno:
                return vnoreno
    return []


def _text(v):
    """Z různých tvarů (str / {'content':..} / {'fullname':..}) vytáhne text."""
    if isinstance(v, dict):
        return v.get("content") or v.get("fullname") or v.get("name") or ""
    return v or ""


def _dni_od(datum_str):
    """Počet dní od daného YYYY-MM-DD do dneška (None když nezadáno)."""
    if not datum_str:
        return None
    try:
        return (date.today() - date.fromisoformat(datum_str[:10])).days
    except Exception:
        return None


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


def _je_oddelovac(t):
    """Freelo vrací v seznamu i skupinové oddělovače podúkolů ('Podúkoly úkolu: …').
    Ty do hlavního seznamu nepatří — podúkoly se ukazují v detailu nadřazeného úkolu."""
    nazev = str(t.get("name") or "")
    return nazev.startswith("Podúkoly úkolu") or t.get("type") in ("subtasks_separator", "separator")


def _pocet_podukolu(t):
    for kand in (t.get("count_subtasks"), t.get("subtasks_count"),
                 t.get("count_taskchecks"), t.get("taskchecks_count")):
        if isinstance(kand, int) and kand:
            return kand
    if isinstance(t.get("subtasks"), list):
        return len(t.get("subtasks"))
    return 0


def _uorm(t, hotovo):
    posledni = _datum(t.get("date_edited_at") or t.get("date_edited"))
    return {"id": t.get("id"), "nazev": t.get("name", ""), "hotovo": hotovo,
            "podukolu": _pocet_podukolu(t),
            "freelo_url": f"https://app.freelo.io/task/{t.get('id')}",
            "termin": _datum(t.get("due_date")),
            "zadan": _datum(t.get("date_add")),
            "posledni": posledni,
            "dni_od_iterace": _dni_od(posledni),
            "komentaru": t.get("comments_count") or t.get("count_comments") or 0,
            "resitel": ((t.get("worker") or {}).get("fullname")
                        or (t.get("assignee") or {}).get("fullname") or "")}


def _prilohy(obj):
    out = []
    for f in _vytahni_seznam(obj, ("files", "attachments")):
        if isinstance(f, dict):
            out.append({"nazev": f.get("filename") or f.get("name") or "příloha",
                        "uuid": f.get("uuid") or "", "velikost": f.get("size") or 0})
    return out


def _komentar_orm(c):
    return {"autor": _text(c.get("author")) or _text(c.get("worker")) or _text(c.get("created_by")),
            "datum": _datum(c.get("date_add") or c.get("date")),
            "text": _text(c.get("content")) or _text(c.get("comment")),
            "prilohy": _prilohy(c)}


def popis_ukolu(task_id):
    """GET /task/{id}/description → {text, datum, prilohy}. Popis je 'pinned' komentář."""
    if not je_nakonfigurovano() or not task_id:
        return {"text": "", "datum": None, "prilohy": []}
    try:
        st, d = _json(f"/task/{task_id}/description")
        if st != 200 or not isinstance(d, dict):
            return {"text": "", "datum": None, "prilohy": []}
        d = d.get("data", d)
        return {"text": _text(d.get("content")), "datum": _datum(d.get("date_add")),
                "prilohy": _prilohy(d)}
    except Exception as e:
        print(f"[freelo] popis: {e}")
        return {"text": "", "datum": None, "prilohy": []}


def ukol_detail(task_id):
    """Detail úkolu z Freelo: název, popis, datumy, řešitel, komentáře (vložené v detailu)."""
    if not je_nakonfigurovano() or not task_id:
        return None
    try:
        st, d = _json(f"/task/{task_id}")
        if st != 200 or not isinstance(d, dict):
            return None
        t = d.get("data", d)
        posledni = _datum(t.get("date_edited_at") or t.get("date_edited"))
        # Komentáře jsou součástí detailu úkolu (samostatný GET endpoint neexistuje)
        komentare = [_komentar_orm(c) for c in _vytahni_seznam(t, ("comments",)) if isinstance(c, dict)]
        # Poslední reakce = datum nejnovějšího komentáře (jinak poslední úprava)
        datumy_kom = [k["datum"] for k in komentare if k["datum"]]
        if datumy_kom:
            posledni = max(datumy_kom)
        # Popis = samostatný endpoint /description (pinned komentář)
        popis = popis_ukolu(task_id)
        return {
            "id": t.get("id"),
            "nazev": t.get("name", ""),
            "popis": popis["text"],
            "popis_prilohy": popis["prilohy"],
            "stav": (t.get("state") or {}).get("state") if isinstance(t.get("state"), dict) else (t.get("state") or ""),
            "hotovo": (str((t.get("state") or {}).get("state") if isinstance(t.get("state"), dict) else t.get("state")).lower()
                       in ("finished", "done", "2")),
            "zadan": _datum(t.get("date_add")),
            "termin": _datum(t.get("due_date")),
            "posledni": posledni,
            "dni_od_iterace": _dni_od(posledni),
            "resitel": ((t.get("worker") or {}).get("fullname") or (t.get("assignee") or {}).get("fullname") or ""),
            "komentare": komentare,
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
        st, d = _json(f"/project/{project_id}/workers")
        if st != 200 or not isinstance(d, dict):
            return []
        ws = (d.get("data", {}) or {}).get("workers", []) or d.get("workers", [])
        return [(w.get("id"), w.get("fullname", "")) for w in ws]
    except Exception as e:
        print(f"[freelo] workers: {e}")
        return []


def subtasks(task_id):
    """Podúkoly úkolu (GET /task/{id}/subtasks) → [{id,nazev,hotovo,termin,resitel}]."""
    if not je_nakonfigurovano() or not task_id:
        return []
    try:
        st, d = _json(f"/task/{task_id}/subtasks")
        if st != 200 or d is None:
            return []
        out = []
        for s in _vytahni_seznam(d, ("subtasks", "tasks", "taskchecks", "data")):
            if not isinstance(s, dict):
                continue
            stav = s.get("state")
            hotovo = bool(s.get("finished") or s.get("is_finished")
                          or (isinstance(stav, dict) and stav.get("state") in ("finished", "done"))
                          or stav in ("finished", "done", 2))
            out.append({"id": s.get("id"), "nazev": s.get("name", ""), "hotovo": hotovo,
                        "termin": _datum(s.get("due_date")),
                        "resitel": _text(s.get("worker")) or _text(s.get("assignee"))})
        return out
    except Exception as e:
        print(f"[freelo] subtasks: {e}")
        return []


def priradit(task_id, worker_id, auth=None):
    try:
        r = requests.post(f"{BASE}/task/{task_id}", auth=_auth(auth),
                          headers=_hlavicky(), json={"worker": int(worker_id)}, timeout=TIMEOUT)
        if r.status_code in (200, 201):
            _CACHE.clear()  # ať se změna hned projeví
            return True
        return False
    except Exception as e:
        print(f"[freelo] priradit: {e}")
        return False


def _auth(auth=None):
    """Vrátí (email, key) — buď osobní klíč přihlášeného, nebo sdílený z env."""
    if auth and auth[0] and auth[1]:
        return auth
    return (FREELO_EMAIL, FREELO_API_KEY)


def _post_akce(path, auth=None):
    try:
        r = requests.post(f"{BASE}{path}", auth=_auth(auth),
                          headers=_hlavicky(), timeout=TIMEOUT)
        if r.status_code in (200, 201):
            _CACHE.clear()
            return True
        return False
    except Exception as e:
        print(f"[freelo] akce {path}: {e}")
        return False


def dokoncit(task_id, auth=None):
    """Označit úkol jako hotový (POST /task/{id}/finish)."""
    return _post_akce(f"/task/{task_id}/finish", auth)


def znovu_otevrit(task_id, auth=None):
    """Znovu otevřít hotový úkol (POST /task/{id}/activate)."""
    return _post_akce(f"/task/{task_id}/activate", auth)


def pridej_komentar(task_id, text, auth=None):
    try:
        r = requests.post(f"{BASE}/task/{task_id}/comments", auth=_auth(auth),
                          headers=_hlavicky(), json={"content": text}, timeout=TIMEOUT)
        if r.status_code in (200, 201):
            _CACHE.clear()
            return True
        return False
    except Exception as e:
        print(f"[freelo] komentar: {e}")
        return False


def project_id_pro_tasklist(tasklist_id):
    """Najde ID projektu, do kterého patří daný tasklist."""
    if not tasklist_id:
        return None
    for p in projekty_s_tasklisty():
        for tl in p.get("tasklisty", []):
            if str(tl.get("id")) == str(tasklist_id):
                return p.get("id")
    return None


def vytvor_ukol(tasklist_id, nazev, popis="", worker_id=None, termin=None, auth=None):
    """Vytvoří úkol v tasklistu klienta. termin = 'YYYY-MM-DD' nebo None. Vrací (ok, info)."""
    pid = project_id_pro_tasklist(tasklist_id)
    if not pid:
        return False, "projekt nenalezen"
    telo = {"name": nazev}
    if popis:
        telo["comment"] = {"content": popis}
    if worker_id:
        try:
            telo["worker"] = int(worker_id)
        except (ValueError, TypeError):
            pass
    if termin:
        telo["due_date"] = f"{termin[:10]}T09:00:00"
    try:
        r = requests.post(f"{BASE}/project/{pid}/tasklist/{tasklist_id}/tasks",
                          auth=_auth(auth), headers=_hlavicky(), json=telo, timeout=TIMEOUT)
        if r.status_code in (200, 201):
            _CACHE.clear()
            return True, "ok"
        return False, f"{r.status_code}: {(r.text or '')[:150]}"
    except Exception as e:
        return False, str(e)


def _id_ukolu_z_komentare(c):
    """Z komentáře (z /all-comments) vytáhne ID úkolu, ať je reference kdekoli."""
    for kandidat in (c.get("task_id"),
                     (c.get("task") or {}).get("id") if isinstance(c.get("task"), dict) else None,
                     (c.get("related_object") or {}).get("id") if isinstance(c.get("related_object"), dict) else None,
                     (c.get("related") or {}).get("id") if isinstance(c.get("related"), dict) else None):
        if kandidat:
            return str(kandidat)
    return None


def reakce_mapa(max_stran=8, vse=False):
    """{task_id(str): 'YYYY-MM-DD'} = datum POSLEDNÍHO komentáře u úkolu (z /all-comments).
    vse=True → projde všechny stránky (pro snapshot, 100% přesné, bez cache)."""
    if not vse:
        c = _cache_get("reakce_mapa")
        if c is not None:
            return c
        max_stran = 8
    out = {}
    try:
        for p in range(max_stran if not vse else 1000):
            st, d = _json(f"/all-comments?type=task&order_by=date_add&order=desc&p={p}")
            if st != 200 or not d:
                break
            coms = _vytahni_seznam(d, ("comments", "data"))
            if not coms:
                break
            for cc in coms:
                if not isinstance(cc, dict):
                    continue
                tid = _id_ukolu_z_komentare(cc)
                dt = _datum(cc.get("date_add") or cc.get("date"))
                if tid and dt and tid not in out:  # desc pořadí → první výskyt = nejnovější
                    out[tid] = dt
            if len(coms) < 20:
                break
    except Exception as e:
        print(f"[freelo] reakce_mapa: {e}")
    if not vse:
        _cache_set("reakce_mapa", out)
    return out


def _dopln_reakce(ukoly, mapa=None):
    """Do úkolů doplní poslední reakci (komentář) z mapy reakcí."""
    if mapa is None:
        mapa = reakce_mapa()
    for u in ukoly:
        d = mapa.get(str(u["id"]))
        if d:
            u["posledni"] = d
            u["dni_od_iterace"] = _dni_od(d)
            u["komentaru"] = 1
        else:
            u["komentaru"] = 0
    return ukoly


def ukoly_raw(tasklist_id, mapa=None):
    """Živé stažení úkolů tasklistu z Freela (bez snapshotu). Pro snapshot i fallback."""
    aktivni, hotove = [], []
    r = _get(f"/tasklist/{tasklist_id}")
    if r.status_code == 200:
        tasks = _vytahni_seznam(r.json(), ("tasks", "data"))
        aktivni = [_uorm(t, False) for t in tasks if isinstance(t, dict) and not _je_oddelovac(t)]
    rf = _get(f"/tasklist/{tasklist_id}/finished-tasks")
    if rf.status_code == 200:
        ft = _vytahni_seznam(rf.json(), ("finished_tasks", "tasks", "data"))
        hotove = [_uorm(t, True) for t in ft if isinstance(t, dict) and not _je_oddelovac(t)]
    _dopln_reakce(aktivni, mapa)
    _dopln_reakce(hotove, mapa)
    return {"aktivni": aktivni, "hotove": hotove, "open_count": len(aktivni)}


def ukoly_klienta(tasklist_id, jen_snapshot=False):
    """Úkoly tasklistu. Přednostně ze snapshotu (rychlé, přesné).
    jen_snapshot=True → nikdy nesahá živě (pro agregační stránky, ať se nesekají)."""
    prazdny = {"aktivni": [], "hotove": [], "open_count": 0}
    if not tasklist_id:
        return prazdny
    # 1) snapshot
    try:
        from . import snapshot_freelo
        ze_snapshotu = snapshot_freelo.tasklist(tasklist_id)
        if ze_snapshotu is not None:
            return ze_snapshotu
    except Exception as e:
        print(f"[freelo] snapshot read: {e}")
    if jen_snapshot or not je_nakonfigurovano():
        return prazdny
    # 2) živě (jen pro detail jednoho klienta) s cache
    ck = f"ukoly:{tasklist_id}"
    c = _cache_get(ck)
    if c is not None:
        return c
    try:
        return _cache_set(ck, ukoly_raw(tasklist_id))
    except Exception as e:
        print(f"[freelo] ukoly: {e}")
        return prazdny


def _bez_reakce(u):
    """Úkol bez reakce = od zadání se nic nestalo (žádná iterace/komentář)."""
    if u.get("komentaru"):
        return False
    return not u["posledni"] or (u["zadan"] and u["posledni"] <= u["zadan"])


def prehled_resitelu(firmy):
    """firmy = list (firma_id, nazev, tasklist_id). Vrátí agregaci úkolů podle řešitele:
    {jmeno: {jmeno, open, po_terminu, max_zpozdeni, bez_reakce, posledni, ukoly:[...]}}."""
    dnes = date.today()
    lide = {}

    def _osoba(jm):
        return lide.setdefault(jm, {"jmeno": jm, "open": 0, "po_terminu": 0, "max_zpozdeni": 0,
                                    "bez_reakce": 0, "hotovo": 0, "posledni": None, "ukoly": []})
    for fid, nazev, tlid in firmy:
        if not tlid:
            continue
        data = ukoly_klienta(tlid, jen_snapshot=True)
        for u in data["aktivni"]:
            e = _osoba(u["resitel"] or "— bez řešitele —")
            e["open"] += 1
            if _bez_reakce(u):
                e["bez_reakce"] += 1
            if u["posledni"] and (e["posledni"] is None or u["posledni"] > e["posledni"]):
                e["posledni"] = u["posledni"]
            zp = 0
            if u["termin"]:
                try:
                    zp = (dnes - date.fromisoformat(u["termin"])).days
                except Exception:
                    zp = 0
                if zp > 0:
                    e["po_terminu"] += 1
                    e["max_zpozdeni"] = max(e["max_zpozdeni"], zp)
            e["ukoly"].append({**u, "firma": nazev, "firma_id": fid, "zpozdeni": max(zp, 0)})
        for u in data["hotove"]:
            _osoba(u["resitel"] or "— bez řešitele —")["hotovo"] += 1
    return lide


def souhrn_tasklistu(tasklist_id, jen_snapshot=False):
    """Metriky jednoho tasklistu pro dlaždici/dashboard:
    {open, po_terminu, max_zpozdeni, bez_reakce, posledni_reakce, overdue_tasks:[...]}."""
    z = {"open": 0, "po_terminu": 0, "max_zpozdeni": 0, "bez_reakce": 0, "hotovo": 0,
         "posledni_reakce": None, "overdue_tasks": []}
    if not tasklist_id:
        return z
    data = ukoly_klienta(tasklist_id, jen_snapshot=jen_snapshot)
    dnes = date.today()
    z["open"] = data["open_count"]
    z["hotovo"] = len(data.get("hotove", []))
    for u in data["aktivni"]:
        # poslední reakce = nejnovější datum iterace napříč úkoly
        if u["posledni"] and (z["posledni_reakce"] is None or u["posledni"] > z["posledni_reakce"]):
            z["posledni_reakce"] = u["posledni"]
        # bez reakce = od zadání se nic nestalo (žádná iterace/úprava)
        if u.get("komentaru"):
            pass  # má komentáře → reagováno
        elif not u["posledni"] or (u["zadan"] and u["posledni"] <= u["zadan"]):
            z["bez_reakce"] += 1
        # po termínu
        if u["termin"]:
            try:
                zpoz = (dnes - date.fromisoformat(u["termin"])).days
            except Exception:
                zpoz = 0
            if zpoz > 0:
                z["po_terminu"] += 1
                z["max_zpozdeni"] = max(z["max_zpozdeni"], zpoz)
                z["overdue_tasks"].append({**u, "zpozdeni": zpoz})
    z["overdue_tasks"].sort(key=lambda x: -x["zpozdeni"])
    return z


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
        rac = _get("/all-comments?type=task&order_by=date_add&order=desc&p=0")
        out["all_comments_status"] = rac.status_code
        out["all_comments_ukazka"] = (rac.text or "")[:900]
        out["reakce_mapa_pocet"] = len(reakce_mapa())
        if task_id:
            rk = _get(f"/task/{task_id}")
            out["task_status"] = rk.status_code
            out["task_ukazka"] = (rk.text or "")[:1200]
            rd = _get(f"/task/{task_id}/description")
            out["description_status"] = rd.status_code
            out["description_ukazka"] = (rd.text or "")[:900]
    except Exception as e:
        out["chyba"] = str(e)
    return out
