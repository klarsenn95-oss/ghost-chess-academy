"""
╔══════════════════════════════════════════════════════════╗
║        ♟  GHOST — Chess Group Manager  (Flask v3)        ║
║   pip install flask reportlab flask-apscheduler          ║
║   python app.py                                          ║
╚══════════════════════════════════════════════════════════╝
"""

from flask import Flask, render_template, request, jsonify, redirect, send_from_directory, session
import json, os, uuid, threading
from datetime import datetime, timedelta, date
from functools import wraps
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
import urllib.parse
from zoneinfo import ZoneInfo
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from supabase_backend import (
    backend_name, bootstrap_from_local_json, load_state, save_state,
    storage_configured, upload_bytes
)


# ReportLab
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                 Table, TableStyle, HRFlowable)
from reportlab.graphics.shapes import Drawing, Rect, String, Line

# APScheduler
from flask_apscheduler import APScheduler

app = Flask(__name__)
app.secret_key = os.environ.get("GHOST_SECRET_KEY", "ghost-dev-secret-change-me")
DATA_FILE = os.path.join(os.path.expanduser("~"), ".ghost_chess_data.json")

ADMIN_USERNAME = os.environ.get("GHOST_ADMIN_USERNAME", "coach")
ADMIN_PASSWORD = os.environ.get("GHOST_ADMIN_PASSWORD", "")

def admin_auth_enabled():
    return bool(ADMIN_PASSWORD)

def admin_logged_in():
    return bool(session.get("coach_logged_in"))

def wants_json_response():
    return request.path.startswith("/api/") or "application/json" in (request.headers.get("Accept") or "")

@app.before_request
def protect_coach_space():
    if not admin_auth_enabled():
        return None
    path = request.path or "/"
    public_prefixes = ("/static/", "/client", "/api/client", "/coach/login", "/login", "/health", "/favicon.ico")
    if path.startswith(public_prefixes):
        return None
    if admin_logged_in():
        return None
    if wants_json_response():
        return jsonify({"ok": False, "error": "Connexion coach requise."}), 401
    return redirect("/login")



@app.before_request
def track_basic_visits():
    """Compteur léger de visites pour le centre de commandement.
    On compte une visite par session et par jour pour éviter de gonfler les chiffres avec les assets/API.
    """
    try:
        if request.method != "GET":
            return None
        path = request.path or "/"
        if path.startswith(("/static/", "/api/", "/health", "/favicon.ico")):
            return None
        today = date.today().isoformat()
        stamp = session.get("ghost_visit_counted")
        if stamp == today:
            return None
        data = load_data()
        stats = data.setdefault("visit_stats", {})
        old_today = stats.get("today")
        stats["total"] = int(stats.get("total") or 0) + 1
        stats["today"] = today
        stats["today_count"] = int(stats.get("today_count") or 0) + 1 if old_today == today else 1
        stats.setdefault("by_day", {})
        stats["by_day"][today] = int(stats["by_day"].get(today) or 0) + 1
        stats["last_path"] = path
        stats["last_at"] = now_fr() if "now_fr" in globals() else datetime.now().strftime("%d/%m/%Y %H:%M")
        data["visit_stats"] = stats
        save_data(data)
        session["ghost_visit_counted"] = today
    except Exception as e:
        print("[GHOST] visit counter skipped:", e)
    return None

@app.route("/coach/login", methods=["GET", "POST"])
def coach_login():
    if not admin_auth_enabled():
        session["coach_logged_in"] = True
        return redirect("/")
    error = ""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["coach_logged_in"] = True
            return redirect("/")
        error = "Identifiants incorrects."
    return render_template("coach_login.html", error=error, username="")

@app.route("/coach/logout")
def coach_logout():
    session.pop("coach_logged_in", None)
    return redirect("/login")

@app.route("/login")
def common_login():
    # Porte d'entrée commune : coach ou élève.
    if admin_logged_in():
        return redirect("/")
    return render_template("entry.html", username="")


# ── Dossiers uploads / reports ─────────────────────────────
UPLOAD_FOLDER  = os.path.join(os.path.dirname(__file__), "static", "uploads", "exam")
CLIENT_UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "static", "uploads", "client")
REPORTS_FOLDER = os.path.join(os.path.dirname(__file__), "static", "reports")
os.makedirs(UPLOAD_FOLDER,  exist_ok=True)
os.makedirs(CLIENT_UPLOAD_FOLDER, exist_ok=True)
os.makedirs(REPORTS_FOLDER, exist_ok=True)
ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "webp", "pdf", "pgn", "txt"}

def allowed_file(fn):
    return "." in fn and fn.rsplit(".", 1)[1].lower() in ALLOWED_EXT

# ── Scheduler ──────────────────────────────────────────────
app.config["SCHEDULER_API_ENABLED"] = False
scheduler = APScheduler()
scheduler.init_app(app)

MONTHS = ["Jan","Fév","Mar","Avr","Mai","Jun","Jul","Aoû","Sep","Oct","Nov","Déc"]

OPENINGS = [
    "Sicilienne","Française","Caro-Kann","Pirc","Moderne","Est-Indien","Ouest-Indien",
    "Grünfeld","Nimzo-Indien","Hollandaise","Anglaise","Reti","Catalan","Espagnole",
    "Italienne","Petrov","Scandinave","Alekhine","Budapest","Benko","Benoni",
    "Roi's gambit","Gambit Dame","Défense Dame","Ouverture Roi","Londre","Colle",
]

DEVOIR_STATUS = ["📋 À faire","🔄 En cours","📤 Rendu","🧑‍🏫 À corriger","✅ Corrigé","✅ Fait"]
COMPORTEMENT  = ["😊 Motivé","😐 Régulier","😴 Passif","🔥 Très investi","😤 Têtu","🤔 Curieux"]
WORK_THEMES   = ["Tactique","Finales","Ouvertures","Milieu de jeu","Calcul","Stratégie",
                 "Endgame technique","Attaque du roi","Structure de pions","Timing"]

RECURRING_ERRORS = [
    "Blunder en zeitnot","Mauvaise gestion du temps","Perd le fil en position complexe",
    "Sous-estime les contre-jeux","Attaque prématurée","Finales mal jouées",
    "Ouverture pas maîtrisée","Calcul incomplet","Échange trop rapide",
    "Ne convertit pas l'avantage","Faiblesse sur l'aile dame","Manque de patience positionnelle",
]

# ─── Dual Rank System ─────────────────────────────────────
RANKS_PIRATES = [
    (2700, "☀️", "Joy Boy",              "#ffe066"),
    (2500, "💠", "Roi des Mers",         "#c0e8ff"),
    (2300, "👒", "Yonko",               "#f472b6"),
    (2100, "🔥", "Yonko Commander",     "#fb923c"),
    (1900, "⚔️", "Shichibukai",         "#a78bfa"),
    (1700, "👑", "Capitaine Pirate",    "#fbbf24"),
    (1500, "💀", "Super Rookie",        "#f87171"),
    (1300, "🗡️", "Pirate Confirmé",     "#e05555"),
    (1100, "🏴", "Pirate Aguerri",      "#cc1a1a"),
    ( 900, "⚓", "Matelot Vétéran",     "#888888"),
    (   0, "🌊", "Matelot",             "#555555"),
]

RANKS_MARINE = [
    (2700, "👁️", "Imu Sama",             "#fde68a"),
    (2500, "🌟", "Grand Amiral",          "#c084fc"),
    (2300, "🌑", "Amiral en Chef",        "#60a5fa"),
    (2100, "🎖️", "Amiral",               "#38bdf8"),
    (1900, "⚓", "Vice-Amiral",           "#7dd3fc"),
    (1700, "🪖", "Contre-Amiral",         "#a5f3fc"),
    (1500, "🎯", "Capitaine",             "#4ade80"),
    (1300, "🔰", "Lieutenant-Commandant", "#86efac"),
    (1100, "🛡️", "Adjudant",             "#bbf7d0"),
    ( 900, "🔫", "Soldat Vétéran",        "#888888"),
    (   0, "🌊", "Recrue",               "#555555"),
]

CADENCE_SUFFIX = {
    "bullet":    ("de l'Éclair",   "⚡"),
    "blitz":     ("de la Flamme",  "🔥"),
    "rapid":     ("de la Tempête", "🌪️"),
    "classical": ("de l'Abîsse",   "🌑"),
    "balanced":  ("des Mers",      "🌊"),
}

HAKI_THRESHOLDS = [
    ("bullet",  2400, "haki_vision",      "🔮 Vision du Futur",      "#fde68a"),
    ("bullet",  2000, "haki_perception",  "👁 Haki Perceptif",        "#7dd3fc"),
    ("blitz",   2000, "haki_armement",    "⚫ Haki d'Armement",      "#9ca3af"),
    ("any",     2300, "haki_royal",       "👑 Haki Royal",            "#fbbf24"),
]

ISLANDS = [
    (2700, "💀", "Raftel",        "Île au bout du monde",   "#fde68a"),
    (2450, "🌲", "Elbaf",         "Île des géants",         "#86efac"),
    (2200, "🔬", "Egghead",       "Île du futur",           "#a5f3fc"),
    (2000, "🐉", "Wano",          "Pays des samouraïs",     "#f472b6"),
    (1850, "⚔️", "Marineford",    "Guerre au sommet",       "#f87171"),
    (1700, "🧊", "Thriller Bark", "Navire fantôme",         "#c4b5fd"),
    (1550, "🚂", "Water Seven",   "Ville sur l'eau",        "#60a5fa"),
    (1400, "🌿", "Skypiea",       "Île dans les nuages",    "#fbbf24"),
    (1200, "🏝️", "Alabasta",      "Royaume du désert",      "#fb923c"),
    (1000, "⚓", "Loguetown",     "Ville de l'exécution",   "#94a3b8"),
    (   0, "🌊", "Fuschia",       "Village de départ",      "#6ee7b7"),
]

def get_island(student):
    """Île déterminée par l'elo moyen (bullet + blitz + rapid), cohérent avec get_rank().
    Un override manuel prend le dessus si défini."""
    override = student.get("island_override")
    if override:
        for threshold, emoji, name, desc, color in ISLANDS:
            if name == override:
                return {"name": name, "emoji": emoji, "desc": desc,
                        "color": color, "threshold": threshold, "manual": True}
    avg = get_avg_elo(student)
    for threshold, emoji, name, desc, color in ISLANDS:
        if avg >= threshold:
            return {"name": name, "emoji": emoji, "desc": desc,
                    "color": color, "threshold": threshold, "manual": False}
    last = ISLANDS[-1]
    return {"name": last[2], "emoji": last[1], "desc": last[3],
            "color": last[4], "threshold": last[0], "manual": False}

# ─── Helpers ───────────────────────────────────────────────
def detect_branch(student):
    return student.get("branch") or "pirates"

def get_peak_elo(student):
    peak = 0
    for k in ["elo_li_blitz","elo_li_bullet","elo_li_rapid","elo_li_classical",
              "elo_cc_blitz","elo_cc_bullet","elo_cc_rapid"]:
        v = student.get(k,"")
        if v and str(v).lstrip("-").isdigit():
            peak = max(peak, int(v))
    for e in student.get("elo_history",[]):
        for k in ["elo_li_blitz","elo_li_bullet","elo_li_rapid","elo_li_classical",
                  "elo_cc_blitz","elo_cc_bullet","elo_cc_rapid","elo_li","elo_cc"]:
            v = e.get(k,"")
            if v and str(v).lstrip("-").isdigit():
                peak = max(peak, int(v))
    return peak

def assign_branches_50_50(students):
    """Assigne Marine/Pirates en alternant par rang d'elo moyen.
    Ne touche que les joueurs sans branche fixée (branch_locked=False et branch vide)."""
    free = [(i,s) for i,s in enumerate(students) if not s.get("branch_locked") and not s.get("branch")]
    if not free:
        return students
    # Mélange avec les joueurs déjà assignés (non-locked) pour un classement global cohérent
    assigned = [(i,s) for i,s in enumerate(students) if not s.get("branch_locked") and s.get("branch")]
    all_free = free + assigned
    all_free.sort(key=lambda x: get_avg_elo(x[1]), reverse=True)
    for rank_pos, (i, s) in enumerate(all_free):
        students[i]["branch"] = "marine" if rank_pos % 2 == 0 else "pirates"
    return students

def get_flame_status(student):
    avg = get_avg_elo(student)
    branch = detect_branch(student)
    ranks = RANKS_MARINE if branch == "marine" else RANKS_PIRATES
    grade_threshold = 0
    for threshold, emoji, title, color in ranks:
        if avg >= threshold:
            grade_threshold = threshold
            break
    # Actif si l'avg courant est au niveau du seuil (toujours vrai par construction,
    # utile seulement en cas d'override manuel de rang)
    if avg >= grade_threshold and grade_threshold > 0:
        return {"active": True,  "icon": "🔥", "color": "#ef4444", "label": "Actif"}
    else:
        return {"active": False, "icon": "🔵", "color": "#60a5fa", "label": "En retrait"}

def get_avg_elo(student):
    def best(k1, k2=None):
        v1 = student.get(k1,""); v2 = student.get(k2,"") if k2 else ""
        a = int(v1) if v1 and str(v1).lstrip("-").isdigit() and int(v1)>0 else 0
        b = int(v2) if v2 and str(v2).lstrip("-").isdigit() and int(v2)>0 else 0
        return max(a, b)
    vals = [v for v in [
        best("elo_li_bullet","elo_cc_bullet"),
        best("elo_li_blitz", "elo_cc_blitz"),
        best("elo_li_rapid", "elo_cc_rapid"),
    ] if v > 0]
    return int(sum(vals)/len(vals)) if vals else 0

def get_best_elos(student):
    mapping = {
        "bullet":    ["elo_li_bullet","elo_cc_bullet"],
        "blitz":     ["elo_li_blitz","elo_cc_blitz"],
        "rapid":     ["elo_li_rapid","elo_cc_rapid"],
        "classical": ["elo_li_classical"],
    }
    result = {}
    for cad, keys in mapping.items():
        for k in keys:
            v = student.get(k,"")
            if v and str(v).lstrip("-").isdigit():
                result[cad] = max(result.get(cad,0), int(v))
    return result

def get_rank(student):
    branch = detect_branch(student)
    ranks = RANKS_MARINE if branch == "marine" else RANKS_PIRATES
    elos = get_best_elos(student)
    avg = get_avg_elo(student)
    peak = get_peak_elo(student)   # conservé uniquement pour l'affichage du pic historique
    best_elo = max(elos.values()) if elos else 0
    best_cad = max(elos, key=elos.get) if elos else "blitz"
    vals = list(elos.values())
    spread = max(vals)-min(vals) if len(vals)>1 else 999
    dom_cad = "balanced" if spread<50 and len(vals)>=3 else best_cad
    suf_label, suf_emoji = CADENCE_SUFFIX.get(dom_cad,("des Mers","🌊"))
    flame = get_flame_status(student)
    rank_locked = student.get("rank_locked", False)
    manual_rank_index = student.get("manual_rank_index")
    if rank_locked and manual_rank_index is not None:
        try:
            r = ranks[int(manual_rank_index)]
            return {"emoji": r[1], "title": r[2], "color": r[3],
                    "best_elo": best_elo, "avg_elo": avg, "peak_elo": peak,
                    "branch": branch, "suf_label": suf_label, "suf_emoji": suf_emoji,
                    "flame": flame, "qg": flame["active"],
                    "rank_locked": True, "manual_rank_index": int(manual_rank_index)}
        except (IndexError, ValueError):
            pass
    # Aucun elo enregistré → rang débutant
    if not avg:
        r = ranks[-1]
        return {"emoji":r[1],"title":r[2],"color":r[3],"best_elo":0,
                "avg_elo":0,"peak_elo":0,"branch":branch,
                "suf_label":"des Mers","suf_emoji":"🌊",
                "flame":get_flame_status(student),"qg":False,
                "rank_locked": False, "manual_rank_index": None}
    # Rang basé sur l'elo moyen (bullet + blitz + rapid)
    for i, (threshold, emoji, title, color) in enumerate(ranks):
        if avg >= threshold:
            return {"emoji":emoji,"title":title,"color":color,
                    "best_elo":best_elo,"avg_elo":avg,"peak_elo":peak,
                    "branch":branch,"suf_label":suf_label,"suf_emoji":suf_emoji,
                    "flame":flame,"qg":flame["active"],
                    "rank_locked": False, "manual_rank_index": i}
    r = ranks[-1]
    return {"emoji":r[1],"title":r[2],"color":r[3],"best_elo":best_elo,
            "avg_elo":avg,"peak_elo":peak,
            "branch":branch,"suf_label":suf_label,"suf_emoji":suf_emoji,
            "flame":flame,"qg":flame["active"],
            "rank_locked": False, "manual_rank_index": len(ranks)-1}

def get_hakis(student):
    elos = get_best_elos(student)
    best_any = max(elos.values()) if elos else 0
    active, seen = [], set()
    manual_hakis = student.get("manual_hakis", [])
    for cad, threshold, key, label, color in HAKI_THRESHOLDS:
        if key in seen: continue
        val = best_any if cad=="any" else elos.get(cad,0)
        if val >= threshold or key in manual_hakis:
            active.append({"key":key,"label":label,"color":color,"manual": key in manual_hakis and val < threshold})
            seen.add(key)
    return active

def get_progression_velocity(student, weeks=4):
    hist = student.get("elo_history",[])
    cutoff = datetime.now() - timedelta(weeks=weeks)
    points = []
    for e in hist:
        try:
            parts = e.get("date","").strip().split("/")
            if len(parts)==3:
                d,m,y = int(parts[0]),int(parts[1]),int(parts[2][:4])
                dt = datetime(y,m,d)
                val = (e.get("elo_li_blitz") or e.get("elo_cc_blitz") or
                       e.get("elo_li") or e.get("elo_cc",""))
                if val and str(val).lstrip("-").isdigit():
                    points.append((dt,int(val)))
        except Exception:
            continue
    points.sort(key=lambda x:x[0])
    recent = [(dt,v) for dt,v in points if dt>=cutoff]
    if len(recent)<2:
        return 0.0,"#333","—",0
    delta = recent[-1][1]-recent[0][1]
    days  = max(1,(recent[-1][0]-recent[0][0]).days)
    vel   = round(delta/days*7,1)
    if vel > 15:   return vel,"#22c55e","🔥 En feu",delta
    elif vel > 5:  return vel,"#4ade80","📈 Progresse",delta
    elif vel > -5: return vel,"#888888","➡️ Stable",delta
    elif vel > -15:return vel,"#f97316","📉 Recule",delta
    else:          return vel,"#ef4444","⬇️ Chute",delta

def get_alerts(students):
    alerts = []
    now = datetime.now()
    for s in students:
        name = s.get("name","?")
        vel,_,badge,_ = get_progression_velocity(s)
        if vel < -15:
            alerts.append({"type":"danger","icon":"📉","text":f"{name} : chute ELO ({vel:+.0f} pts/sem)"})
        elif vel < -5:
            alerts.append({"type":"warn","icon":"📉","text":f"{name} : ELO en recul ({vel:+.0f} pts/sem)"})
        for d in s.get("devoirs",[]):
            if d.get("status")!="✅ Fait":
                try:
                    dp = d.get("due","").split("/")
                    if len(dp)==3:
                        due_dt = datetime(int(dp[2]),int(dp[1]),int(dp[0]))
                        if due_dt < now:
                            alerts.append({"type":"warn","icon":"📚",
                                           "text":f"{name} : devoir en retard — {d.get('title','?')}"})
                except Exception: pass
        last = s.get("li_last_online","")
        if last and last!="—":
            try:
                lp = last.split("/")
                if len(lp)==3:
                    last_dt = datetime(int(lp[2]),int(lp[1]),int(lp[0]))
                    if (now-last_dt).days>14:
                        alerts.append({"type":"info","icon":"💤",
                                       "text":f"{name} : inactif sur Lichess depuis {(now-last_dt).days}j"})
            except Exception: pass
    return alerts

def build_elo_chart_data(student):
    hist = student.get("elo_history",[])
    cadences = {
        "blitz_li":     ("elo_li_blitz","elo_li","#cc1a1a"),
        "blitz_cc":     ("elo_cc_blitz","elo_cc","#fbbf24"),
        "bullet_li":    ("elo_li_bullet",None,"#ff3a1a"),
        "rapid_li":     ("elo_li_rapid",None,"#f472b6"),
        "classical_li": ("elo_li_classical",None,"#a78bfa"),
        "rapid_cc":     ("elo_cc_rapid",None,"#fb923c"),
    }
    series = {k:[] for k in cadences}
    all_dates = []
    for e in sorted(hist, key=lambda x: x.get("date","")):
        date_str = e.get("date","")[:5]
        if date_str: all_dates.append(date_str)
        for key,(f1,f2,col) in cadences.items():
            val = e.get(f1)
            if not val and f2: val = e.get(f2)
            series[key].append(int(val) if val and str(val).lstrip("-").isdigit() else None)
    return {"labels":all_dates,"series":series,"cadences":cadences}

# ─── API fetchers ──────────────────────────────────────────
def _api_get(url):
    req = Request(url,headers={"User-Agent":"GHOST-Chess-Manager/3.0"})
    with urlopen(req,timeout=8) as r:
        return json.loads(r.read().decode())

def fetch_lichess(username):
    username = username.strip()
    data = _api_get(f"https://lichess.org/api/user/{urllib.parse.quote(username)}")
    perfs = data.get("perfs",{})
    def elo(cat):
        r = perfs.get(cat,{}).get("rating","")
        return str(r) if r and str(r).lstrip("-").isdigit() else ""
    def games(cat): return perfs.get(cat,{}).get("games",0)
    total = sum(games(c) for c in ["bullet","blitz","rapid","classical","correspondence"])
    ts = data.get("seenAt",0)
    last = datetime.fromtimestamp(ts/1000).strftime("%d/%m/%Y") if ts else "—"
    return {"elo_bullet":elo("bullet"),"elo_blitz":elo("blitz"),"elo_rapid":elo("rapid"),
            "elo_classical":elo("classical"),"games_total":str(total),
            "title":data.get("title",""),"last_online":last,
            "url":f"https://lichess.org/@/{username}",
            "name":data.get("profile",{}).get("realName","")}

def fetch_chesscom(username):
    username = username.strip()
    profile = _api_get(f"https://api.chess.com/pub/player/{urllib.parse.quote(username)}")
    try: stats = _api_get(f"https://api.chess.com/pub/player/{urllib.parse.quote(username)}/stats")
    except Exception: stats = {}
    def elo(cat):
        last = stats.get(cat,{}).get("last",{})
        r = last.get("rating","") if last else ""
        return str(r) if r and str(r).lstrip("-").isdigit() else ""
    def ngames(cat):
        rec = stats.get(cat,{}).get("record",{})
        return rec.get("win",0)+rec.get("loss",0)+rec.get("draw",0)
    total = sum(ngames(c) for c in ["chess_bullet","chess_blitz","chess_rapid","chess_daily"])
    joined = profile.get("joined",0)
    return {"elo_bullet":elo("chess_bullet"),"elo_blitz":elo("chess_blitz"),
            "elo_rapid":elo("chess_rapid"),"games_total":str(total),
            "country":profile.get("country","").split("/")[-1],
            "joined":datetime.fromtimestamp(joined).strftime("%d/%m/%Y") if joined else "—",
            "url":profile.get("url",f"https://www.chess.com/member/{username}"),
            "title":profile.get("title","")}

def fetch_lichess_games(username, max_games=30):
    username = username.strip()
    url = (f"https://lichess.org/api/games/user/{urllib.parse.quote(username)}"
           f"?max={max_games}&opening=true&clocks=false&evals=false&tags=true")
    req = Request(url,headers={"User-Agent":"GHOST-Chess-Manager/3.0","Accept":"application/x-ndjson"})
    with urlopen(req,timeout=15) as r:
        raw = r.read().decode("utf-8")
    openings = {}
    white_r = {"win":0,"loss":0,"draw":0}
    black_r = {"win":0,"loss":0,"draw":0}
    opening_results = {}
    total = 0
    for line in raw.strip().splitlines():
        if not line.strip(): continue
        try: game = json.loads(line)
        except Exception: continue
        total += 1
        op_name = game.get("opening",{}).get("name","Inconnue").split(":")[0].strip()
        openings[op_name] = openings.get(op_name,0)+1
        if op_name not in opening_results:
            opening_results[op_name] = {"win":0,"loss":0,"draw":0}
        players = game.get("players",{})
        white_id = players.get("white",{}).get("user",{}).get("id","").lower()
        result = game.get("winner","draw")
        if white_id == username.lower():
            if result=="white": white_r["win"]+=1; opening_results[op_name]["win"]+=1
            elif result=="black": white_r["loss"]+=1; opening_results[op_name]["loss"]+=1
            else: white_r["draw"]+=1; opening_results[op_name]["draw"]+=1
        else:
            if result=="black": black_r["win"]+=1; opening_results[op_name]["win"]+=1
            elif result=="white": black_r["loss"]+=1; opening_results[op_name]["loss"]+=1
            else: black_r["draw"]+=1; opening_results[op_name]["draw"]+=1
    top = sorted(openings.items(),key=lambda x:-x[1])[:8]
    top_with_wr = []
    for op,count in top:
        res = opening_results.get(op,{})
        t = res.get("win",0)+res.get("loss",0)+res.get("draw",0)
        wr = int(res.get("win",0)/t*100) if t else 0
        top_with_wr.append({"name":op,"count":count,"winrate":wr,
                             "win":res.get("win",0),"loss":res.get("loss",0),"draw":res.get("draw",0)})
    return {"total":total,"top_openings":top_with_wr,
            "white_results":white_r,"black_results":black_r}

# ─── Data ──────────────────────────────────────────────────
def _default_data():
    return {
        "students": [],
        "group_notes": [],
        "sessions": [],
        "pairs": [],
        "exam_bank": {},
        "price_grid": default_price_grid(),
        "units": [],
        "users": [],
        "registration_codes": [],
        "registration_requests": [],
        "client_notifications": [],
        "client_price_plans": default_client_price_plans(),
    }

def normalize_data(d):
    if not isinstance(d, dict):
        d = _default_data()
    if "students" not in d or not isinstance(d.get("students"), list): d["students"] = []
    if "sessions" not in d: d["sessions"] = []
    if "group_notes" not in d: d["group_notes"] = []
    if "pairs" not in d: d["pairs"] = []
    if "exam_bank" not in d: d["exam_bank"] = {}
    # V22 : la grille officielle est remise en cohérence avec les offres Ghost Academy.
    if "price_grid" not in d or not isinstance(d.get("price_grid"), dict):
        d["price_grid"] = default_price_grid()
    else:
        official_grid = default_price_grid()
        # Les anciennes grilles 1500/2500/17000 etc. sont remplacées par les tarifs validés.
        for _k, _v in official_grid.items():
            d["price_grid"][_k] = _v
    if "units" not in d: d["units"] = []
    if "users" not in d: d["users"] = []
    if "registration_codes" not in d: d["registration_codes"] = []
    if "registration_requests" not in d or not isinstance(d.get("registration_requests"), list): d["registration_requests"] = []
    if "client_notifications" not in d: d["client_notifications"] = []
    if "tournaments" not in d or not isinstance(d.get("tournaments"), list): d["tournaments"] = []
    if "student_messages" not in d or not isinstance(d.get("student_messages"), list): d["student_messages"] = []
    if "payments_log" not in d or not isinstance(d.get("payments_log"), list): d["payments_log"] = []
    if "client_price_plans" not in d: d["client_price_plans"] = default_client_price_plans()
    try:
        if any("€" in str(p.get("price", "")) for p in d.get("client_price_plans", [])):
            d["client_price_plans"] = default_client_price_plans()
    except Exception:
        pass
    # V16 : mise à jour de la grille officielle des offres (FCFA) + ajout des nouvelles formules.
    try:
        d["client_price_plans"] = merge_default_plans(d.get("client_price_plans"))
    except Exception:
        pass
    # V15 : cohérence des formules importées. La séance découverte vaut toujours 1 séance.
    try:
        for p in d.get("client_price_plans", []):
            key = (p.get("key") or "").lower()
            name = (p.get("name") or "").lower()
            if key == "session_30" or "découverte" in name or "decouverte" in name:
                p["sessions_total"] = 1
            elif not safe_int(p.get("sessions_total"), 0):
                p["sessions_total"] = plan_session_total(p)
        reconcile_client_plan_state(d)
    except Exception:
        pass
    return d

def load_data():
    remote = load_state()
    if remote is None:
        remote = bootstrap_from_local_json(DATA_FILE)
    if remote is not None:
        d = normalize_data(remote)
        changed = assign_branches_50_50(d["students"])
        if any(s.get("branch") != d["students"][i].get("branch") for i, s in enumerate(changed)):
            d["students"] = changed
            save_data(d)
        return d

    if os.path.exists(DATA_FILE):
        with open(DATA_FILE,"r",encoding="utf-8") as f:
            d = json.load(f)
        d = normalize_data(d)
        changed = assign_branches_50_50(d["students"])
        if any(s.get("branch") != d["students"][i].get("branch") for i, s in enumerate(changed)):
            d["students"] = changed
            save_data(d)
        return d
    return _default_data()

def save_data(data):
    saved_remote = save_state(data)
    dual_write = os.environ.get("GHOST_DUAL_WRITE_JSON", "0").lower() in ("1", "true", "yes", "on")
    if (not saved_remote) or dual_write:
        with open(DATA_FILE,"w",encoding="utf-8") as f:
            json.dump(data,f,ensure_ascii=False,indent=2)

def enrich_students(students):
    result = []
    for i,s in enumerate(students):
        s = dict(s)
        s["_index"] = i
        s["_rank"] = get_rank(s)
        s["_hakis"] = get_hakis(s)
        vel,badge_color,badge_label,delta = get_progression_velocity(s)
        s["_velocity"] = vel
        s["_badge_color"] = badge_color
        s["_badge_label"] = badge_label
        s["_delta"] = delta
        s["_pending_devoirs"] = len([d for d in s.get("devoirs",[]) if d.get("status")!="✅ Fait"])
        s["_pending_rappels"] = len([r for r in s.get("rappels",[]) if not r.get("done")])
        elos = get_best_elos(s)
        def best_val(*keys):
            for k in keys:
                v = s.get(k,"")
                if v and str(v).lstrip("-").isdigit() and int(v)>0: return int(v)
            return 0
        s["_blitz_elo"]  = max(best_val("elo_li_blitz"),  best_val("elo_cc_blitz"))
        s["_bullet_elo"] = max(best_val("elo_li_bullet"), best_val("elo_cc_bullet"))
        s["_rapid_elo"]  = max(best_val("elo_li_rapid"),  best_val("elo_cc_rapid"))
        s["_avg_elo"]    = get_avg_elo(s)
        s["_sort_elo"]   = s["_avg_elo"]
        s["_island"]     = get_island(s)
        # Calcul % objectif ELO
        elo_target = s.get("elo_target","")
        elo_target_pct = 0
        if elo_target and str(elo_target).isdigit() and elos:
            best = max(elos.values())
            target = int(elo_target)
            hist_elos = [int(e.get("elo_li_blitz") or e.get("elo_li") or e.get("elo_cc") or 0)
                         for e in s.get("elo_history",[])
                         if e.get("elo_li_blitz") or e.get("elo_li") or e.get("elo_cc")]
            start = min(hist_elos) if hist_elos else best
            elo_target_pct = min(100,max(0,int((best-start)/(target-start)*100))) if target!=start else 100
        s["_elo_target_pct"] = elo_target_pct
        result.append(s)
    return result

# ═══════════════════════════════════════════════════════════
# ─── PDF Generation ────────────────────────────────────────
# ═══════════════════════════════════════════════════════════

C_RED    = colors.HexColor("#cc1a1a")
C_DARK   = colors.HexColor("#0a0a0a")
C_BORDER = colors.HexColor("#1a1a1a")
C_GREY   = colors.HexColor("#444444")
C_LIGHT  = colors.HexColor("#cccccc")
C_WHITE  = colors.white
C_MARINE = colors.HexColor("#60a5fa")
C_PIRATE = colors.HexColor("#f472b6")
C_GREEN  = colors.HexColor("#4ade80")
C_AMBER  = colors.HexColor("#fbbf24")

def _ps(name, **kw):
    base = ParagraphStyle(name, fontName="Helvetica", fontSize=10,
                          textColor=C_LIGHT, spaceAfter=4, leading=14)
    for k,v in kw.items(): setattr(base, k, v)
    return base

def _mini_elo_chart(elo_history, width=14*cm, height=3.5*cm):
    points = []
    for e in sorted(elo_history, key=lambda x: x.get("date","")):
        val = (e.get("elo_li_blitz") or e.get("elo_cc_blitz") or
               e.get("elo_li") or e.get("elo_cc",""))
        try:
            v = int(val)
            if v > 0: points.append(v)
        except Exception: pass
    if len(points) < 2: return None
    d = Drawing(width, height)
    d.add(Rect(0,0,width,height, fillColor=colors.HexColor("#0a0a0a"),
               strokeColor=colors.HexColor("#1a1a1a"), strokeWidth=0.5))
    mn, mx = min(points), max(points)
    rng = mx - mn if mx != mn else 1
    px, py = 22, 14
    wi, hi = width-2*px, height-2*py
    n = len(points)
    coords = [(px+(i/(n-1))*wi, py+((v-mn)/rng)*hi) for i,v in enumerate(points)]
    for i in range(len(coords)-1):
        x1,y1 = coords[i]; x2,y2 = coords[i+1]
        d.add(Line(x1,y1,x2,y2, strokeColor=C_RED, strokeWidth=1.5))
    d.add(String(px, py-9, str(mn), fontSize=7, fillColor=C_GREY, fontName="Helvetica"))
    d.add(String(px, py+hi+2, str(mx), fontSize=7, fillColor=C_GREY, fontName="Helvetica"))
    lx,ly = coords[-1]
    d.add(String(lx-12, ly+3, str(points[-1]), fontSize=8, fillColor=C_RED, fontName="Helvetica-Bold"))
    return d

def _in_period(date_str, date_from, date_to):
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            s = date_str.strip()
            dt = datetime.strptime(s[:len(fmt)], fmt).date()
            return date_from <= dt <= date_to
        except Exception: pass
    return False

def generate_ghost_pdf(student, data, date_from, date_to):
    name     = student.get("name","Ghost")
    rank     = student.get("_rank", get_rank(student))
    branch   = rank.get("branch","pirates")
    island   = student.get("_island", get_island(student))
    safe_name = secure_filename(name.replace(" ","_"))
    week_str  = date_from.strftime("%Y-W%V")
    filename  = f"rapport_{safe_name}_{week_str}.pdf"
    filepath  = os.path.join(REPORTS_FOLDER, filename)

    doc = SimpleDocTemplate(filepath, pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.5*cm,  bottomMargin=1.5*cm)
    story = []

    # En-tête
    hd = [[
        Paragraph("<font size='18'><b>⚡ GHOST</b></font>", _ps("h", textColor=C_RED)),
        Paragraph(
            f"<font size='8'>Rapport du {date_from.strftime('%d/%m/%Y')}"
            f" au {date_to.strftime('%d/%m/%Y')}</font>",
            _ps("sub", fontSize=8, textColor=C_GREY, alignment=2)),
    ]]
    ht = Table(hd, colWidths=["60%","40%"])
    ht.setStyle(TableStyle([
        ("ALIGN",(1,0),(1,0),"RIGHT"),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LINEBELOW",(0,0),(-1,0),0.5,C_BORDER),("BOTTOMPADDING",(0,0),(-1,0),8),
    ]))
    story.append(ht); story.append(Spacer(1,10))

    # Identité
    branch_label = "🎖️ Marine" if branch=="marine" else "🏴‍☠️ Pirates"
    id_d = [[
        Paragraph(f"<b><font size='16'>{name}</font></b>", _ps("n", fontSize=16, textColor=C_WHITE)),
        Paragraph(
            f"{rank.get('emoji','')} {rank.get('title','')}<br/>"
            f"<font size='9' color='#888888'>{branch_label} · {island.get('emoji','')} {island.get('name','')}</font>",
            _ps("r", fontSize=11, textColor=C_LIGHT, alignment=2)),
    ]]
    id_t = Table(id_d, colWidths=["55%","45%"])
    id_t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#0a0a0a")),
        ("BOX",(0,0),(-1,-1),0.5,C_BORDER),
        ("TOPPADDING",(0,0),(-1,-1),10),("BOTTOMPADDING",(0,0),(-1,-1),10),
        ("LEFTPADDING",(0,0),(-1,-1),12),("RIGHTPADDING",(0,0),(-1,-1),12),
        ("ALIGN",(1,0),(1,0),"RIGHT"),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ]))
    story.append(id_t); story.append(Spacer(1,14))

    def sec(txt):
        story.append(Paragraph(f"<font color='#cc1a1a'><b>{txt}</b></font>",
                                _ps("st", fontSize=10, textColor=C_RED, spaceBefore=10, spaceAfter=6)))
        story.append(HRFlowable(width="100%", thickness=0.5, color=C_BORDER, spaceAfter=6))

    # ELO
    def bv(*keys):
        for k in keys:
            v = student.get(k,"")
            if v and str(v).lstrip("-").isdigit() and int(v)>0: return int(v)
        return 0
    blitz  = max(bv("elo_li_blitz"),  bv("elo_cc_blitz"))
    bullet = max(bv("elo_li_bullet"), bv("elo_cc_bullet"))
    rapid  = max(bv("elo_li_rapid"),  bv("elo_cc_rapid"))
    avg    = student.get("_avg_elo", get_avg_elo(student))
    vel    = student.get("_velocity", 0)
    vel_str= f"+{vel:.1f}" if vel>=0 else f"{vel:.1f}"
    vc     = "#4ade80" if vel>5 else "#ef4444" if vel<-5 else "#888888"

    sec("⚡ Elo actuel")
    el_d = [["Bullet","Blitz","Rapid","Moy.","Vélocité"],
            [str(bullet) if bullet else "—", str(blitz) if blitz else "—",
             str(rapid)  if rapid  else "—", str(avg)   if avg   else "—",
             Paragraph(f"<font color='{vc}'><b>{vel_str} pts/sem</b></font>",
                       _ps("v", fontSize=10, alignment=1))]]
    el_t = Table(el_d, colWidths=["19%","19%","19%","19%","24%"])
    el_t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#111111")),
        ("BACKGROUND",(0,1),(-1,1),colors.HexColor("#0a0a0a")),
        ("BOX",(0,0),(-1,-1),0.5,C_BORDER),
        ("INNERGRID",(0,0),(-1,-1),0.3,colors.HexColor("#151515")),
        ("TEXTCOLOR",(0,0),(-1,0),C_GREY),("TEXTCOLOR",(0,1),(-1,1),C_WHITE),
        ("FONTNAME",(0,0),(-1,0),"Helvetica"),("FONTNAME",(0,1),(-1,1),"Helvetica-Bold"),
        ("FONTSIZE",(0,0),(-1,-1),10),("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7),
    ]))
    story.append(el_t); story.append(Spacer(1,8))

    chart = _mini_elo_chart(student.get("elo_history",[]))
    if chart:
        story.append(chart); story.append(Spacer(1,4))
        story.append(Paragraph("<font color='#444444' size='8'>Progression Blitz (historique)</font>",
                                _ps("cl", fontSize=8, textColor=C_GREY, alignment=1)))
    story.append(Spacer(1,10))

    target = student.get("elo_target","")
    pct    = student.get("_elo_target_pct", 0)
    if target:
        story.append(Paragraph(
            f"<font color='#fbbf24'>🎯 Objectif ELO :</font> <b>{target}</b>  "
            f"<font size='9' color='#555555'>({pct}% atteint)</font>",
            _ps("obj", fontSize=10, textColor=C_LIGHT)))
        story.append(Spacer(1,10))

    # Présences
    sec("📅 Présences")
    sessions = data.get("sessions",[])
    period_sessions = []
    for sess in sessions:
        try:
            d_str = sess.get("date","")[:10]
            for fmt in ("%d/%m/%Y","%Y-%m-%d"):
                try:
                    d = datetime.strptime(d_str, fmt).date()
                    if date_from <= d <= date_to:
                        period_sessions.append(sess)
                    break
                except: pass
        except: pass
    present_count = sum(1 for s in period_sessions if name in s.get("present",[]))
    total_count   = len(period_sessions)
    pr_d = [["Séances","Présent","Absent"],
            [str(total_count), str(present_count), str(total_count-present_count)]]
    pr_t = Table(pr_d, colWidths=["50%","25%","25%"])
    pr_t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#111111")),
        ("BACKGROUND",(0,1),(-1,1),colors.HexColor("#0a0a0a")),
        ("BOX",(0,0),(-1,-1),0.5,C_BORDER),("INNERGRID",(0,0),(-1,-1),0.3,colors.HexColor("#151515")),
        ("TEXTCOLOR",(0,0),(-1,0),C_GREY),("TEXTCOLOR",(0,1),(0,1),C_LIGHT),
        ("TEXTCOLOR",(1,1),(1,1),C_GREEN),
        ("TEXTCOLOR",(2,1),(2,1),colors.HexColor("#ef4444") if (total_count-present_count)>0 else C_LIGHT),
        ("FONTNAME",(0,0),(-1,-1),"Helvetica"),("FONTNAME",(0,1),(-1,-1),"Helvetica-Bold"),
        ("FONTSIZE",(0,0),(-1,-1),10),("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7),
    ]))
    story.append(pr_t)
    themes = [s.get("theme","") for s in period_sessions if s.get("theme","")]
    if themes:
        story.append(Spacer(1,6))
        story.append(Paragraph(
            f"<font size='9' color='#444444'>Thèmes : </font><font size='9'>{', '.join(themes)}</font>",
            _ps("th", fontSize=9, textColor=C_LIGHT)))
    story.append(Spacer(1,10))

    # Devoirs
    devoirs = student.get("devoirs",[])
    if devoirs:
        sec("📚 Devoirs")
        dv_d = [["Devoir","Statut"]]
        for dv in devoirs:
            status = dv.get("status","📋 À faire")
            cmap = {"✅ Fait":"#4ade80","🔄 En cours":"#fbbf24","📋 À faire":"#888888"}
            sc = cmap.get(status,"#888888")
            dv_d.append([
                Paragraph(dv.get("title","—"), _ps("dt", fontSize=9, textColor=C_LIGHT)),
                Paragraph(f"<font color='{sc}'>{status}</font>", _ps("ds", fontSize=9, alignment=1)),
            ])
        dv_t = Table(dv_d, colWidths=["72%","28%"])
        dv_t.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#111111")),
            ("BACKGROUND",(0,1),(-1,-1),colors.HexColor("#0a0a0a")),
            ("BOX",(0,0),(-1,-1),0.5,C_BORDER),("INNERGRID",(0,0),(-1,-1),0.3,colors.HexColor("#111111")),
            ("TEXTCOLOR",(0,0),(-1,0),C_GREY),("FONTSIZE",(0,0),(-1,-1),9),
            ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
            ("LEFTPADDING",(0,0),(-1,-1),8),("ALIGN",(1,0),(1,-1),"CENTER"),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ]))
        story.append(dv_t); story.append(Spacer(1,10))

    # Examens sur la période
    period_exams = [r for r in student.get("exam_results",[]) if _in_period(r.get("date",""), date_from, date_to)]
    if period_exams:
        sec("🎖️ Examens")
        ex_d = [["Grade","Score","Résultat"]]
        for r in period_exams:
            score = r.get("score",0); total_p = r.get("total",1)
            pct2  = int(score/total_p*100) if total_p else 0
            passed = r.get("passed",False)
            rc = "#4ade80" if passed else "#ef4444"
            ex_d.append([r.get("grade_label","—"), f"{score}/{total_p} ({pct2}%)",
                Paragraph(f"<font color='{rc}'><b>{'✅ Réussi' if passed else '❌ Échoué'}</b></font>",
                          _ps("er", fontSize=9, alignment=1))])
        ex_t = Table(ex_d, colWidths=["45%","30%","25%"])
        ex_t.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#111111")),
            ("BACKGROUND",(0,1),(-1,-1),colors.HexColor("#0a0a0a")),
            ("BOX",(0,0),(-1,-1),0.5,C_BORDER),("INNERGRID",(0,0),(-1,-1),0.3,colors.HexColor("#111111")),
            ("TEXTCOLOR",(0,0),(-1,0),C_GREY),("TEXTCOLOR",(0,1),(1,-1),C_LIGHT),
            ("FONTSIZE",(0,0),(-1,-1),9),("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
            ("LEFTPADDING",(0,0),(-1,-1),8),("ALIGN",(1,0),(-1,-1),"CENTER"),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ]))
        story.append(ex_t); story.append(Spacer(1,10))

    # Remarques coach sur la période
    remarques = [r for r in student.get("remarques",[]) if _in_period(r.get("date",""), date_from, date_to)]
    if remarques:
        sec("💬 Notes du coach")
        for r in remarques:
            story.append(Paragraph(
                f"<font size='8' color='#444444'>{r.get('date','')[:10]}  {r.get('tag','')}</font><br/>"
                f"<font size='9'>{r.get('text','')}</font>",
                _ps("rm", fontSize=9, textColor=C_LIGHT, spaceAfter=4)))
        story.append(Spacer(1,6))

    # Erreurs récurrentes
    errors = student.get("recurring_errors",[])
    if errors:
        sec("⚠️ Points à travailler")
        for e in errors:
            story.append(Paragraph(f"• {e}",
                _ps("er2", fontSize=9, textColor=colors.HexColor("#f97316"), spaceAfter=3)))
        story.append(Spacer(1,6))

    # Pied de page
    story.append(Spacer(1,20))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_BORDER))
    story.append(Spacer(1,4))
    story.append(Paragraph(
        f"<font size='8' color='#333333'>Rapport GHOST · généré le "
        f"{datetime.now().strftime('%d/%m/%Y à %H:%M')} · usage confidentiel</font>",
        _ps("ft", fontSize=8, textColor=C_GREY, alignment=1)))

    doc.build(story)
    return filepath

# ── Job cron : lundi 8h ────────────────────────────────────
@scheduler.task("cron", id="weekly_reports", day_of_week="mon", hour=8, minute=0)
def weekly_reports_job():
    today      = date.today()
    last_mon   = today - timedelta(days=today.weekday() + 7)
    last_sun   = last_mon + timedelta(days=6)
    data       = load_data()
    students   = enrich_students(data["students"])
    for s in students:
        try:
            generate_ghost_pdf(s, data, last_mon, last_sun)
            print(f"[GHOST] Rapport auto : {s.get('name','?')}")
        except Exception as e:
            print(f"[GHOST] Erreur rapport {s.get('name','?')} : {e}")


# ─── Client portal helpers ─────────────────────────────────
PARIS_TZ = ZoneInfo("Europe/Paris")

def now_paris():
    return datetime.now(PARIS_TZ)

def now_iso():
    return now_paris().isoformat(timespec="seconds")

def now_fr():
    return now_paris().strftime("%d/%m/%Y %H:%M")

def student_name_from_index(data, student_index, fallback="Ghost non lié"):
    if isinstance(student_index, int) and 0 <= student_index < len(data.get("students", [])):
        return data["students"][student_index].get("name") or fallback
    return fallback

def telegram_send(chat_id, text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("GHOST_TELEGRAM_BOT_TOKEN")
    if not token or not chat_id or not text:
        return False
    try:
        payload = urllib.parse.urlencode({"chat_id": str(chat_id), "text": text[:3900]}).encode("utf-8")
        req = Request(f"https://api.telegram.org/bot{token}/sendMessage", data=payload, method="POST")
        with urlopen(req, timeout=4) as resp:
            return 200 <= resp.status < 300
    except Exception as e:
        print(f"[GHOST] Telegram notification skipped: {e}")
        return False

def telegram_chat_for_student(data, student_index):
    if isinstance(student_index, int) and 0 <= student_index < len(data.get("students", [])):
        stu = data["students"][student_index]
        if stu.get("telegram_chat_id"):
            return stu.get("telegram_chat_id")
    user = find_user_for_student(data, student_index)
    return user.get("telegram_chat_id") if user else ""
def default_price_grid():
    return {
        "registration_fee": 5000,
        "registration_fee_label": "5 000 FCFA",
        "session_30": 2000,
        "session_60": 3500,
        "pack_progression": 9000,
        "tournament_prep": 10000,
        "ghost_premium": 20000,
        "forfait_note": "Tarifs en FCFA. Paiement à Arthur Simo (+237) 694054282, confirmé par le coach avant activation.",
    }

def default_client_price_plans():
    return [
        {"key":"session_30","name":"Séance découverte","price":"2 000 FCFA","period":"30 minutes","sessions_total":1,"featured":"starter","desc":"Diagnostic rapide, analyse de quelques parties, discussion et conseils ciblés pour débloquer un problème précis."},
        {"key":"session_60","name":"Séance standard","price":"3 500 FCFA","period":"1 heure","sessions_total":1,"featured":"standard","desc":"Offre découverte + suivi ponctuel : analyse, exercices rapides, conseil d’ouverture/tactique et prochaine étape claire."},
        {"key":"pack_progression","name":"Pack progression","price":"9 000 FCFA","period":"suivi mensuel","sessions_total":5,"featured":"progress","desc":"5 séances personnalisées par mois, suivi de la progression, entraînement tactique, devoirs et corrections structurées."},
        {"key":"tournament_prep","name":"Préparation tournoi","price":"10 000 FCFA","period":"4 séances ciblées","sessions_total":4,"featured":"tournament","desc":"Parties contre le coach, analyse de parties, préparation d’ouvertures, gestion du temps, préparation mentale et stratégie de tournoi."},
        {"key":"ghost_premium","name":"Offre Ghost","price":"20 000 FCFA","period":"accompagnement premium mensuel","sessions_total":8,"featured":"ghost","desc":"Accompagnement sur mesure et à la demande : priorité de réponse < 24h, meilleurs joueurs camerounais au choix, assistance aux parties, analyses personnalisées, corrections intensives et préparation aux tournois."},
    ]

def default_amount_for_plan(plan_key):
    mapping = {
        "session_30": "2 000 FCFA",
        "session_60": "3 500 FCFA",
        "pack_progression": "9 000 FCFA",
        "pack_4": "9 000 FCFA",
        "tournament_prep": "10 000 FCFA",
        "ghost_premium": "20 000 FCFA",
        "monthly_plus": "20 000 FCFA",
        "standard": "3 500 FCFA",
    }
    return mapping.get(plan_key or "", "3 500 FCFA")

def parse_fcfa(value):
    return safe_int(str(value or '').replace('FCFA','').replace('F','').replace('fcfa',''), 0)

def format_fcfa(value):
    return f"{safe_int(value,0):,}".replace(',', ' ') + " FCFA"

def merge_default_plans(existing=None):
    defaults = {p['key']: dict(p) for p in default_client_price_plans()}
    out = []
    seen = set()
    for p in existing or []:
        key = p.get('key') or ''
        if key in defaults:
            # V16 : on met à jour les offres officielles et on conserve uniquement d'éventuelles notes personnalisées non critiques.
            merged = dict(defaults[key])
            if p.get('custom_note'):
                merged['custom_note'] = p.get('custom_note')
            out.append(merged); seen.add(key)
        elif key in ('pack_4','monthly_basic','monthly_plus'):
            # anciens plans remplacés par la nouvelle grille
            continue
        else:
            out.append(p); seen.add(key)
    for key, p in defaults.items():
        if key not in seen:
            out.append(dict(p))
    return out


def find_client_plan(data, plan_key):
    if (plan_key or "") in ("no_plan", "app_access", "registration_only", ""): 
        return {"key":"no_plan","name":"Accès Ghost","price":"0 FCFA","period":"Accès à l’app","sessions_total":0,"desc":"Accès validé à la plateforme. Choisis une formule pour démarrer un accompagnement."}
    plans = data.get("client_price_plans") or default_client_price_plans()
    return next((p for p in plans if p.get("key") == plan_key), plans[0] if plans else {"key":"session_60","name":"Séance standard","price":"3 500 FCFA","period":"1 heure","desc":""})

def plan_theme(plan_key):
    key = plan_key or "session_60"
    if key == "ghost_premium":
        return "ghost"
    if key == "tournament_prep":
        return "tournoi"
    if key.startswith("monthly"):
        return "mensuel"
    if key.startswith("pack"):
        return "pack"
    if "30" in key:
        return "decouverte"
    return "standard"


def safe_int(value, default=0):
    try:
        return int(float(str(value).replace(" ", "").replace("FCFA", "").replace(",", ".").strip()))
    except Exception:
        return default

def safe_float(value, default=0.0):
    try:
        return float(str(value).replace(" ", "").replace("FCFA", "").replace(",", ".").strip())
    except Exception:
        return default

def fmt_qty(value):
    v = safe_float(value, 0.0)
    if abs(v - round(v)) < 0.0001:
        return str(int(round(v)))
    # joli affichage pour les fractions courantes côté coach/élève
    known = [(0.25, "1/4"), (1/3, "1/3"), (0.5, "1/2"), (0.75, "3/4")]
    for k, label in known:
        if abs(v-k) < 0.02:
            return label
    return (f"{v:.2f}").rstrip("0").rstrip(".")

app.jinja_env.globals.update(fmt_qty=fmt_qty)

def plan_session_total(plan):
    """Nombre de séances incluses dans une formule.
    V15 : la séance découverte / 30 min est obligatoirement une séance unique.
    """
    if not plan:
        return 1
    key = (plan.get("key") or "").lower()
    period = (plan.get("period") or "").lower()
    name = (plan.get("name") or "").lower()
    txt = " ".join([key, period, name])
    if key == "session_30" or "30" in key or "découverte" in txt or "decouverte" in txt:
        return 1
    explicit = safe_int(plan.get("sessions_total"), None)
    if explicit is not None and explicit > 0:
        return explicit
    if "mens" in txt or "monthly" in txt:
        return 8
    for n in [12, 10, 8, 6, 4, 3, 2]:
        if str(n) in txt:
            return n
    return 1


def normalize_plan_status(status):
    status = (status or "").strip().lower()
    if status in ("epuise", "épuisé", "complete", "completed", "terminé", "termine"):
        return "completed"
    if status in ("pending", "pending_payment", "awaiting", "à confirmer", "a confirmer"):
        return "pending"
    return status or "inactive"

def canonical_plan_total(data_or_plan, plan_key=None):
    """Retourne le nombre officiel de séances. Corrige les anciennes séances découverte en 1/1."""
    if isinstance(data_or_plan, dict) and ("client_price_plans" in data_or_plan or plan_key):
        plan = find_client_plan(data_or_plan, plan_key)
    else:
        plan = data_or_plan
    return max(1, plan_session_total(plan or {}))

def active_plan_is_completed(user):
    active = user.get("active_plan") or {}
    status = normalize_plan_status(active.get("status"))
    total = safe_float(active.get("total_sessions"), 1) or 1
    used = safe_float(active.get("used_sessions"), 0)
    return status == "completed" or (total > 0 and used >= total and status not in ("pending", "inactive"))

def reconcile_client_plan_state(data):
    """Nettoie les vieux états importés : découverte 1/1, forfait terminé != paiement en retard."""
    if not isinstance(data, dict):
        return data
    for p in data.get("client_price_plans", []) or []:
        if (p.get("key") or "").lower() == "session_30" or "découverte" in (p.get("name") or "").lower() or "decouverte" in (p.get("name") or "").lower():
            p["sessions_total"] = 1
    for u in data.get("users", []) or []:
        # V22 : anciennes suspensions automatiques par relances converties en simple relance.
        if u.get("payment_status") == "restricted" and not u.get("banned"):
            u["payment_status"] = "overdue"
            u["access_restricted"] = False
        active = u.get("active_plan") or {}
        if active:
            plan_key = active.get("plan_key") or u.get("plan") or "session_60"
            plan = find_client_plan(data, plan_key)
            total = canonical_plan_total(plan)
            used = max(0.0, min(safe_float(active.get("used_sessions"), 0.0), float(total)))
            active["plan_key"] = plan.get("key")
            active["plan_name"] = plan.get("name")
            active["total_sessions"] = total
            active["used_sessions"] = used
            status = normalize_plan_status(active.get("status"))
            if used >= total and status not in ("pending", "inactive"):
                active["status"] = "completed"
                if not u.get("pending_plan_request") and u.get("payment_status") in ("overdue", "restricted", "pending"):
                    u["payment_status"] = "paid"
                    u["payment_reminders"] = 0
                    u["access_restricted"] = False
            u["active_plan"] = active
    return data

def lichess_analysis_url(fen):
    fen = (fen or "").strip()
    if not fen:
        return "https://lichess.org/analysis"
    # Lichess accepte une FEN dans l'URL d'analyse ; les espaces sont plus lisibles en underscores.
    compact = fen.replace(" ", "_")
    return "https://lichess.org/analysis/" + urllib.parse.quote(compact, safe="/")

def lichess_paste_url():
    return "https://lichess.org/paste"

app.jinja_env.globals.update(lichess_analysis_url=lichess_analysis_url, lichess_paste_url=lichess_paste_url)

def get_user_plan_state(user, selected_plan=None):
    selected_plan = selected_plan or find_client_plan({"client_price_plans": default_client_price_plans()}, user.get("plan"))
    active = user.get("active_plan") or {}
    plan_key = active.get("plan_key") or selected_plan.get("key") or user.get("plan") or "no_plan"
    # V26 : l'accès Ghost de base n'est pas un forfait consommable.
    # On évite les affichages absurdes 0/1 ou "Accès offert" côté Ghost.
    if (plan_key or "") in ("", "no_plan", "app_access", "registration_only"):
        return {
            "plan_key": "no_plan",
            "name": "Accès Ghost",
            "total_sessions": 0,
            "used_sessions": "0",
            "used_sessions_raw": 0.0,
            "remaining_sessions": "—",
            "remaining_sessions_raw": 0.0,
            "percent": 0,
            "status": "inactive",
            "is_completed": False,
            "is_pending": False,
            "is_active": False,
            "last_session_at": active.get("last_session_at", ""),
            "started_at": active.get("started_at", ""),
            "expires_at": active.get("expires_at", ""),
        }
    active_plan = selected_plan if selected_plan.get("key") == plan_key else find_client_plan({"client_price_plans": default_client_price_plans()}, plan_key)
    total = max(1, canonical_plan_total(active_plan))
    used = safe_float(active.get("used_sessions"), 0.0) if active else 0.0
    used = max(0.0, min(used, float(total)))
    if active and active.get("plan_key") == plan_key:
        status = normalize_plan_status(active.get("status") or ("active" if user.get("payment_status") in ("paid", "free") else "pending"))
    else:
        status = "pending" if user.get("payment_status") not in ("paid", "free") else "inactive"
    if used >= total and status in ("active", "epuise", "completed"):
        status = "completed"
    percent = int(round((used / total) * 100)) if total else 0
    remaining = max(0.0, total - used)
    return {
        "plan_key": plan_key,
        "name": active_plan.get("name") or active.get("plan_name") or plan_key,
        "total_sessions": total,
        "used_sessions": fmt_qty(used),
        "used_sessions_raw": used,
        "remaining_sessions": fmt_qty(remaining),
        "remaining_sessions_raw": remaining,
        "percent": percent,
        "status": status,
        "is_completed": status == "completed",
        "is_pending": status == "pending",
        "is_active": status == "active",
        "last_session_at": active.get("last_session_at", ""),
        "started_at": active.get("started_at", ""),
        "expires_at": active.get("expires_at", ""),
    }

def student_pairs_payload(data, student_index):
    if not isinstance(student_index, int) or student_index < 0 or student_index >= len(data.get("students", [])):
        return []
    me = (data["students"][student_index].get("name") or "").strip()
    out = []
    for p in data.get("pairs", []):
        a, b = (p.get("a") or "").strip(), (p.get("b") or "").strip()
        if me and (a == me or b == me):
            partner = b if a == me else a
            out.append({
                "partner": partner,
                "type": p.get("type") or "pair",
                "note": p.get("note") or "",
                "role": p.get("role") or "binôme",
            })
    return out

def student_calendar_payload(student):
    if not student:
        return []
    rows = []
    for a in student.get("client_appointments", []):
        if a.get("status") in ("accepté", "proposition", "demandé", "à revoir"):
            rows.append({
                "id": a.get("id"),
                "day": a.get("day"),
                "time": a.get("time"),
                "status": a.get("status"),
                "reason": a.get("reason", ""),
                "proposed_day": a.get("proposed_day", ""),
                "proposed_time": a.get("proposed_time", ""),
            })
    rows.sort(key=lambda x: (x.get("day") or "9999-99-99", x.get("time") or "99:99"))
    return rows

def is_image_url(url):
    u = (url or "").lower().split("?")[0]
    return u.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"))

app.jinja_env.globals.update(is_image_url=is_image_url)

def upload_many_from_request(prefix="file"):
    files = request.files.getlist("files") or request.files.getlist("file")
    urls = []
    for f in files:
        if not f or not f.filename:
            continue
        if not allowed_file(f.filename):
            continue
        filename = secure_filename(f"{prefix}_{uuid.uuid4().hex[:10]}_{f.filename}")
        content = f.read()
        remote_url = upload_bytes(filename, content, prefix=prefix) if storage_configured() else None
        if remote_url:
            urls.append(remote_url)
            continue
        path = os.path.join(CLIENT_UPLOAD_FOLDER, filename)
        with open(path, "wb") as out:
            out.write(content)
        urls.append(f"/static/uploads/client/{filename}")
    return urls

def grandline_payload(data, current_idx=None):
    students = enrich_students(data.get("students", []))
    rows = []
    for i, st in enumerate(students):
        island = get_island(st)
        rank = get_rank(st)
        rows.append({
            "index": i,
            "name": st.get("name") or "Ghost",
            "avg_elo": get_avg_elo(st),
            "island": island,
            "rank_title": rank.get("title"),
            "rank_emoji": rank.get("emoji"),
            "branch": rank.get("branch"),
            "is_me": i == current_idx,
        })
    rows.sort(key=lambda x: (x["island"].get("threshold",0), x["avg_elo"]), reverse=True)
    return rows

def get_user_payment_state(user):
    raw_status = user.get("payment_status") or "pending"
    active = user.get("active_plan") or {}
    active_status = normalize_plan_status(active.get("status"))
    has_pending_plan = bool(user.get("pending_plan_request"))

    labels = {
        "paid": ("✅ À jour", "ok"),
        "pending": ("⏳ Paiement à confirmer", "warn"),
        "overdue": ("⚠️ Paiement en attente", "danger"),
        "restricted": ("🔒 Accès restreint", "danger"),
        "free": ("🎟️ Accès Ghost validé", "ok"),
    }
    # Un forfait consommé à 100% n'est pas une dette. C'est une fin normale.
    # On corrige aussi les anciens états V13 où un forfait épuisé avait été marqué par erreur "overdue".
    if active_status == "completed" and not has_pending_plan:
        label, tone = "✅ Forfait terminé", "ok"
        status = "free" if raw_status == "free" else "paid"
    else:
        status = raw_status
        label, tone = labels.get(status, labels["pending"])

    return {
        "status": status,
        "label": label,
        "tone": tone,
        "next_due": user.get("next_due", ""),
        "amount_due": user.get("amount_due", ""),
        "last_payment": user.get("last_payment", ""),
        "note": user.get("payment_note", ""),
        "reminders": int(user.get("payment_reminders") or 0),
        "restricted": bool(user.get("access_restricted") or raw_status == "restricted"),
        "has_pending_plan": has_pending_plan,
        "active_plan_status": active_status,
    }

def add_student_feedback(data, student_index, title, text, kind="feedback", linked_type="manual", linked_id=None, image_url="", position_fen="", pgn="", tags=None, priority="normal", action_required=False, attachments=None):
    if not isinstance(student_index, int) or student_index < 0 or student_index >= len(data.get("students", [])):
        return None
    student = data["students"][student_index]
    title = title or "Feedback coach"
    text = text or ""
    kind = kind or "feedback"
    linked_type = linked_type or "manual"
    # V26 : notifications Ghost plus intelligentes.
    # Si exactement le même message système existe déjà récemment, on le remet simplement en non-lu
    # au lieu d'empiler 5 fois la même notification quand le coach reclique sur un bouton.
    recent = student.setdefault("client_feedback", [])[:20]
    if kind in {"payment", "account", "appointment", "system"}:
        for old in recent:
            if (old.get("title") == title and old.get("text") == text and old.get("kind") == kind and
                old.get("linked_type") == linked_type and (old.get("linked_id") or None) == (linked_id or None)):
                old["date"] = now_fr()
                old["read_by_student"] = False
                old["priority"] = priority or old.get("priority") or "normal"
                old["action_required"] = bool(action_required)
                return old
    entry = {
        "id": str(uuid.uuid4()),
        "date": now_fr(),
        "title": title,
        "text": text,
        "kind": kind,
        "linked_type": linked_type,
        "linked_id": linked_id,
        "image_url": image_url or "",
        "position_fen": position_fen or "",
        "pgn": pgn or "",
        "tags": tags or [],
        "attachments": attachments or ([] if not image_url else [image_url]),
        "priority": priority or "normal",
        "action_required": bool(action_required),
        "read_by_student": False,
    }
    student.setdefault("client_feedback", []).insert(0, entry)
    student["client_feedback"] = student.get("client_feedback", [])[:80]
    chat_id = telegram_chat_for_student(data, student_index)
    if chat_id:
        telegram_send(chat_id, f"GHOST Academy\n{title}\n{text}".strip())
    return entry

def find_user_for_student(data, student_index):
    return next((u for u in data.get("users", []) if u.get("student_index") == student_index), None)


def get_current_user(data=None):
    uid = session.get("client_user_id")
    if not uid:
        return None
    data = data or load_data()
    return next((u for u in data.get("users", []) if u.get("id") == uid), None)

def notification_target_url(kind="info", student_index=None, item_id=None):
    if isinstance(student_index, int):
        student_base = f"/student/{student_index}"
        if kind in ("game", "analysis"):
            return student_base + "#parties"
        if kind == "appointment":
            return student_base + "#agenda"
        if kind == "homework":
            return student_base + "#devoirs"
        if kind == "payment":
            return student_base + "#finances"
        if kind == "tournament":
            return student_base + "#agenda"
        if kind in ("feedback", "feedback_reply", "message", "note", "elo", "account", "info"):
            return student_base + "#echanges"
    base = "/admin/clients"
    if kind == "game": return base + "#games"
    if kind == "appointment": return base + "#appointments"
    if kind == "homework": return base + "#homework"
    if kind == "payment": return base + "#payments"
    if kind == "account": return base + "#accounts"
    if kind in ("note", "elo"): return base + "#notifications"
    return base + "#notifications"

def add_client_notification(data, title, text, kind="info", user_id=None, student_index=None, target_url=None, item_id=None):
    target = target_url or notification_target_url(kind, student_index, item_id)
    data.setdefault("client_notifications", []).insert(0, {
        "id": str(uuid.uuid4()),
        "title": title,
        "text": text,
        "kind": kind,
        "user_id": user_id,
        "student_index": student_index,
        "item_id": item_id,
        "target_url": target,
        "date": now_iso(),
        "read": False,
    })
    data["client_notifications"] = data.get("client_notifications", [])[:120]
    admin_chat_id = (os.environ.get("TELEGRAM_ADMIN_CHAT_ID") or
                     os.environ.get("GHOST_TELEGRAM_ADMIN_CHAT_ID") or
                     os.environ.get("TELEGRAM_COACH_CHAT_ID"))
    if admin_chat_id:
        who = student_name_from_index(data, student_index, "")
        suffix = f" - {who}" if who else ""
        telegram_send(admin_chat_id, f"GHOST Academy\n{title}{suffix}\n{text}\n{target}".strip())


def calculate_age_from_birthdate(birthdate):
    """Calcule l'âge depuis une date YYYY-MM-DD sans casser si la date est vide/invalide."""
    if not birthdate:
        return ""
    try:
        d = datetime.strptime(str(birthdate)[:10], "%Y-%m-%d").date()
        today = datetime.now().date()
        age = today.year - d.year - ((today.month, today.day) < (d.month, d.day))
        return age if 0 <= age <= 120 else ""
    except Exception:
        return ""

def public_student_payload(student):
    if not student:
        return None
    rank = get_rank(student)
    island = get_island(student)
    vel, badge_color, badge_label, delta = get_progression_velocity(student)
    all_feedback = student.get("client_feedback", [])[:40]
    pedagogic_kinds = {"feedback", "game", "homework", "analysis", "lesson"}
    coach_feedback = [f for f in all_feedback if (f.get("kind") or "feedback") in pedagogic_kinds]
    divers = [f for f in all_feedback if (f.get("kind") or "feedback") not in pedagogic_kinds]
    ghost_notifications = []
    for f in all_feedback:
        if not f.get("read_by_student"):
            k = (f.get("kind") or "feedback")
            ghost_notifications.append({
                "id": f.get("id"),
                "title": f.get("title") or "Notification",
                "text": f.get("text") or "",
                "date": f.get("date") or "",
                "kind": k,
                "target_tab": "feedback" if k in pedagogic_kinds else "divers",
            })
    return {
        "name": student.get("name", "Ghost"),
        "lichess": student.get("lichess", ""),
        "chesscom": student.get("chesscom", ""),
        "email": student.get("email", ""),
        "phone": student.get("phone", ""),
        "telegram_chat_id": student.get("telegram_chat_id", ""),
        "city": student.get("city", ""),
        "birthdate": student.get("birthdate", ""),
        "age": calculate_age_from_birthdate(student.get("birthdate")) or student.get("age", ""),
        "goal": student.get("goal", ""),
        "style": student.get("style", ""),
        "interests": student.get("interests", ""),
        "strengths": student.get("strengths", ""),
        "weaknesses": student.get("weaknesses", ""),
        "special_difficulties": student.get("special_difficulties", ""),
        "avatar": student.get("avatar", ""),
        "avg_elo": get_avg_elo(student),
        "rank": rank,
        "island": island,
        "velocity": vel,
        "hakis": get_hakis(student),
        "badge_color": badge_color,
        "badge_label": badge_label,
        "delta": delta,
        "devoirs": student.get("devoirs", [])[-20:],
        "workplans": student.get("workplans", [])[-5:],
        "agenda": student.get("agenda", [])[-8:],
        "client_games": student.get("client_games", [])[:20],
        "client_notes": student.get("client_notes", [])[:12],
        "client_appointments": student.get("client_appointments", [])[:12],
        "payments": student.get("payments", [])[-30:],
        "client_feedback": coach_feedback[:20],
        "client_divers": divers[:20],
        "client_notifications": ghost_notifications[:20],
        "client_notifications_count": len(ghost_notifications),
    }

def require_client_json():
    data = load_data()
    user = get_current_user(data)
    if not user:
        return data, None, jsonify({"ok": False, "error": "not_authenticated"}), 401
    return data, user, None, None

def client_is_restricted(user):
    # V22 : les relances paiement ne suspendent plus automatiquement un compte.
    # Seule une suspension/bannissement explicite du coach limite l’accès.
    return bool(user.get("access_restricted") or user.get("banned"))

def restricted_response(user):
    return jsonify({
        "ok": False,
        "error": "access_restricted",
        "message": "Ton accès est temporairement suspendu par le coach. Contacte-le pour réactiver ton espace Ghost.",
        "reminders": int(user.get("payment_reminders") or 0),
    }), 403


def unread_notifications(data, limit=10):
    rows = []
    for n in data.get("client_notifications", []):
        if not n.get("read"):
            row = dict(n)
            row.setdefault("target_url", notification_target_url(row.get("kind"), row.get("student_index"), row.get("item_id")))
            rows.append(row)
    return rows[:limit]


def finance_summary(data):
    """Résumé financier réel : basé d'abord sur l'historique des paiements validés, puis fallback anciens comptes."""
    paid = pending = 0
    by_plan = {}
    logs = data.get("payments_log") or []
    if logs:
        for rec in logs:
            amount = safe_int(rec.get("amount") or parse_fcfa(rec.get("amount_label")), 0)
            status = rec.get("status") or "paid"
            plan_name = rec.get("plan") or rec.get("kind") or "Paiement"
            if status in ("paid", "validated", "confirmed"):
                paid += amount
                by_plan[plan_name] = by_plan.get(plan_name, 0) + amount
            elif status in ("pending", "awaiting_validation"):
                pending += amount
    else:
        users = data.get("users", [])
        plans_by_key = {p.get("key"): p for p in (data.get("client_price_plans") or default_client_price_plans())}
        for u in users:
            plan = plans_by_key.get(u.get("plan")) or find_client_plan(data, u.get("plan"))
            amount = parse_fcfa(u.get("amount_due") or plan.get("price"))
            if u.get("payment_status") in ("paid", "free"):
                paid += amount
                by_plan[plan.get("name", "Formule")] = by_plan.get(plan.get("name", "Formule"), 0) + amount
            elif u.get("payment_status") in ("pending", "overdue", "restricted", None, "") or u.get("pending_plan_request"):
                pending += amount
    # V30 : inclut aussi les versements historiques saisis directement dans les fiches Ghosts,
    # même s'ils n'ont pas encore été recopiés dans payments_log.
    seen_payment_ids = {rec.get("id") for rec in logs if rec.get("id")}
    for idx, stu in enumerate(data.get("students", []) or []):
        for pmt in stu.get("payments", []) or []:
            pid = pmt.get("id")
            if pid and pid in seen_payment_ids:
                continue
            amount = safe_int(pmt.get("amount"), 0)
            label = pmt.get("label") or "Versement fiche Ghost"
            status = pmt.get("status") or "paid"
            if status in ("paid", "validated", "confirmed"):
                paid += amount
                by_plan[label] = by_plan.get(label, 0) + amount
            elif status in ("pending", "awaiting_validation"):
                pending += amount
    # demandes d'inscription en attente : visibilité dans la finance mais non encaissé validé
    for req in data.get("registration_requests", []) or []:
        if req.get("status") in ("pending_validation", "pending"):
            pending += safe_int(req.get("amount"), 5000)
    return {"paid": paid, "pending": pending, "total": paid + pending, "by_plan": by_plan, "paid_fmt": format_fcfa(paid), "pending_fmt": format_fcfa(pending), "total_fmt": format_fcfa(paid+pending)}

def tournament_target_indices(data, tournament):
    """Renvoie les index d'élèves invités au tournoi."""
    if tournament.get("target_all"):
        return [i for i, _ in enumerate(data.get("students", []))]
    out = []
    for x in tournament.get("targets") or []:
        try:
            out.append(int(x))
        except Exception:
            continue
    # ordre stable, sans doublon
    seen = set(); clean = []
    for i in out:
        if i not in seen:
            seen.add(i); clean.append(i)
    return clean

def enrich_tournament(data, tournament, student_index=None):
    """Ajoute des stats d'affichage sans casser les anciens tournois stockés."""
    t = dict(tournament or {})
    responses = t.get("responses") or {}
    invited = tournament_target_indices(data, t)
    accepted = 0
    unavailable = 0
    answered = 0
    for i in invited:
        r = responses.get(str(i)) or responses.get(i) or {}
        val = (r.get("response") if isinstance(r, dict) else r) or ""
        if val:
            answered += 1
        if val == "participe":
            accepted += 1
        elif val == "indisponible":
            unavailable += 1
    t["_invited"] = len(invited)
    t["_accepted"] = accepted
    t["_unavailable"] = unavailable
    t["_pending"] = max(0, len(invited) - answered)
    if student_index is not None:
        rr = responses.get(str(student_index)) or responses.get(student_index) or {}
        t["_my_response"] = (rr.get("response") if isinstance(rr, dict) else rr) or ""
    return t

def enriched_tournaments(data, limit=30, student_index=None):
    return [enrich_tournament(data, t, student_index) for t in data.get("tournaments", [])[:limit]]

def visible_tournaments_for_student(data, student_index):
    out=[]
    for t in data.get("tournaments", []):
        targets=t.get("targets") or []
        if t.get("target_all") or str(student_index) in [str(x) for x in targets] or student_index in targets:
            out.append(enrich_tournament(data, t, student_index))
    return out[:20]

def visible_messages_for_student(data, student_index):
    me = str(student_index)
    out=[]
    for m in data.get("student_messages", []):
        targets=[str(x) for x in m.get("targets", [])]
        if m.get("target_all") or me in targets or str(m.get("from_student_index")) == me:
            out.append(m)
    return out[:50]

def student_contacts_payload(data, student_index):
    """Contacts visibles dans l'espace Ghost.
    V17 : on ne limite plus les échanges aux seuls binômes.
    L'élève peut envoyer à tous, à ses binômes, ou choisir un élève précis.
    """
    contacts = []
    for i, s in enumerate(data.get("students", [])):
        if i == student_index:
            continue
        name = (s.get("name") or "?").strip() or "?"
        contacts.append({
            "index": i,
            "name": name,
            "elo": get_avg_elo(s),
            "island": get_island(s).get("name"),
            "rank": get_rank(s).get("title"),
        })
    contacts.sort(key=lambda x: (x.get("name") or "").lower())
    return contacts

# ═══════════════════════════════════════════════════════════
# ─── Routes ────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════


@app.route("/health")
def health():
    return jsonify({"ok": True, "app": "ghost-chess", "version": "v24", "port": 5023, "backend": backend_name(), "storage": "supabase" if storage_configured() else "local"})

@app.route("/app")
def app_shell():
    data = load_data()
    students = enrich_students(data["students"])
    return render_template("shell.html", students=students, notifications=unread_notifications(data, 10))

@app.route("/")
def index():
    # Sans ?embed=1, on ouvre la nouvelle app-shell pour garder la musique et le fond animés.
    if request.args.get("embed") != "1":
        data = load_data()
        students = enrich_students(data["students"])
        return render_template("shell.html", students=students, notifications=unread_notifications(data, 10))
    data = load_data()
    students = enrich_students(data["students"])
    sorted_students = sorted(students,key=lambda s:s["_sort_elo"],reverse=True)
    alerts = get_alerts(data["students"])
    pending_devoirs = sum(s["_pending_devoirs"] for s in students)
    pending_rappels = sum(s["_pending_rappels"] for s in students)
    ranks_pirates_data = [{"idx":i,"threshold":r[0],"emoji":r[1],"title":r[2],"color":r[3]} for i,r in enumerate(RANKS_PIRATES)]
    ranks_marine_data  = [{"idx":i,"threshold":r[0],"emoji":r[1],"title":r[2],"color":r[3]} for i,r in enumerate(RANKS_MARINE)]
    return render_template("index.html",
        embed=(request.args.get("embed") == "1"),
        students=students,sorted_students=sorted_students,alerts=alerts,
        sessions=data["sessions"],pending_devoirs=pending_devoirs,
        pending_rappels=pending_rappels,openings=OPENINGS,
        work_themes=WORK_THEMES,devoir_status=DEVOIR_STATUS,
        ranks_pirates=ranks_pirates_data, ranks_marine=ranks_marine_data,
        haki_thresholds=HAKI_THRESHOLDS,
        pairs=data.get("pairs",[]),
        dashboard_tournaments=enriched_tournaments(data, 3),
        price_grid=data.get("price_grid",{}),
        price_plans=data.get("client_price_plans") or default_client_price_plans(),
        visit_stats=data.get("visit_stats", {}))

@app.route("/student/<int:idx>")
def student_page(idx):
    data = load_data()
    if idx >= len(data["students"]): return redirect("/")
    s = data["students"][idx]
    rank = get_rank(s)
    hakis = get_hakis(s)
    vel,badge_color,badge_label,delta = get_progression_velocity(s)
    elos = get_best_elos(s)
    chart_data = build_elo_chart_data(s)
    elo_target = s.get("elo_target","")
    elo_target_pct = 0
    if elo_target and str(elo_target).isdigit() and elos:
        best = max(elos.values())
        target = int(elo_target)
        hist_elos = [int(e.get("elo_li_blitz") or e.get("elo_li") or e.get("elo_cc") or 0)
                     for e in s.get("elo_history",[]) if e.get("elo_li_blitz") or e.get("elo_li") or e.get("elo_cc")]
        start = min(hist_elos) if hist_elos else best
        elo_target_pct = min(100,max(0,int((best-start)/(target-start)*100))) if target!=start else 100
    if s.get("games_analysis"):
        tops = s["games_analysis"].get("top_openings",[])
        if tops and isinstance(tops[0],(list,tuple)):
            s["games_analysis"]["top_openings"] = [
                {"name":o[0],"count":o[1],"winrate":0,"win":0,"loss":0,"draw":0}
                for o in tops]
    alerts = get_alerts([s])
    ranks_pirates_data = [{"idx":i,"threshold":r[0],"emoji":r[1],"title":r[2],"color":r[3]} for i,r in enumerate(RANKS_PIRATES)]
    ranks_marine_data  = [{"idx":i,"threshold":r[0],"emoji":r[1],"title":r[2],"color":r[3]} for i,r in enumerate(RANKS_MARINE)]
    haki_list = [{"cad":h[0],"threshold":h[1],"key":h[2],"label":h[3],"color":h[4]} for h in HAKI_THRESHOLDS]
    island = get_island(s)
    islands_list = []
    seen2 = set()
    for t, e, n, d, c in ISLANDS:
        if n not in seen2:
            seen2.add(n)
            islands_list.append({"threshold": t, "emoji": e, "name": n, "desc": d, "color": c})
    return render_template("student.html",
        s=s,idx=idx,rank=rank,hakis=hakis,vel=vel,badge_color=badge_color,
        badge_label=badge_label,delta=delta,elos=elos,
        chart_data=chart_data,elo_target_pct=elo_target_pct,alerts=alerts,
        openings=OPENINGS,devoir_status=DEVOIR_STATUS,
        comportement=COMPORTEMENT,work_themes=WORK_THEMES,
        recurring_errors=RECURRING_ERRORS,now_date=datetime.now().strftime("%Y-%m-%d"),
        ranks_pirates=ranks_pirates_data, ranks_marine=ranks_marine_data,
        haki_list=haki_list,
        island=island, islands_list=islands_list,
        exam_bank=data.get("exam_bank",{}),
        price_grid=data.get("price_grid",{}),
        price_plans=data.get("client_price_plans") or default_client_price_plans(),
        pairs=data.get("pairs",[]),
        students=enrich_students(data["students"]))

@app.route("/islands")
def islands_page():
    data = load_data()
    students = enrich_students(data["students"])
    island_groups = {}
    for isl in ISLANDS:
        name = isl[2]
        if name not in island_groups:
            island_groups[name] = {"name": name, "emoji": isl[1], "desc": isl[3],
                "color": isl[4], "threshold": isl[0], "students": []}
    for s in students:
        iname = s["_island"]["name"]
        if iname in island_groups:
            island_groups[iname]["students"].append(s)
    seen = set()
    ordered = []
    for isl in ISLANDS:
        name = isl[2]
        if name not in seen:
            seen.add(name)
            ordered.append(island_groups[name])
    seen2 = set()
    islands_list = []
    for t, e, n, d, c in ISLANDS:
        if n not in seen2:
            seen2.add(n)
            islands_list.append({"threshold": t, "emoji": e, "name": n, "desc": d, "color": c})
    return render_template("islands.html",
        islands=ordered, islands_list=islands_list, all_students=students)

# ── Nouvelles pages ────────────────────────────────────────

@app.route("/bank")
def bank_page():
    data = load_data()
    ranks_pirates_data = [{"idx":i,"threshold":r[0],"emoji":r[1],"title":r[2],"color":r[3]} for i,r in enumerate(RANKS_PIRATES)]
    ranks_marine_data  = [{"idx":i,"threshold":r[0],"emoji":r[1],"title":r[2],"color":r[3]} for i,r in enumerate(RANKS_MARINE)]
    return render_template("bank.html",
        exam_bank=data.get("exam_bank",{}),
        ranks_pirates=ranks_pirates_data,
        ranks_marine=ranks_marine_data)

@app.route("/exam")
def exam_page():
    data = load_data()
    students = enrich_students(data["students"])
    ranks_pirates_data = [{"idx":i,"threshold":r[0],"emoji":r[1],"title":r[2],"color":r[3]} for i,r in enumerate(RANKS_PIRATES)]
    ranks_marine_data  = [{"idx":i,"threshold":r[0],"emoji":r[1],"title":r[2],"color":r[3]} for i,r in enumerate(RANKS_MARINE)]
    return render_template("exam.html",
        students=students,
        exam_bank=data.get("exam_bank",{}),
        ranks_pirates=ranks_pirates_data,
        ranks_marine=ranks_marine_data)

@app.route("/reports")
def reports_page():
    data = load_data()
    students = enrich_students(data["students"])
    pdfs = []
    if os.path.exists(REPORTS_FOLDER):
        for f in sorted(os.listdir(REPORTS_FOLDER), reverse=True):
            if f.endswith(".pdf"):
                fpath = os.path.join(REPORTS_FOLDER, f)
                pdfs.append({
                    "filename": f,
                    "size_kb":  round(os.path.getsize(fpath)/1024, 1),
                    "date":     datetime.fromtimestamp(os.path.getmtime(fpath)).strftime("%d/%m/%Y %H:%M"),
                })
    return render_template("reports.html", students=students, pdfs=pdfs)

@app.route("/reports/download/<filename>")
def download_report(filename):
    safe = secure_filename(filename)
    return send_from_directory(REPORTS_FOLDER, safe, as_attachment=True)

# ── API Upload image ───────────────────────────────────────
@app.route("/api/upload/image", methods=["POST"])
def upload_image():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "no file"})
    f = request.files["file"]
    if not f or not allowed_file(f.filename):
        return jsonify({"ok": False, "error": "type non autorisé"})
    ext   = f.filename.rsplit(".", 1)[1].lower()
    fname = f"{uuid.uuid4().hex[:10]}.{ext}"
    f.save(os.path.join(UPLOAD_FOLDER, fname))
    return jsonify({"ok": True, "url": f"/static/uploads/exam/{fname}"})

# ── API Rapports ───────────────────────────────────────────
@app.route("/api/reports/generate", methods=["POST"])
def generate_reports():
    body = request.json or {}
    if body.get("week_offset") is not None:
        offset  = int(body["week_offset"])
        today   = date.today()
        monday  = today - timedelta(days=today.weekday()) - timedelta(weeks=offset)
        sunday  = monday + timedelta(days=6)
    else:
        try:
            monday = datetime.strptime(body["date_from"], "%Y-%m-%d").date()
            sunday = datetime.strptime(body["date_to"],   "%Y-%m-%d").date()
        except Exception:
            today  = date.today()
            monday = today - timedelta(days=today.weekday() + 7)
            sunday = monday + timedelta(days=6)

    data     = load_data()
    students = enrich_students(data["students"])
    indices  = body.get("student_indices")
    if indices is not None:
        students = [s for s in students if s.get("_index") in indices]

    results = []
    for s in students:
        try:
            path  = generate_ghost_pdf(s, data, monday, sunday)
            results.append({"name": s.get("name","?"), "file": os.path.basename(path), "ok": True})
        except Exception as e:
            results.append({"name": s.get("name","?"), "error": str(e), "ok": False})

    return jsonify({"ok": True, "results": results,
                    "period": f"{monday.strftime('%d/%m/%Y')} → {sunday.strftime('%d/%m/%Y')}"})

# ── Routes existantes (inchangées) ─────────────────────────

@app.route("/api/students/island", methods=["POST"])
def set_student_island():
    data = load_data()
    body = request.json
    idx = body.get("index")
    if idx is None or idx >= len(data["students"]):
        return jsonify({"ok": False, "error": "invalid index"})
    if body.get("reset"):
        data["students"][idx].pop("island_override", None)
    else:
        data["students"][idx]["island_override"] = body.get("island", "")
    save_data(data)
    return jsonify({"ok": True})

@app.route("/session/live")
def session_live():
    data = load_data()
    return render_template("session.html",students=data["students"],openings=OPENINGS)

@app.route("/api/students/save",methods=["POST"])
def save_student():
    data = load_data()
    student = request.json
    idx = student.get("_index")
    student["updated"] = now_fr()
    if not student.get("created"): student["created"] = student["updated"]
    clean = {k:v for k,v in student.items() if not k.startswith("_")}
    if idx is not None and 0<=idx<len(data["students"]):
        existing = data["students"][idx]
        ELO_FIELDS = ["elo_li_blitz","elo_li_bullet","elo_li_rapid","elo_li_classical",
                      "elo_cc_blitz","elo_cc_bullet","elo_cc_rapid",
                      "li_games","li_last_online","cc_games","cc_joined","elo_otb","elo_target",
                      "recurring_errors"]
        for preserve in ["devoirs","remarques","rappels","elo_history","journal",
                         "game_analyses","games_analysis","work_plan",
                         "branch_locked","rank_locked","manual_rank_index","manual_hakis"] + ELO_FIELDS:
            if preserve in existing and (preserve not in clean or not clean.get(preserve)):
                clean[preserve] = existing[preserve]
        if clean.get("birthdate"):
            age = calculate_age_from_birthdate(clean.get("birthdate"))
            if age != "":
                clean["age"] = age
        data["students"][idx] = clean
    else:
        data["students"].append(clean)
    save_data(data)
    return jsonify({"ok":True,"index":idx if idx is not None else len(data["students"])-1})

@app.route("/api/students/set_rank",methods=["POST"])
def set_student_rank():
    data = load_data()
    body = request.json
    idx = body.get("index")
    if idx is None or idx >= len(data["students"]):
        return jsonify({"ok":False,"error":"invalid index"})
    s = data["students"][idx]
    if "branch_locked" in body:
        s["branch_locked"] = bool(body["branch_locked"])
        if body["branch_locked"] and body.get("branch"):
            s["branch"] = body["branch"]
    if "rank_locked" in body:
        s["rank_locked"] = bool(body["rank_locked"])
        if body["rank_locked"] and body.get("manual_rank_index") is not None:
            s["manual_rank_index"] = int(body["manual_rank_index"])
        elif not body["rank_locked"]:
            s.pop("manual_rank_index", None)
    if "manual_hakis" in body:
        s["manual_hakis"] = body["manual_hakis"]
    data["students"][idx] = s
    save_data(data)
    return jsonify({"ok":True})

@app.route("/api/students/delete",methods=["POST"])
def delete_student():
    data = load_data()
    idx = request.json.get("index")
    if idx is not None and 0<=idx<len(data["students"]):
        data["students"].pop(idx); save_data(data)
    return jsonify({"ok":True})

@app.route("/api/students/devoir",methods=["POST"])
def update_devoir():
    data = load_data()
    body = request.json
    idx = body["student_index"]
    s = data["students"][idx]
    action = body.get("action")
    if action == "add":
        entry = {
            "id": str(uuid.uuid4()),
            "title": body["title"],
            "due": body.get("due", ""),
            "status": "📋 À faire",
            "note": body.get("note", ""),
            "attachments": body.get("attachments") or [],
            "created_at": now_fr(),
            "source": "coach",
        }
        s.setdefault("devoirs", []).append(entry)
        add_student_feedback(data, idx, "Nouveau devoir", f"{entry['title']} — à rendre pour le {entry.get('due') or 'prochainement'}. {entry.get('note','')}", "homework", "devoir", entry["id"], action_required=True)
        if entry["attachments"] and s.get("client_feedback"):
            s["client_feedback"][0]["attachments"] = entry["attachments"]
    elif action == "status":
        di = body["devoir_index"]
        s["devoirs"][di]["status"] = body["status"]
        s["devoirs"][di]["updated_at"] = now_fr()
        if body["status"] in ("✅ Corrigé", "✅ Fait"):
            add_student_feedback(data, idx, "Devoir corrigé", f"Ton devoir ‘{s['devoirs'][di].get('title','')}’ a été corrigé par le coach.", "homework", "devoir", s["devoirs"][di].get("id"))
    elif action == "delete":
        s["devoirs"].pop(body["devoir_index"])
    data["students"][idx]=s; save_data(data)
    return jsonify({"ok":True})

@app.route("/api/students/remarque",methods=["POST"])
def update_remarque():
    data = load_data()
    body = request.json
    idx = body["student_index"]
    s = data["students"][idx]
    if body["action"]=="add":
        s.setdefault("remarques",[]).append({"text":body["text"],"date":body["date"],"tag":body.get("tag","")})
    elif body["action"]=="delete":
        s["remarques"].pop(body["remarque_index"])
    data["students"][idx]=s; save_data(data)
    return jsonify({"ok":True})

@app.route("/api/students/progression",methods=["POST"])
def update_progression():
    data = load_data()
    body = request.json
    idx = body["student_index"]
    s = data["students"][idx]
    if body["action"]=="add":
        entry = {"date":body["date"],"elo_li":body.get("elo_li",""),
                 "elo_cc":body.get("elo_cc",""),"note":body.get("note","")}
        for cad in ["bullet","blitz","rapid","classical"]:
            if body.get(f"elo_li_{cad}"): entry[f"elo_li_{cad}"] = body[f"elo_li_{cad}"]
            if body.get(f"elo_cc_{cad}"): entry[f"elo_cc_{cad}"] = body[f"elo_cc_{cad}"]
        s.setdefault("elo_history",[]).append(entry)
    elif body["action"]=="delete":
        s["elo_history"].pop(body["prog_index"])
    data["students"][idx]=s; save_data(data)
    return jsonify({"ok":True})

@app.route("/api/students/workplan",methods=["POST"])
def update_workplan():
    data = load_data()
    body = request.json
    idx = body["student_index"]
    data["students"][idx]["work_plan"] = body["work_plan"]
    data["students"][idx]["elo_target"] = body.get("elo_target","")
    data["students"][idx]["elo_target_date"] = body.get("elo_target_date","")
    data["students"][idx]["recurring_errors"] = body.get("recurring_errors",[])
    save_data(data)
    return jsonify({"ok":True})

@app.route("/api/students/analysis",methods=["POST"])
def save_analysis():
    data = load_data()
    body = request.json
    idx = body["student_index"]
    s = data["students"][idx]
    if body["action"]=="add":
        s.setdefault("game_analyses",[]).append({
            "id":datetime.now().strftime("%Y%m%d%H%M%S"),
            "date":datetime.now().strftime("%d/%m/%Y"),
            "url":body.get("url",""),"opening":body.get("opening",""),
            "result":body.get("result",""),"notes":body.get("notes",""),
        })
    elif body["action"]=="delete":
        s["game_analyses"].pop(body["analysis_index"])
    data["students"][idx]=s; save_data(data)
    return jsonify({"ok":True,"analyses":s.get("game_analyses",[])})

@app.route("/api/students/journal",methods=["POST"])
def update_journal():
    data = load_data()
    body = request.json
    idx = body["student_index"]
    s = data["students"][idx]
    if body["action"]=="add":
        s.setdefault("journal",[]).append({
            "id":datetime.now().strftime("%Y%m%d%H%M%S%f"),
            "date":datetime.now().strftime("%d/%m/%Y"),
            "time":datetime.now().strftime("%H:%M"),
            "content":body["content"].strip(),"mood":body.get("mood","")})
    elif body["action"]=="edit":
        for e in s.get("journal",[]):
            if e["id"]==body["entry_id"]:
                e["content"]=body["content"].strip()
                e["edited"]=datetime.now().strftime("%H:%M"); break
    elif body["action"]=="delete":
        s["journal"]=[e for e in s.get("journal",[]) if e["id"]!=body["entry_id"]]
    data["students"][idx]=s; save_data(data)
    return jsonify({"ok":True})

@app.route("/api/sync/lichess",methods=["POST"])
def sync_lichess():
    data = load_data()
    body = request.json
    idx = body.get("student_index")
    username = body.get("username","").strip()
    if not username: return jsonify({"ok":False,"error":"no username"})
    try:
        d = fetch_lichess(username)
        s = data["students"][idx]
        if d["elo_bullet"]: s["elo_li_bullet"]=d["elo_bullet"]
        if d["elo_blitz"]:  s["elo_li_blitz"]=d["elo_blitz"]
        if d["elo_rapid"]:  s["elo_li_rapid"]=d["elo_rapid"]
        if d["elo_classical"]: s["elo_li_classical"]=d["elo_classical"]
        s["li_games"]=d["games_total"]; s["li_last_online"]=d["last_online"]
        today = datetime.now().strftime("%d/%m/%Y")
        hist = s.setdefault("elo_history",[])
        li_update = {"date":today,"elo_li":d["elo_blitz"],"elo_li_blitz":d["elo_blitz"],
                     "elo_li_bullet":d["elo_bullet"],"elo_li_rapid":d["elo_rapid"],
                     "elo_li_classical":d["elo_classical"],"note":"Sync auto"}
        existing = next((e for e in hist if e.get("date")==today), None)
        if existing is None: hist.append(li_update)
        else: existing.update(li_update)
        s["updated"]=datetime.now().strftime("%d/%m/%Y %H:%M")
        data["students"][idx]=s; save_data(data)
        return jsonify({"ok":True,"data":d})
    except HTTPError as e:
        return jsonify({"ok":False,"error":"Joueur introuvable" if e.code==404 else f"Erreur {e.code}"})
    except Exception:
        return jsonify({"ok":False,"error":"Pas de connexion"})

@app.route("/api/sync/chesscom",methods=["POST"])
def sync_chesscom():
    data = load_data()
    body = request.json
    idx = body.get("student_index")
    username = body.get("username","").strip()
    if not username: return jsonify({"ok":False,"error":"no username"})
    try:
        d = fetch_chesscom(username)
        s = data["students"][idx]
        if d["elo_bullet"]: s["elo_cc_bullet"]=d["elo_bullet"]
        if d["elo_blitz"]:  s["elo_cc_blitz"]=d["elo_blitz"]
        if d["elo_rapid"]:  s["elo_cc_rapid"]=d["elo_rapid"]
        s["cc_games"]=d["games_total"]; s["cc_joined"]=d["joined"]
        today = datetime.now().strftime("%d/%m/%Y")
        hist = s.setdefault("elo_history",[])
        cc_update = {"date":today,"elo_cc":d["elo_blitz"],"elo_cc_blitz":d["elo_blitz"],
                     "elo_cc_bullet":d["elo_bullet"],"elo_cc_rapid":d["elo_rapid"],"note":"Sync auto"}
        existing = next((e for e in hist if e.get("date")==today), None)
        if existing is None: hist.append(cc_update)
        else: existing.update(cc_update)
        s["updated"]=datetime.now().strftime("%d/%m/%Y %H:%M")
        data["students"][idx]=s; save_data(data)
        return jsonify({"ok":True,"data":d})
    except HTTPError as e:
        return jsonify({"ok":False,"error":"Joueur introuvable" if e.code==404 else f"Erreur {e.code}"})
    except Exception:
        return jsonify({"ok":False,"error":"Pas de connexion"})

@app.route("/api/sync/all",methods=["POST"])
def sync_all():
    data = load_data()
    results = {"ok":0,"err":0,"details":[]}
    today = datetime.now().strftime("%d/%m/%Y")
    for i,s in enumerate(data["students"]):
        name = s.get("name","?")
        hist = s.setdefault("elo_history",[])
        today_entry = next((e for e in hist if e.get("date")==today), None)
        if today_entry is None:
            today_entry = {"date":today,"note":"Sync auto"}
            hist.append(today_entry)
        if s.get("lichess","").strip():
            try:
                d = fetch_lichess(s["lichess"].strip())
                if d["elo_bullet"]: s["elo_li_bullet"]=d["elo_bullet"]
                if d["elo_blitz"]:  s["elo_li_blitz"]=d["elo_blitz"]
                if d["elo_rapid"]:  s["elo_li_rapid"]=d["elo_rapid"]
                if d["elo_classical"]: s["elo_li_classical"]=d["elo_classical"]
                s["li_games"]=d["games_total"]; s["li_last_online"]=d["last_online"]
                today_entry.update({"elo_li":d["elo_blitz"],"elo_li_blitz":d["elo_blitz"],
                    "elo_li_bullet":d["elo_bullet"],"elo_li_rapid":d["elo_rapid"],
                    "elo_li_classical":d["elo_classical"]})
                results["ok"]+=1; results["details"].append(f"✅ {name} (Li)")
            except Exception as e:
                results["err"]+=1; results["details"].append(f"❌ {name} Li: {e}")
        if s.get("chesscom","").strip():
            try:
                d = fetch_chesscom(s["chesscom"].strip())
                if d["elo_bullet"]: s["elo_cc_bullet"]=d["elo_bullet"]
                if d["elo_blitz"]:  s["elo_cc_blitz"]=d["elo_blitz"]
                if d["elo_rapid"]:  s["elo_cc_rapid"]=d["elo_rapid"]
                s["cc_games"]=d["games_total"]
                today_entry.update({"elo_cc":d["elo_blitz"],"elo_cc_blitz":d["elo_blitz"],
                    "elo_cc_bullet":d["elo_bullet"],"elo_cc_rapid":d["elo_rapid"]})
                results["ok"]+=1; results["details"].append(f"✅ {name} (CC)")
            except Exception as e:
                results["err"]+=1; results["details"].append(f"❌ {name} CC: {e}")
        s["updated"]=datetime.now().strftime("%d/%m/%Y %H:%M")
        data["students"][i]=s
    save_data(data)
    return jsonify({"ok":True,"results":results})

@app.route("/api/sync/games",methods=["POST"])
def sync_games():
    body = request.json
    username = body.get("username","").strip()
    if not username: return jsonify({"ok":False,"error":"no username"})
    try:
        result = fetch_lichess_games(username)
        data = load_data()
        idx = body.get("student_index")
        if idx is not None and idx < len(data["students"]):
            data["students"][idx]["games_analysis"] = result
            if result["top_openings"]:
                s = data["students"][idx]
                top = [o["name"] for o in result["top_openings"][:3]]
                if not s.get("opening_white"): s["opening_white"] = ", ".join(top[:2])
                if not s.get("opening_black"): s["opening_black"] = top[0]
            save_data(data)
        return jsonify({"ok":True,"result":result})
    except HTTPError as e:
        return jsonify({"ok":False,"error":"Joueur introuvable" if e.code==404 else f"Erreur {e.code}"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/sessions/save",methods=["POST"])
def save_session():
    data = load_data()
    body = request.json
    idx = body.get("_index")
    session = {k:v for k,v in body.items() if k!="_index"}
    if not session.get("created"): session["created"]=datetime.now().strftime("%d/%m/%Y %H:%M")
    if idx is not None and 0<=idx<len(data["sessions"]):
        data["sessions"][idx]=session
    else:
        data["sessions"].append(session)
    # V32 : une séance planifiée avec des Ghosts sélectionnés apparaît aussi dans leur profil.
    selected = session.get("students") or session.get("selected") or session.get("present") or []
    selected_names = set([str(x).strip() for x in selected if str(x).strip()])
    for si, stu in enumerate(data.get("students", [])):
        if (stu.get("name") or "").strip() in selected_names or si in selected:
            stu.setdefault("agenda", []).append({
                "date": session.get("date") or session.get("day") or now_fr(),
                "time": session.get("time") or session.get("hour") or "",
                "theme": session.get("theme") or session.get("title") or "Séance planifiée",
                "status": "planifiée",
                "source": "session"
            })
            add_student_feedback(data, si, "Séance planifiée", f"{session.get('theme') or session.get('title') or 'Séance'} — consulte ton calendrier.", "appointment", "session")
    save_data(data)
    return jsonify({"ok":True})

@app.route("/api/sessions/delete",methods=["POST"])
def delete_session():
    data = load_data()
    idx = request.json.get("index")
    if idx is not None and 0<=idx<len(data["sessions"]):
        data["sessions"].pop(idx); save_data(data)
    return jsonify({"ok":True})

@app.route("/api/session/end",methods=["POST"])
def end_session():
    data = load_data()
    body = request.json
    session = {"date":datetime.now().strftime("%d/%m/%Y %H:%M"),
               "theme":body.get("theme",""),"duration":body.get("duration",0),
               "present":body.get("present",[]),"notes":body.get("notes",""),
               "created":datetime.now().strftime("%d/%m/%Y %H:%M")}
    data.setdefault("sessions",[]).append(session)
    for name in body.get("present",[]):
        for i,s in enumerate(data["students"]):
            if s.get("name")==name:
                note = body.get("student_notes",{}).get(name,"")
                if note:
                    s.setdefault("remarques",[]).append({
                        "text":note,"date":datetime.now().strftime("%d/%m/%Y %H:%M"),"tag":"🎓 Session"})
                    data["students"][i]=s
    save_data(data)
    return jsonify({"ok":True})

@app.route("/api/pairs/save", methods=["POST"])
def save_pairs():
    data = load_data()
    data["pairs"] = request.json.get("pairs", [])
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/pairs/get", methods=["GET"])
def get_pairs():
    data = load_data()
    return jsonify({"pairs": data.get("pairs", [])})

@app.route("/api/exams/save", methods=["POST"])
def save_exam():
    data = load_data()
    body = request.json
    exams = data.setdefault("exam_bank", {})
    grade_key = body.get("grade_key", "")
    if not grade_key:
        return jsonify({"ok": False, "error": "no grade_key"})
    exams[grade_key] = {
        "grade_key": grade_key,
        "grade_label": body.get("grade_label", grade_key),
        "questions": body.get("questions", []),
        "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
    }
    data["exam_bank"] = exams
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/exams/get", methods=["GET"])
def get_exams():
    data = load_data()
    return jsonify({"exam_bank": data.get("exam_bank", {})})

@app.route("/api/students/exam_result", methods=["POST"])
def save_exam_result():
    data = load_data()
    body = request.json
    idx = body.get("student_index")
    if idx is None or idx >= len(data["students"]):
        return jsonify({"ok": False})
    s = data["students"][idx]
    result = {
        "id": datetime.now().strftime("%Y%m%d%H%M%S"),
        "date": datetime.now().strftime("%d/%m/%Y"),
        "grade_key": body.get("grade_key", ""),
        "grade_label": body.get("grade_label", ""),
        "score": body.get("score", 0),
        "total": body.get("total", 0),
        "passed": body.get("passed", False),
        "notes": body.get("notes", ""),
        "examiner": body.get("examiner", ""),
    }
    if result["passed"]:
        s["exam_grade_unlocked"] = s.get("exam_grade_unlocked", [])
        if result["grade_key"] not in s["exam_grade_unlocked"]:
            s["exam_grade_unlocked"].append(result["grade_key"])
    s.setdefault("exam_results", []).append(result)
    data["students"][idx] = s
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/students/finance", methods=["POST"])
def update_finance():
    data = load_data()
    body = request.json
    idx = body.get("student_index")
    if idx is None or idx >= len(data["students"]):
        return jsonify({"ok": False})
    s = data["students"][idx]
    action = body.get("action")
    if action == "add_payment":
        entry = {
            "id": datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:4],
            "date": body.get("date", datetime.now().strftime("%d/%m/%Y")),
            "amount": float(body.get("amount", 0)),
            "label": body.get("label", ""),
            "type": body.get("type", "session"),
            "status": body.get("status", "paid"),
        }
        s.setdefault("payments", []).append(entry)
        # V30 : un versement saisi dans la fiche individuelle est aussi visible côté Ghost
        # et comptabilisé dans les finances globales.
        user = next((u for u in data.get("users", []) if u.get("student_index") == idx), None)
        if user:
            rec = {"id": entry["id"], "date": entry["date"], "user_id": user.get("id"), "student_index": idx, "student_name": s.get("name"), "plan_key": entry.get("type"), "plan": entry.get("label") or "Versement", "amount": safe_int(entry.get("amount"), 0), "amount_label": format_fcfa(entry.get("amount")), "status": entry.get("status", "paid"), "source": "student_finance"}
            user.setdefault("payment_history", []).insert(0, rec)
            user["payment_history"] = user.get("payment_history", [])[:80]
            data.setdefault("payments_log", []).insert(0, rec)
            data["payments_log"] = data.get("payments_log", [])[:400]
    elif action == "delete_payment":
        pid = body.get("payment_id")
        s["payments"] = [p for p in s.get("payments", []) if p.get("id") != pid]
        for u in data.get("users", []):
            if u.get("student_index") == idx:
                u["payment_history"] = [p for p in u.get("payment_history", []) if p.get("id") != pid]
        data["payments_log"] = [p for p in data.get("payments_log", []) if p.get("id") != pid]
    elif action == "update_status":
        pid = body.get("payment_id"); status = body.get("status")
        for pmt in s.get("payments", []):
            if pmt.get("id") == pid: pmt["status"] = status; break
        for u in data.get("users", []):
            if u.get("student_index") == idx:
                for pmt in u.get("payment_history", []):
                    if pmt.get("id") == pid: pmt["status"] = status
        for pmt in data.get("payments_log", []):
            if pmt.get("id") == pid: pmt["status"] = status
    elif action == "set_plan":
        s["billing_plan"] = body.get("billing_plan", "")
        s["billing_note"] = body.get("billing_note", "")
    data["students"][idx] = s
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/pricegrid/save", methods=["POST"])
def save_pricegrid():
    data = load_data()
    data["price_grid"] = request.json.get("price_grid", {})
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/pricegrid/get", methods=["GET"])
def get_pricegrid():
    data = load_data()
    default_grid = default_price_grid()
    return jsonify({"price_grid": data.get("price_grid", default_grid)})

@app.route("/api/units/save", methods=["POST"])
def save_units():
    data = load_data()
    data["units"] = request.json.get("units", [])
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/units/get", methods=["GET"])
def get_units():
    data = load_data()
    return jsonify({"units": data.get("units", [])})

@app.route("/api/students/agenda", methods=["POST"])
def update_agenda():
    data = load_data()
    body = request.json
    idx = body.get("student_index")
    if idx is None or idx >= len(data["students"]):
        return jsonify({"ok": False})
    s = data["students"][idx]
    action = body.get("action")
    if action == "add":
        event = {
            "id": datetime.now().strftime("%Y%m%d%H%M%S"),
            "date": body.get("date", ""), "time": body.get("time", ""),
            "duration": body.get("duration", 60), "label": body.get("label", ""),
            "type": body.get("type", "session"), "done": False,
        }
        s.setdefault("agenda", []).append(event)
    elif action == "delete":
        eid = body.get("event_id")
        s["agenda"] = [e for e in s.get("agenda", []) if e.get("id") != eid]
    elif action == "toggle_done":
        eid = body.get("event_id")
        for e in s.get("agenda", []):
            if e.get("id") == eid: e["done"] = not e.get("done", False); break
    data["students"][idx] = s
    save_data(data)
    return jsonify({"ok": True})

# ═══════════════════════════════════════════════════════════
# ─── Plan de Travail ────────────────────────────────────────
# ═══════════════════════════════════════════════════════════

@app.route("/workplan")
def workplan_page():
    data = load_data()
    students = enrich_students(data["students"])
    return render_template("workplan.html", students=students)

@app.route("/api/workplan/save", methods=["POST"])
def save_workplan():
    data = load_data()
    body = request.json
    idx = body.get("studentIdx")
    if idx is None or idx >= len(data["students"]):
        return jsonify({"ok": False, "error": "invalid index"})
    plan = {k: v for k, v in body.items()}
    plan["date"] = datetime.now().strftime("%d/%m/%Y %H:%M")
    s = data["students"][idx]
    s.setdefault("workplans", []).append(plan)
    data["students"][idx] = s
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/workplan/history", methods=["GET"])
def get_workplan_history():
    data = load_data()
    idx = request.args.get("student_index", type=int)
    if idx is None or idx >= len(data["students"]):
        return jsonify({"plans": []})
    plans = data["students"][idx].get("workplans", [])
    return jsonify({"plans": plans})

@app.route("/api/workplan/delete", methods=["POST"])
def delete_workplan():
    data = load_data()
    body = request.json
    idx = body.get("student_index")
    plan_idx = body.get("plan_index")
    if idx is None or idx >= len(data["students"]):
        return jsonify({"ok": False})
    plans = data["students"][idx].get("workplans", [])
    if plan_idx is not None and 0 <= plan_idx < len(plans):
        plans.pop(plan_idx)
        data["students"][idx]["workplans"] = plans
        save_data(data)
    return jsonify({"ok": True})

@app.route("/api/workplan/pdf", methods=["POST"])
def generate_workplan_pdf():
    from io import BytesIO
    from flask import send_file
    body = request.json
    plan = body.get("plan", {})

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            rightMargin=2*cm, leftMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    story = []

    def add_title(text, color_hex="#cc1a1a"):
        c = colors.HexColor(color_hex)
        style = ParagraphStyle("wph1", parent=styles["Heading1"],
                               fontSize=18, textColor=c, spaceAfter=6)
        story.append(Paragraph(text, style))

    def add_section(text):
        style = ParagraphStyle("wpsec", parent=styles["Heading2"],
                               fontSize=10, textColor=colors.HexColor("#888888"),
                               spaceAfter=4, spaceBefore=10, fontName="Helvetica-Bold")
        story.append(Paragraph(text.upper(), style))

    def add_body(text):
        style = ParagraphStyle("wpbody", parent=styles["Normal"],
                               fontSize=10, textColor=colors.HexColor("#333333"), spaceAfter=3)
        story.append(Paragraph(str(text) if text else "—", style))

    def add_hr():
        story.append(HRFlowable(width="100%", thickness=0.5,
                                color=colors.HexColor("#dddddd"), spaceAfter=6))

    add_title(f"Plan de Travail — {plan.get('studentName','?')}")
    add_body(f"Rang : {plan.get('studentRank','')}  |  Elo moyen : {plan.get('studentElo','—')}  |  Date : {plan.get('date','')}")
    story.append(Spacer(1, 0.3*cm))
    add_hr()

    if plan.get('eloTarget'):
        add_section("Objectif Elo")
        add_body(f"Cible : {plan['eloTarget']} pts" + (f"  |  Deadline : {plan['eloDeadline']}" if plan.get('eloDeadline') else ''))
        add_hr()

    if plan.get('themes'):
        add_section("Themes prioritaires")
        add_body(" · ".join(plan['themes']))
        add_hr()

    if plan.get('objectives'):
        add_section("Objectifs")
        for o in plan['objectives']:
            status = "[OK]" if o.get('done') else "[ ]"
            prio_map = {'high':'Haute','med':'Moy.','low':'Basse'}
            prio = prio_map.get(o.get('prio','med'),'Moy.')
            add_body(f"{status} [{prio}] {o.get('text','')}")
        add_hr()

    if plan.get('pointsForts') or plan.get('pointsFaibles'):
        add_section("Points forts / Points faibles")
        if plan.get('pointsForts'):
            add_body(f"Points forts : {plan['pointsForts']}")
        if plan.get('pointsFaibles'):
            add_body(f"Points faibles : {plan['pointsFaibles']}")
        add_hr()

    if plan.get('errors') or plan.get('errorsCustom'):
        add_section("Erreurs recurrentes")
        all_errors = list(plan.get('errors', []))
        if plan.get('errorsCustom'):
            all_errors.append(plan['errorsCustom'])
        add_body(" · ".join(all_errors))
        add_hr()

    if plan.get('resources'):
        add_section("Devoirs & Ressources")
        for r in plan['resources']:
            add_body(f"{r.get('type','')} {r.get('text','')}")
        add_hr()

    star_fields = plan.get('stars', {})
    if any(v for v in star_fields.values() if v):
        add_section("Evaluation")
        for k, label in [('motivation','Motivation'),('regularite','Regularite'),('progression','Progression')]:
            val = int(star_fields.get(k) or 0)
            add_body(f"{label} : {'*'*val}{'-'*(5-val)} ({val}/5)")
        if plan.get('comportement'):
            add_body(f"Comportement : {plan['comportement']}")
        if plan.get('evalNotes'):
            add_body(plan['evalNotes'])
        add_hr()

    if plan.get('nextDate') or plan.get('nextFocus'):
        add_section("Prochaine seance")
        if plan.get('nextDate'):
            add_body(f"Date : {plan['nextDate']}")
        if plan.get('nextFocus'):
            add_body(f"Focus : {plan['nextFocus']}")
        add_hr()

    if plan.get('freeNotes'):
        add_section("Notes libres")
        add_body(plan['freeNotes'])

    doc.build(story)
    buf.seek(0)
    name = (plan.get('studentName') or 'ghost').replace(' ', '_')
    return send_file(buf, mimetype='application/pdf',
                     as_attachment=True,
                     download_name=f"plan_{name}.pdf")

# ═══════════════════════════════════════════════════════════
# ─── Tournoi des Châteaux ───────────────────────────────────
# ═══════════════════════════════════════════════════════════

@app.route("/chateau")
def chateau_page():
    data = load_data()
    students = enrich_students(data["students"])
    return render_template("chateau.html", students=students)

# ═══════════════════════════════════════════════════════════
# ─── Roulette Russe ─────────────────────────────────────────
# ═══════════════════════════════════════════════════════════

@app.route("/roulette")
def roulette_page():
    data = load_data()
    students = enrich_students(data["students"])
    return render_template("roulette.html", students=students)


# ═══════════════════════════════════════════════════════════
# ─── Portail élèves / utilisateurs ────────────────────────
# ═══════════════════════════════════════════════════════════

@app.route("/client")
def client_portal():
    data = load_data()
    user = get_current_user(data)
    if not user:
        return render_template("client_auth.html")
    student = None
    idx = user.get("student_index")
    if isinstance(idx, int) and 0 <= idx < len(data.get("students", [])):
        student = data["students"][idx]
    plans = data.get("client_price_plans") or default_client_price_plans()
    selected_plan = find_client_plan(data, user.get("plan"))
    return render_template(
        "client_dashboard.html",
        user=user,
        student=public_student_payload(student),
        payment=get_user_payment_state(user),
        price_plans=plans,
        selected_plan=selected_plan,
        plan_theme=plan_theme(user.get("plan")),
        plan_state=get_user_plan_state(user, selected_plan),
        grandline_students=grandline_payload(data, idx),
        client_pairs=student_pairs_payload(data, idx),
        client_calendar=student_calendar_payload(student),
        client_tournaments=visible_tournaments_for_student(data, idx),
        student_messages=visible_messages_for_student(data, idx),
        student_contacts=student_contacts_payload(data, idx),
        theme=plan_theme(selected_plan.get("key")),
    )

@app.route("/favicon.ico")
def favicon():
    return send_from_directory(app.static_folder, "favicon.svg", mimetype="image/svg+xml")

@app.route("/client/logout")
def client_logout():
    session.pop("client_user_id", None)
    return redirect("/client")

@app.route("/admin/clients")
def admin_clients():
    data = load_data()
    reconcile_client_plan_state(data)
    save_data(data)
    students = enrich_students(data.get("students", []))
    users = data.get("users", [])
    payment_users = []
    for u in users:
        u["_plan_state"] = get_user_plan_state(u, find_client_plan(data, u.get("plan")))
        ap = u.get("active_plan") or {}
        # V26 : le panneau Paiements / accès ne doit pas afficher les Ghosts qui ont seulement
        # l'accès app de base. Il liste uniquement les demandes, forfaits actifs/terminés,
        # paiements en attente ou relances.
        has_real_plan = bool(u.get("pending_plan_request")) or (ap.get("plan_key") not in (None, "", "no_plan") and ap.get("status") not in (None, "", "inactive"))
        status = (u.get("payment_status") or "").strip()
        amount_due_value = parse_fcfa(u.get("amount_due"))
        needs_payment_attention = status in ("pending", "overdue", "awaiting_validation") or bool(u.get("pending_plan_request")) or (amount_due_value > 0 and (u.get("plan") or "no_plan") != "no_plan")
        if has_real_plan or needs_payment_attention:
            payment_users.append(u)
    payment_logs = []
    for rec in data.get("payments_log", [])[:80]:
        row = dict(rec)
        if not row.get("student_name"):
            row["student_name"] = row.get("request_name") or student_name_from_index(data, row.get("student_index"))
        payment_logs.append(row)
    return render_template(
        "admin_clients.html",
        students=students,
        users=payment_users,
        all_users=users,
        codes=data.get("registration_codes", []),
        registration_requests=data.get("registration_requests", [])[:50],
        registration_fee=format_fcfa(default_price_grid().get("registration_fee", 5000)),
        notifications=data.get("client_notifications", [])[:60],
        price_plans=data.get("client_price_plans") or default_client_price_plans(),
        finance=finance_summary(data),
        payment_logs=payment_logs,
        tournaments=enriched_tournaments(data, 30),
        exercises=data.get("exercises", [])[:30],
        messages=data.get("student_messages", [])[:30],
        visit_stats=data.get("visit_stats", {}),
    )

@app.route("/api/client/registration/request", methods=["POST"])
def api_client_registration_request():
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "").strip()
    email = (body.get("email") or "").strip().lower()
    phone = (body.get("phone") or "").strip()
    reference = (body.get("reference") or "").strip()
    note = (body.get("note") or "").strip()
    if not name or not email:
        return jsonify({"ok": False, "error": "Nom et email obligatoires."}), 400
    data = load_data()
    if any((u.get("email") or "").lower() == email for u in data.get("users", [])):
        return jsonify({"ok": False, "error": "Un compte existe déjà avec cet email. Connecte-toi plutôt."}), 400
    # Une demande déjà en attente est mise à jour au lieu de dupliquer.
    req = next((r for r in data.get("registration_requests", []) if (r.get("email") or "").lower() == email and r.get("status") in ("pending_validation", "pending")), None)
    if not req:
        req = {"id": str(uuid.uuid4()), "created_at": now_fr()}
        data.setdefault("registration_requests", []).insert(0, req)
    req.update({
        "name": name, "email": email, "phone": phone, "reference": reference, "note": note,
        "amount": default_price_grid().get("registration_fee", 5000),
        "amount_label": default_price_grid().get("registration_fee_label", "5 000 FCFA"),
        "status": "pending_validation", "updated_at": now_fr(),
    })
    add_client_notification(data, "Paiement inscription à valider", f"{name} signale le paiement de l'inscription Ghost ({req['amount_label']}).", "registration", None, None, target_url="/admin/clients#registration-requests", item_id=req["id"])
    save_data(data)
    return jsonify({"ok": True, "message": "Demande envoyée. Le coach confirmera le paiement et te transmettra ton code d’inscription."})

@app.route("/api/admin/registration/approve", methods=["POST"])
def api_admin_registration_approve():
    body = request.get_json(force=True, silent=True) or {}
    rid = body.get("request_id")
    data = load_data()
    req = next((r for r in data.get("registration_requests", []) if r.get("id") == rid), None)
    if not req:
        return jsonify({"ok": False, "error": "Demande introuvable."}), 404
    student_index = body.get("student_index")
    if student_index == "" or student_index is None:
        student_index = None
    else:
        try:
            student_index = int(student_index)
        except Exception:
            student_index = None
    access_kind = (body.get("access_kind") or "paid_registration").strip()
    free_access = access_kind == "free_registration"
    ghost_name = student_name_from_index(data, student_index, req.get("name") or "Ghost non lié")
    raw = uuid.uuid4().hex[:8].upper()
    code_value = "GHOST-" + raw[:4] + "-" + raw[4:]
    entry = {
        "id": str(uuid.uuid4()), "code": code_value, "kind": "app_access", "student_index": student_index,
        "email_hint": (req.get("email") or "").lower(), "plan": "no_plan", "amount_due": "0 FCFA",
        "created_at": now_fr(), "used": False, "source_request_id": rid,
        "student_name": ghost_name,
        "request_name": req.get("name"),
        "free_access": free_access,
        "access_label": "Inscription offerte" if free_access else "Inscription Ghost - 5 000 FCFA",
    }
    data.setdefault("registration_codes", []).insert(0, entry)
    req.update({"status": "approved", "approved_at": now_fr(), "code": code_value, "student_index": student_index, "student_name": ghost_name, "access_kind": access_kind})
    if not free_access:
        payment_record = {"id": str(uuid.uuid4()), "date": now_fr(), "user_id": None, "student_index": student_index, "student_name": ghost_name, "request_name": req.get("name"), "plan_key": "registration", "plan": "Inscription Ghost", "amount": safe_int(req.get("amount"), 5000), "amount_label": req.get("amount_label") or "5 000 FCFA", "status": "paid", "source_request_id": rid}
        data.setdefault("payments_log", []).insert(0, payment_record)
        data["payments_log"] = data.get("payments_log", [])[:300]
    add_client_notification(data, "Inscription validée", f"Code généré pour {req.get('name')} : {code_value}", "registration", None, None, target_url="/admin/clients#registration-requests", item_id=rid)
    save_data(data)
    return jsonify({"ok": True, "code": entry, "request": req})

@app.route("/api/admin/registration/reject", methods=["POST"])
def api_admin_registration_reject():
    body = request.get_json(force=True, silent=True) or {}
    rid = body.get("request_id")
    data = load_data()
    req = next((r for r in data.get("registration_requests", []) if r.get("id") == rid), None)
    if not req:
        return jsonify({"ok": False, "error": "Demande introuvable."}), 404
    req.update({"status": "rejected", "rejected_at": now_fr(), "reject_note": (body.get("note") or "").strip()})
    save_data(data)
    return jsonify({"ok": True, "request": req})

@app.route("/api/client/register", methods=["POST"])
def api_client_register():
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "").strip()
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    code_value = (body.get("code") or "").strip().upper()
    if not name or not email or len(password) < 4 or not code_value:
        return jsonify({"ok": False, "error": "Champs incomplets ou mot de passe trop court."}), 400
    data = load_data()
    if any((u.get("email") or "").lower() == email for u in data.get("users", [])):
        return jsonify({"ok": False, "error": "Un compte existe déjà avec cet email."}), 400
    code = next((c for c in data.get("registration_codes", []) if c.get("code") == code_value), None)
    if not code or code.get("used"):
        return jsonify({"ok": False, "error": "Code invalide ou déjà utilisé."}), 400

    student_index = code.get("student_index")
    if not isinstance(student_index, int) or student_index < 0 or student_index >= len(data.get("students", [])):
        data.setdefault("students", []).append({
            "name": name,
            "email": email,
            "created_from_client": True,
            "client_games": [],
            "client_notes": [],
            "client_appointments": [],
        })
        student_index = len(data["students"]) - 1
    else:
        data["students"][student_index]["email"] = data["students"][student_index].get("email") or email
        data["students"][student_index]["name"] = data["students"][student_index].get("name") or name

    user = {
        "id": str(uuid.uuid4()),
        "name": name,
        "email": email,
        "password_hash": generate_password_hash(password),
        "student_index": student_index,
        "created_at": now_iso(),
        "active": True,
        "role": "student",
        "plan": code.get("plan", "session_60"),
        "payment_status": "free" if code.get("kind") == "app_access" or code.get("plan") == "no_plan" else "pending",
        "amount_due": "0 FCFA" if code.get("kind") == "app_access" or code.get("plan") == "no_plan" else (code.get("amount_due") or default_amount_for_plan(code.get("plan", "session_60"))),
        "app_access_status": "offered" if code.get("free_access") else ("paid" if code.get("kind") == "app_access" or code.get("plan") == "no_plan" else "pending"),
        "registration_kind": "offered" if code.get("free_access") else "paid",
        "registration_validated_at": code.get("created_at") if code.get("kind") == "app_access" or code.get("plan") == "no_plan" else "",
        "payment_reminders": 0,
        "access_restricted": False,
        "active_plan": {
            "plan_key": code.get("plan", "session_60"),
            "used_sessions": 0,
            "total_sessions": 0 if code.get("kind") == "app_access" or code.get("plan") == "no_plan" else plan_session_total(find_client_plan(data, code.get("plan", "session_60"))),
            "status": "inactive" if code.get("kind") == "app_access" or code.get("plan") == "no_plan" else "pending",
            "started_at": "",
        },
        "plan_history": [],
    }
    data.setdefault("users", []).append(user)
    code["used"] = True
    code["used_by"] = user["id"]
    code["used_at"] = now_iso()
    add_client_notification(data, "Nouveau compte élève", f"{name} a créé son compte avec le code {code_value}.", "account", user["id"], student_index)
    save_data(data)
    session["client_user_id"] = user["id"]
    return jsonify({"ok": True})

@app.route("/api/client/login", methods=["POST"])
def api_client_login():
    body = request.get_json(force=True, silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    data = load_data()
    user = next((u for u in data.get("users", []) if (u.get("email") or "").lower() == email), None)
    if not user or not check_password_hash(user.get("password_hash", ""), password):
        return jsonify({"ok": False, "error": "Email ou mot de passe incorrect."}), 401
    if not user.get("active", True):
        return jsonify({"ok": False, "error": "Compte désactivé."}), 403
    session["client_user_id"] = user["id"]
    return jsonify({"ok": True})

@app.route("/api/client/game", methods=["POST"])
def api_client_game():
    data, user, resp, code = require_client_json()
    if resp: return resp, code
    if client_is_restricted(user): return restricted_response(user)
    body = request.get_json(force=True, silent=True) or {}
    url = (body.get("url") or "").strip()
    note = (body.get("note") or "").strip()
    image_url = (body.get("image_url") or "").strip()
    position_fen = (body.get("position_fen") or "").strip()
    pgn = (body.get("pgn") or "").strip()
    attachments = body.get("attachments") or ([] if not image_url else [image_url])
    if isinstance(attachments, str):
        attachments = [attachments]
    if not url and not note and not attachments and not position_fen and not pgn:
        return jsonify({"ok": False, "error": "Ajoute au moins une URL, une note, une position, un PGN ou un fichier."}), 400
    idx = user.get("student_index")
    if not isinstance(idx, int) or idx >= len(data.get("students", [])):
        return jsonify({"ok": False, "error": "Profil élève introuvable."}), 400
    entry = {"id": str(uuid.uuid4()), "date": now_fr(), "url": url, "note": note, "position_fen": position_fen, "pgn": pgn, "image_url": attachments[0] if attachments else "", "attachments": attachments, "status": "nouveau"}
    data["students"][idx].setdefault("client_games", []).insert(0, entry)
    add_client_notification(data, "Nouvelle partie", f"{user.get('name')} a ajouté une partie ou une analyse.", "game", user.get("id"), idx, item_id=entry["id"])
    save_data(data)
    return jsonify({"ok": True, "entry": entry})

@app.route("/api/client/note", methods=["POST"])
def api_client_note():
    data, user, resp, code = require_client_json()
    if resp: return resp, code
    if client_is_restricted(user): return restricted_response(user)
    body = request.get_json(force=True, silent=True) or {}
    text = (body.get("text") or "").strip()
    category = (body.get("category") or "objectif").strip()
    if not text:
        return jsonify({"ok": False, "error": "Note vide."}), 400
    idx = user.get("student_index")
    entry = {"id": str(uuid.uuid4()), "date": now_fr(), "category": category, "text": text, "status": "nouveau"}
    data["students"][idx].setdefault("client_notes", []).insert(0, entry)
    add_client_notification(data, "Note élève", f"{user.get('name')} a ajouté : {text[:70]}", "note", user.get("id"), idx)
    save_data(data)
    return jsonify({"ok": True, "entry": entry})

@app.route("/api/client/appointment", methods=["POST"])
def api_client_appointment():
    data, user, resp, code = require_client_json()
    if resp: return resp, code
    if client_is_restricted(user): return restricted_response(user)
    body = request.get_json(force=True, silent=True) or {}
    day = (body.get("day") or "").strip()
    time = (body.get("time") or "").strip()
    reason = (body.get("reason") or "").strip()
    if not day or not time:
        return jsonify({"ok": False, "error": "Choisis une date et une heure."}), 400
    idx = user.get("student_index")
    entry = {"id": str(uuid.uuid4()), "created_at": now_fr(), "day": day, "time": time, "reason": reason, "status": "demandé"}
    data["students"][idx].setdefault("client_appointments", []).insert(0, entry)
    add_client_notification(data, "RDV demandé", f"{user.get('name')} demande une séance le {day} à {time}.", "appointment", user.get("id"), idx, item_id=entry["id"])
    save_data(data)
    return jsonify({"ok": True, "entry": entry})

@app.route("/api/client/upload", methods=["POST"])
def api_client_upload():
    data, user, resp, code = require_client_json()
    if resp: return resp, code
    if client_is_restricted(user): return restricted_response(user)
    urls = upload_many_from_request(user.get("id", "student"))
    if not urls:
        return jsonify({"ok": False, "error": "Aucun fichier valide. Formats acceptés : image, PDF, PGN, TXT."}), 400
    return jsonify({"ok": True, "url": urls[0], "urls": urls})

@app.route("/api/client/plan/select", methods=["POST"])
def api_client_plan_select():
    data, user, resp, code = require_client_json()
    if resp: return resp, code
    body = request.get_json(force=True, silent=True) or {}
    plan_key = (body.get("plan") or "").strip()
    plan = find_client_plan(data, plan_key)
    if not plan.get("key"):
        return jsonify({"ok": False, "error": "Formule introuvable."}), 404

    old_plan = user.get("plan")
    same_plan = old_plan == plan.get("key")
    active = user.get("active_plan") or {}
    active_status = normalize_plan_status(active.get("status"))
    if active_status == "active" and not active_plan_is_completed(user):
        return jsonify({"ok": False, "error": "Tu as déjà une formule active. Termine-la d’abord, ou demande au coach d’annuler/adapter le forfait en cours."}), 400
    if user.get("pending_plan_request"):
        return jsonify({"ok": False, "error": "Une demande de formule est déjà en attente. Attends la validation du coach ou demande-lui d’annuler la tentative."}), 400
    user["plan"] = plan.get("key")
    user["amount_due"] = plan.get("price", "")
    user["payment_status"] = "pending"
    user["payment_reminders"] = 0
    user["access_restricted"] = False
    user["plan_requested_at"] = now_iso()
    user["pending_plan_request"] = {
        "id": str(uuid.uuid4()),
        "plan_key": plan.get("key"),
        "plan_name": plan.get("name"),
        "price": plan.get("price"),
        "sessions_total": plan_session_total(plan),
        "requested_at": now_fr(),
        "renewal": same_plan,
        "status": "pending_payment",
    }
    # On prépare le nouveau forfait, mais il ne devient actif qu'après validation du coach.
    user["active_plan"] = {
        "plan_key": plan.get("key"),
        "plan_name": plan.get("name"),
        "used_sessions": 0,
        "total_sessions": canonical_plan_total(plan),
        "status": "pending",
        "started_at": "",
        "last_session_at": "",
        "sessions_log": [],
    }
    idx = user.get("student_index")
    notif_title = "Renouvellement de formule" if same_plan else "Formule choisie"
    add_client_notification(data, notif_title, f"{user.get('name')} a demandé : {plan.get('name')} — {plan.get('price')}. Paiement à valider.", "payment", user.get("id"), idx)
    if isinstance(idx, int):
        msg = "renouvelé" if same_plan else "choisi"
        add_student_feedback(data, idx, "Formule enregistrée", f"Tu as {msg} : {plan.get('name')} ({plan.get('price')}). Le coach validera l’accès dès confirmation du paiement.", "payment", "plan")
    save_data(data)
    return jsonify({"ok": True, "plan": plan, "old_plan": old_plan, "renewal": same_plan})

@app.route("/api/admin/codes/generate", methods=["POST"])
def api_admin_generate_code():
    body = request.get_json(force=True, silent=True) or {}
    data = load_data()
    raw = uuid.uuid4().hex[:8].upper()
    code = "GHOST-" + raw[:4] + "-" + raw[4:]
    student_index = body.get("student_index")
    if student_index == "" or student_index is None:
        student_index = None
    else:
        try: student_index = int(student_index)
        except Exception: student_index = None
    access_kind = (body.get("access_kind") or "paid_registration").strip()
    free_access = access_kind == "free_registration"
    ghost_name = student_name_from_index(data, student_index, "Profil à créer")
    # V30 : les codes manuels servent à ouvrir l'accès Ghost. On distingue inscription payée et offerte.
    entry = {
        "id": str(uuid.uuid4()),
        "code": code,
        "kind": "app_access",
        "student_index": student_index,
        "email_hint": (body.get("email") or "").strip().lower(),
        "plan": "no_plan",
        "amount_due": "0 FCFA",
        "created_at": now_fr(),
        "used": False,
        "free_access": free_access,
        "access_label": "Inscription offerte" if free_access else "Inscription Ghost - 5 000 FCFA",
        "student_name": ghost_name,
    }
    data.setdefault("registration_codes", []).insert(0, entry)
    if not free_access:
        # Comptabilise uniquement les inscriptions réellement payées.
        payment_record = {
            "id": str(uuid.uuid4()), "date": now_fr(), "user_id": None,
            "student_index": student_index, "student_name": ghost_name,
            "plan_key": "registration", "plan": "Inscription Ghost",
            "amount": 5000, "amount_label": "5 000 FCFA", "status": "paid",
            "source": "manual_code",
        }
        data.setdefault("payments_log", []).insert(0, payment_record)
        data["payments_log"] = data.get("payments_log", [])[:300]
    save_data(data)
    return jsonify({"ok": True, "code": entry})

@app.route("/api/admin/notifications/read", methods=["POST"])
def api_admin_notifications_read():
    data = load_data()
    for n in data.get("client_notifications", []):
        n["read"] = True
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/admin/notifications/delete", methods=["POST"])
def api_admin_notifications_delete():
    body = request.get_json(force=True, silent=True) or {}
    data = load_data()
    notification_id = body.get("notification_id")
    if notification_id:
        before = len(data.get("client_notifications", []))
        data["client_notifications"] = [n for n in data.get("client_notifications", []) if n.get("id") != notification_id]
        removed = before != len(data.get("client_notifications", []))
    else:
        data["client_notifications"] = []
        removed = True
    save_data(data)
    return jsonify({"ok": True, "removed": removed})

@app.route("/api/admin/notifications")
def api_admin_notifications_list():
    data = load_data()
    rows = unread_notifications(data, 20)
    return jsonify({"ok": True, "count": len(rows), "notifications": rows})

@app.route("/api/admin/appointment/action", methods=["POST"])
def api_admin_appointment_action():
    body = request.get_json(force=True, silent=True) or {}
    data = load_data()
    try:
        idx = int(body.get("student_index"))
    except Exception:
        return jsonify({"ok": False, "error": "student_index invalide"}), 400
    appointment_id = body.get("appointment_id")
    action = body.get("action")
    message = (body.get("message") or "").strip()
    proposed_day = (body.get("proposed_day") or "").strip()
    proposed_time = (body.get("proposed_time") or "").strip()
    if idx < 0 or idx >= len(data.get("students", [])):
        return jsonify({"ok": False, "error": "Élève introuvable"}), 404
    apps = data["students"][idx].setdefault("client_appointments", [])
    appt = next((a for a in apps if a.get("id") == appointment_id), None)
    if not appt:
        return jsonify({"ok": False, "error": "RDV introuvable"}), 404
    if action == "accept":
        appt["status"] = "accepté"
        appt["coach_reply"] = message or "RDV validé. Prépare une partie ou une position à analyser."
        title = "RDV accepté"
        text = f"Ta séance du {appt.get('day')} à {appt.get('time')} est validée. {appt['coach_reply']}"
    elif action == "reject":
        appt["status"] = "refusé"
        appt["coach_reply"] = message or "Ce créneau n'est pas possible. Propose un autre horaire depuis ton espace."
        title = "RDV refusé"
        text = appt["coach_reply"]
    elif action == "propose":
        if not proposed_day or not proposed_time:
            return jsonify({"ok": False, "error": "Indique une nouvelle date et une heure."}), 400
        appt["status"] = "proposition"
        appt["proposed_day"] = proposed_day
        appt["proposed_time"] = proposed_time
        appt["coach_reply"] = message or "Je te propose ce nouveau créneau."
        title = "Nouveau créneau proposé"
        text = f"Créneau proposé : {proposed_day} à {proposed_time}. {appt['coach_reply']}"
    else:
        return jsonify({"ok": False, "error": "Action inconnue"}), 400
    appt["updated_at"] = now_fr()
    feedback = add_student_feedback(data, idx, title, text, "appointment", "appointment", appointment_id)
    save_data(data)
    return jsonify({"ok": True, "appointment": appt, "feedback": feedback})

@app.route("/api/client/appointment/respond", methods=["POST"])
def api_client_appointment_respond():
    data, user, resp, code = require_client_json()
    if resp: return resp, code
    body = request.get_json(force=True, silent=True) or {}
    idx = user.get("student_index")
    appointment_id = body.get("appointment_id")
    response = body.get("response")
    apps = data.get("students", [])[idx].setdefault("client_appointments", [])
    appt = next((a for a in apps if a.get("id") == appointment_id), None)
    if not appt:
        return jsonify({"ok": False, "error": "RDV introuvable"}), 404
    if response == "accept_proposal" and appt.get("status") == "proposition":
        appt["day"] = appt.get("proposed_day") or appt.get("day")
        appt["time"] = appt.get("proposed_time") or appt.get("time")
        appt["status"] = "accepté"
        appt["student_response"] = "proposition acceptée"
        add_client_notification(data, "Créneau accepté", f"{user.get('name')} accepte le RDV du {appt.get('day')} à {appt.get('time')}.", "appointment", user.get("id"), idx)
    elif response == "decline_proposal":
        appt["status"] = "à revoir"
        appt["student_response"] = "proposition refusée"
        add_client_notification(data, "Créneau refusé", f"{user.get('name')} refuse la proposition et demande un autre créneau.", "appointment", user.get("id"), idx)
    else:
        return jsonify({"ok": False, "error": "Réponse invalide"}), 400
    appt["updated_at"] = now_fr()
    save_data(data)
    return jsonify({"ok": True, "appointment": appt})

@app.route("/api/admin/feedback/send", methods=["POST"])
def api_admin_feedback_send():
    body = request.get_json(force=True, silent=True) or {}
    data = load_data()
    try:
        idx = int(body.get("student_index"))
    except Exception:
        return jsonify({"ok": False, "error": "Choisis un élève."}), 400
    title = (body.get("title") or "Feedback coach").strip()
    text = (body.get("text") or "").strip()
    kind = (body.get("kind") or "feedback").strip()
    image_url = (body.get("image_url") or "").strip()
    attachments = body.get("attachments") or ([] if not image_url else [image_url])
    if isinstance(attachments, str): attachments = [attachments]
    position_fen = (body.get("position_fen") or "").strip()
    pgn = (body.get("pgn") or "").strip()
    tags = body.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    priority = (body.get("priority") or "normal").strip()
    if not text and not image_url and not position_fen and not pgn:
        return jsonify({"ok": False, "error": "Feedback vide."}), 400
    entry = add_student_feedback(data, idx, title, text, kind, "game" if body.get("linked_id") else "manual", body.get("linked_id"), image_url=attachments[0] if attachments else image_url, position_fen=position_fen, pgn=pgn, tags=tags, priority=priority, action_required=body.get("action_required", False))
    if entry is not None:
        entry["attachments"] = attachments
    save_data(data)
    return jsonify({"ok": True, "feedback": entry})

@app.route("/api/admin/feedback/reply", methods=["POST"])
def api_admin_feedback_reply():
    body = request.get_json(force=True, silent=True) or {}
    data = load_data()
    try:
        idx = int(body.get("student_index"))
    except Exception:
        return jsonify({"ok": False, "error": "Élève invalide."}), 400
    feedback_id = body.get("feedback_id")
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Réponse vide."}), 400
    if idx < 0 or idx >= len(data.get("students", [])):
        return jsonify({"ok": False, "error": "Élève introuvable."}), 404
    feedbacks = data["students"][idx].setdefault("client_feedback", [])
    fb = next((f for f in feedbacks if f.get("id") == feedback_id), None)
    if not fb:
        return jsonify({"ok": False, "error": "Feedback introuvable."}), 404
    reply = {"id": str(uuid.uuid4()), "date": now_fr(), "author": "coach", "text": text, "read_by_student": False}
    fb.setdefault("replies", []).append(reply)
    fb["read_by_student"] = False
    fb["date"] = now_fr()
    # Remonte le fil en tête pour qu'il soit immédiatement visible côté Ghost.
    feedbacks.remove(fb)
    feedbacks.insert(0, fb)
    save_data(data)
    return jsonify({"ok": True, "reply": reply, "feedback": fb})


@app.route("/api/admin/game/status", methods=["POST"])
def api_admin_game_status():
    body = request.get_json(force=True, silent=True) or {}
    data = load_data()
    try:
        idx = int(body.get("student_index"))
    except Exception:
        return jsonify({"ok": False, "error": "Élève invalide."}), 400
    game_id = body.get("game_id")
    status = (body.get("status") or "en analyse").strip()
    if idx < 0 or idx >= len(data.get("students", [])):
        return jsonify({"ok": False, "error": "Élève introuvable."}), 404
    games = data["students"][idx].setdefault("client_games", [])
    g = next((x for x in games if x.get("id") == game_id), None)
    if not g:
        return jsonify({"ok": False, "error": "Partie introuvable."}), 404
    g["status"] = status
    g["updated_at"] = now_fr()
    if status == "corrigé":
        add_student_feedback(data, idx, "Partie corrigée", "Ta partie a été corrigée par le coach.", "game", "game", game_id)
    save_data(data)
    return jsonify({"ok": True, "game": g})


@app.route("/api/admin/game/delete", methods=["POST"])
def api_admin_game_delete():
    body = request.get_json(force=True, silent=True) or {}
    data = load_data()
    try:
        idx = int(body.get("student_index"))
    except Exception:
        return jsonify({"ok": False, "error": "Élève invalide."}), 400
    game_id = body.get("game_id")
    if idx < 0 or idx >= len(data.get("students", [])):
        return jsonify({"ok": False, "error": "Élève introuvable."}), 404
    games = data["students"][idx].setdefault("client_games", [])
    before = len(games)
    data["students"][idx]["client_games"] = [g for g in games if g.get("id") != game_id]
    save_data(data)
    return jsonify({"ok": True, "deleted": before != len(data["students"][idx]["client_games"])})

@app.route("/api/admin/payment/update", methods=["POST"])
def api_admin_payment_update():
    body = request.get_json(force=True, silent=True) or {}
    data = load_data()
    user_id = body.get("user_id")
    user = next((u for u in data.get("users", []) if u.get("id") == user_id), None)
    if not user:
        return jsonify({"ok": False, "error": "Compte introuvable."}), 404
    status = body.get("payment_status") or "pending"
    user["payment_status"] = status
    user["next_due"] = (body.get("next_due") or "").strip()
    user["amount_due"] = (body.get("amount_due") or "").strip()
    user["payment_note"] = (body.get("payment_note") or "").strip()
    if body.get("plan"):
        user["plan"] = body.get("plan")
    if status in ("paid", "free"):
        user["last_payment"] = now_fr()
        user["payment_reminders"] = 0
        user["access_restricted"] = False
        plan = find_client_plan(data, user.get("plan") or "session_60")
        user["active_plan"] = {
            "plan_key": plan.get("key"),
            "plan_name": plan.get("name"),
            "used_sessions": safe_int((user.get("active_plan") or {}).get("used_sessions"), 0) if (user.get("active_plan") or {}).get("plan_key") == plan.get("key") else 0,
            "total_sessions": canonical_plan_total(plan),
            "status": "active",
            "started_at": now_fr(),
            "last_session_at": (user.get("active_plan") or {}).get("last_session_at", ""),
        }
        req = user.pop("pending_plan_request", None)
        user.setdefault("plan_history", []).insert(0, {"date": now_fr(), "plan": plan.get("name"), "price": plan.get("price"), "status": "confirmed", "request": req})
        user["plan_history"] = user.get("plan_history", [])[:30]
    elif status == "restricted":
        # V22 : le statut restreint n’est plus utilisé pour les relances.
        # Pour suspendre vraiment un compte, utiliser l’action Bannir/Réactiver.
        user["access_restricted"] = bool(body.get("force_restrict"))
    idx = user.get("student_index")
    if isinstance(idx, int):
        if status == "paid":
            add_student_feedback(data, idx, "Paiement confirmé", "Ton accès est à jour. Tu peux continuer à envoyer tes parties et demander des séances.", "payment", "payment")
        elif status == "overdue":
            add_student_feedback(data, idx, "Rappel paiement", "Ton paiement semble en attente. Régularise-le pour éviter l'interruption du suivi.", "payment", "payment")
    save_data(data)
    return jsonify({"ok": True, "user": user})


@app.route("/api/admin/upload", methods=["POST"])
def api_admin_upload():
    urls = upload_many_from_request("coach")
    if not urls:
        return jsonify({"ok": False, "error": "Aucun fichier valide."}), 400
    return jsonify({"ok": True, "url": urls[0], "urls": urls})

@app.route("/api/admin/payment/confirm", methods=["POST"])
def api_admin_payment_confirm():
    body = request.get_json(force=True, silent=True) or {}
    data = load_data()
    user_id = body.get("user_id")
    user = next((u for u in data.get("users", []) if u.get("id") == user_id), None)
    if not user:
        return jsonify({"ok": False, "error": "Compte introuvable."}), 404

    if body.get("amount_due") is not None:
        user["amount_due"] = (body.get("amount_due") or "").strip()
    if body.get("next_due") is not None:
        user["next_due"] = (body.get("next_due") or "").strip()

    req = user.get("pending_plan_request") or {}
    plan_key = req.get("plan_key") or user.get("plan") or "session_60"
    plan = find_client_plan(data, plan_key)
    user["plan"] = plan.get("key")
    user["payment_status"] = "paid"
    user["payment_reminders"] = 0
    user["access_restricted"] = False
    user["last_payment"] = now_fr()
    user["amount_due"] = user.get("amount_due") or plan.get("price", "")
    user["active_plan"] = {
        "plan_key": plan.get("key"),
        "plan_name": plan.get("name"),
        "used_sessions": 0,
        "total_sessions": canonical_plan_total(plan),
        "status": "active",
        "started_at": now_fr(),
        "last_session_at": "",
        "sessions_log": [],
    }
    req = user.pop("pending_plan_request", None)
    payment_record = {"id": str(uuid.uuid4()), "date": now_fr(), "user_id": user.get("id"), "student_index": user.get("student_index"), "student_name": user.get("name"), "plan_key": plan.get("key"), "plan": plan.get("name"), "amount": parse_fcfa(user.get("amount_due") or plan.get("price")), "amount_label": user.get("amount_due") or plan.get("price"), "status": "paid"}
    data.setdefault("payments_log", []).insert(0, payment_record)
    data["payments_log"] = data.get("payments_log", [])[:300]
    user.setdefault("payment_history", []).insert(0, payment_record)
    user["payment_history"] = user.get("payment_history", [])[:50]
    user.setdefault("plan_history", []).insert(0, {"date": now_fr(), "plan": plan.get("name"), "price": plan.get("price"), "status": "confirmed", "request": req})
    user["plan_history"] = user.get("plan_history", [])[:30]
    idx = user.get("student_index")
    if isinstance(idx, int):
        total = canonical_plan_total(plan)
        add_student_feedback(data, idx, "Paiement confirmé ✅", f"Paiement validé par le coach. Formule active : {plan.get('name')} · {total} séance(s).", "payment", "payment")
    save_data(data)
    return jsonify({"ok": True, "user": user})

@app.route("/api/admin/payment/remind", methods=["POST"])
def api_admin_payment_remind():
    body = request.get_json(force=True, silent=True) or {}
    data = load_data()
    user_id = body.get("user_id")
    user = next((u for u in data.get("users", []) if u.get("id") == user_id), None)
    if not user:
        return jsonify({"ok": False, "error": "Compte introuvable."}), 404
    count = int(user.get("payment_reminders") or 0) + 1
    user["payment_reminders"] = count
    # V22 : une relance ne suspend plus jamais le compte. Elle sert uniquement de rappel.
    user["payment_status"] = "overdue"
    user["access_restricted"] = bool(user.get("banned"))
    msg = (body.get("message") or "").strip()
    idx = user.get("student_index")
    if isinstance(idx, int):
        text = msg or f"Relance paiement #{count} : merci de finaliser ou signaler ton paiement au coach."
        add_student_feedback(data, idx, f"Relance paiement #{count}", text, "payment", "payment", priority="high", action_required=True)
    save_data(data)
    return jsonify({"ok": True, "user": user, "reminders": count, "restricted": False})



@app.route("/api/admin/payment/delete", methods=["POST"])
def api_admin_payment_delete():
    """Supprime une tentative/paiement lié à un forfait côté coach.
    - avec payment_id : supprime une ligne d'encaissement/historique ;
    - avec user_id : remet le Ghost à l'accès de base, supprime la demande/formule active.
    """
    body = request.get_json(force=True, silent=True) or {}
    data = load_data()
    user_id = body.get("user_id")
    payment_id = body.get("payment_id")
    user = next((u for u in data.get("users", []) if u.get("id") == user_id), None) if user_id else None
    removed = False

    if payment_id:
        before = len(data.get("payments_log", []))
        data["payments_log"] = [p for p in data.get("payments_log", []) if p.get("id") != payment_id]
        removed = removed or len(data.get("payments_log", [])) != before
        for u in data.get("users", []):
            if u.get("payment_history"):
                old = len(u.get("payment_history", []))
                u["payment_history"] = [p for p in u.get("payment_history", []) if p.get("id") != payment_id]
                removed = removed or old != len(u.get("payment_history", []))

    if user:
        idx = user.get("student_index")
        ap = user.get("active_plan") or {}
        before_state = {
            "pending": bool(user.get("pending_plan_request")),
            "plan": user.get("plan"),
            "payment_status": user.get("payment_status"),
            "amount_due": user.get("amount_due"),
            "next_due": user.get("next_due"),
            "payment_note": user.get("payment_note"),
            "reminders": user.get("payment_reminders"),
            "active_key": ap.get("plan_key"),
            "active_status": ap.get("status"),
            "used": ap.get("used_sessions"),
            "total": ap.get("total_sessions"),
        }
        already_base = (
            not before_state["pending"]
            and (before_state["plan"] in (None, "", "no_plan"))
            and (before_state["payment_status"] in (None, "", "free"))
            and not before_state["amount_due"] and not before_state["next_due"] and not before_state["payment_note"]
            and safe_int(before_state["reminders"], 0) == 0
            and (before_state["active_key"] in (None, "", "no_plan"))
            and (before_state["active_status"] in (None, "", "inactive"))
        )
        if not already_base:
            # Suppression totale de la tentative/formule affichée dans Paiements / accès élèves.
            # L'inscription Ghost reste validée ; on revient juste à l'accès app de base.
            user.pop("pending_plan_request", None)
            user["active_plan"] = {
                "status": "inactive",
                "plan_key": "no_plan",
                "plan_name": "Accès Ghost",
                "used_sessions": 0,
                "total_sessions": 0,
                "sessions_log": [],
            }
            user["plan"] = "no_plan"
            user["payment_status"] = "free"
            user["amount_due"] = ""
            user["next_due"] = ""
            user["payment_note"] = ""
            user["payment_reminders"] = 0
            # Nettoie uniquement les demandes non finalisées de l'historique des forfaits.
            user["plan_history"] = [h for h in user.get("plan_history", []) if h.get("status") not in ("pending", "pending_payment", "awaiting_validation")]
            # V26 : une seule notification utile, pas une pluie de notifications si le coach reclique.
            if isinstance(idx, int):
                add_student_feedback(
                    data,
                    idx,
                    "Paiement annulé",
                    "La tentative de paiement ou le forfait a été retiré. Ton accès Ghost reste actif.",
                    "payment",
                    "payment",
                )
            removed = True

    save_data(data)
    return jsonify({"ok": True, "removed": removed})

@app.route("/api/admin/payment/log_status", methods=["POST"])
def api_admin_payment_log_status():
    body = request.get_json(force=True, silent=True) or {}
    payment_id = body.get("payment_id")
    status = (body.get("status") or "pending").strip()
    if status not in ("paid", "validated", "confirmed", "pending", "awaiting_validation"):
        return jsonify({"ok": False, "error": "Statut invalide."}), 400
    data = load_data()
    found = None
    for rec in data.get("payments_log", []):
        if rec.get("id") == payment_id:
            rec["status"] = status
            rec["updated_at"] = now_fr()
            if not rec.get("student_name"):
                rec["student_name"] = student_name_from_index(data, rec.get("student_index"))
            found = rec
            break
    if not found:
        return jsonify({"ok": False, "error": "Paiement introuvable."}), 404
    for u in data.get("users", []):
        for pmt in u.get("payment_history", []) or []:
            if pmt.get("id") == payment_id:
                pmt["status"] = status
                pmt["updated_at"] = now_fr()
    save_data(data)
    return jsonify({"ok": True, "payment": found})

@app.route("/api/admin/registration/delete", methods=["POST"])
def api_admin_registration_delete():
    body = request.get_json(force=True, silent=True) or {}
    data = load_data()
    rid = body.get("request_id")
    code_id = body.get("code_id")
    if rid:
        data["registration_requests"] = [r for r in data.get("registration_requests", []) if r.get("id") != rid]
        data["registration_codes"] = [c for c in data.get("registration_codes", []) if c.get("source_request_id") != rid]
        data["payments_log"] = [p for p in data.get("payments_log", []) if p.get("source_request_id") != rid]
    if code_id:
        data["registration_codes"] = [c for c in data.get("registration_codes", []) if c.get("id") != code_id]
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/admin/user/ban", methods=["POST"])
def api_admin_user_ban():
    body = request.get_json(force=True, silent=True) or {}
    data = load_data()
    user_id = body.get("user_id")
    banned = bool(body.get("banned"))
    user = next((u for u in data.get("users", []) if u.get("id") == user_id), None)
    if not user:
        return jsonify({"ok": False, "error": "Compte introuvable."}), 404
    user["banned"] = banned
    user["access_restricted"] = banned
    idx = user.get("student_index")
    if isinstance(idx, int):
        if banned:
            add_student_feedback(data, idx, "Accès suspendu", "Ton accès Ghost a été temporairement suspendu par le coach.", "account", "account", priority="urgent")
        else:
            add_student_feedback(data, idx, "Accès réactivé", "Ton accès Ghost a été réactivé par le coach.", "account", "account")
    save_data(data)
    return jsonify({"ok": True, "user": user})

@app.route("/api/client/elo/sync", methods=["POST"])
def api_client_elo_sync():
    data, user, resp, code = require_client_json()
    if resp: return resp, code
    idx = user.get("student_index")
    if not isinstance(idx, int) or idx < 0 or idx >= len(data.get("students", [])):
        return jsonify({"ok": False, "error": "Profil élève introuvable."}), 404
    s = data["students"][idx]
    results = []
    today = datetime.now().strftime("%d/%m/%Y")
    hist = s.setdefault("elo_history", [])
    today_entry = next((e for e in hist if e.get("date") == today), None)
    if today_entry is None:
        today_entry = {"date": today, "note": "Sync élève"}
        hist.append(today_entry)
    if (s.get("lichess") or "").strip():
        try:
            d = fetch_lichess(s["lichess"].strip())
            if d["elo_bullet"]: s["elo_li_bullet"] = d["elo_bullet"]
            if d["elo_blitz"]: s["elo_li_blitz"] = d["elo_blitz"]
            if d["elo_rapid"]: s["elo_li_rapid"] = d["elo_rapid"]
            if d["elo_classical"]: s["elo_li_classical"] = d["elo_classical"]
            s["li_games"] = d["games_total"]; s["li_last_online"] = d["last_online"]
            today_entry.update({"elo_li": d["elo_blitz"], "elo_li_blitz": d["elo_blitz"], "elo_li_bullet": d["elo_bullet"], "elo_li_rapid": d["elo_rapid"], "elo_li_classical": d["elo_classical"]})
            results.append("Lichess synchronisé")
        except Exception as e:
            results.append(f"Lichess non synchronisé : {e}")
    if (s.get("chesscom") or "").strip():
        try:
            d = fetch_chesscom(s["chesscom"].strip())
            if d["elo_bullet"]: s["elo_cc_bullet"] = d["elo_bullet"]
            if d["elo_blitz"]: s["elo_cc_blitz"] = d["elo_blitz"]
            if d["elo_rapid"]: s["elo_cc_rapid"] = d["elo_rapid"]
            s["cc_games"] = d["games_total"]; s["cc_joined"] = d["joined"]
            today_entry.update({"elo_cc": d["elo_blitz"], "elo_cc_blitz": d["elo_blitz"], "elo_cc_bullet": d["elo_bullet"], "elo_cc_rapid": d["elo_rapid"]})
            results.append("Chess.com synchronisé")
        except Exception as e:
            results.append(f"Chess.com non synchronisé : {e}")
    if not results:
        return jsonify({"ok": False, "error": "Aucun pseudo Lichess ou Chess.com n'est renseigné dans ton profil."}), 400
    s["updated"] = now_fr()
    data["students"][idx] = s
    add_client_notification(data, "ELO actualisé", f"{user.get('name')} a actualisé son ELO depuis l'espace Ghost.", "elo", user.get("id"), idx)
    save_data(data)
    fresh = public_student_payload(s)
    return jsonify({"ok": True, "messages": results, "avg_elo": fresh.get("avg_elo"), "rank": fresh.get("rank", {}).get("title"), "island": fresh.get("island", {}).get("name")})

@app.route("/api/admin/package/use", methods=["POST"])
def api_admin_package_use():
    body = request.get_json(force=True, silent=True) or {}
    data = load_data()
    user_id = body.get("user_id")
    user = next((u for u in data.get("users", []) if u.get("id") == user_id), None)
    if not user:
        return jsonify({"ok": False, "error": "Compte introuvable."}), 404
    active = user.setdefault("active_plan", {})
    if normalize_plan_status(active.get("status")) in ("pending", "completed", "inactive"):
        return jsonify({"ok": False, "error": "Aucun forfait actif à consommer. Valide un paiement ou demande un renouvellement."}), 400

    plan = find_client_plan(data, active.get("plan_key") or user.get("plan") or "session_60")
    total = canonical_plan_total(plan)
    used = safe_float(active.get("used_sessions"), 0.0)
    delta = safe_float(body.get("delta"), 1.0)
    # La séance découverte reste une séance unique : un clic de validation la termine.
    if (plan.get("key") or "") == "session_30":
        delta = 1.0
    used = max(0.0, min(float(total), used + delta))
    log_entry = {"date": now_fr(), "delta": delta, "used_after": used, "total": total, "note": (body.get("note") or "").strip()}
    sessions_log = active.setdefault("sessions_log", [])
    sessions_log.append(log_entry)
    if len(sessions_log) > 80:
        del sessions_log[:-80]

    completed = used >= total
    active.update({
        "plan_key": plan.get("key"),
        "plan_name": plan.get("name"),
        "total_sessions": total,
        "used_sessions": used,
        "last_session_at": now_fr(),
        "status": "completed" if completed else "active",
        "completed_at": now_fr() if completed else active.get("completed_at", ""),
    })
    user["active_plan"] = active
    user["payment_status"] = "paid" if user.get("payment_status") != "free" else "free"
    user["access_restricted"] = False
    idx = user.get("student_index")
    percent = int(round(used / max(1, total) * 100))
    if isinstance(idx, int):
        if completed:
            user.setdefault("plan_history", []).insert(0, {"date": now_fr(), "plan": plan.get("name"), "price": plan.get("price"), "status": "completed", "used_sessions": used, "total_sessions": total})
            user["plan_history"] = user.get("plan_history", [])[:30]
            add_student_feedback(data, idx, "Forfait terminé ✅", f"Ton forfait {plan.get('name')} est terminé ({fmt_qty(used)}/{fmt_qty(total)} séance(s)). Ce n’est pas un retard de paiement : choisis une nouvelle formule quand tu veux continuer.", "payment", "plan", priority="normal")
            add_client_notification(data, "Forfait terminé", f"{user.get('name')} a terminé son forfait {plan.get('name')}.", "payment", user.get("id"), idx)
        else:
            add_student_feedback(data, idx, "Séance comptabilisée", f"Une séance a été validée par le coach. Forfait utilisé à {percent}% ({fmt_qty(used)}/{fmt_qty(total)}).", "appointment", "session")
    save_data(data)
    return jsonify({"ok": True, "active_plan": active, "percent": percent, "completed": completed})

@app.route("/api/admin/package/undo", methods=["POST"])
def api_admin_package_undo():
    body = request.get_json(force=True, silent=True) or {}
    data = load_data()
    user_id = body.get("user_id")
    user = next((u for u in data.get("users", []) if u.get("id") == user_id), None)
    if not user:
        return jsonify({"ok": False, "error": "Compte introuvable."}), 404
    active = user.setdefault("active_plan", {})
    total = safe_float(active.get("total_sessions"), 1)
    used = max(0.0, safe_float(active.get("used_sessions"), 0) - 1.0)
    active["used_sessions"] = used
    active["status"] = "active" if user.get("payment_status") in ("paid", "free") else "pending"
    active["completed_at"] = ""
    if active.get("sessions_log"):
        active["sessions_log"].pop()
    save_data(data)
    return jsonify({"ok": True, "active_plan": active, "percent": int(round(used / max(1, total) * 100))})

@app.route("/api/client/homework/submit", methods=["POST"])
def api_client_homework_submit():
    data, user, resp, code = require_client_json()
    if resp: return resp, code
    if client_is_restricted(user): return restricted_response(user)
    body = request.get_json(force=True, silent=True) or {}
    idx = user.get("student_index")
    try:
        di = int(body.get("devoir_index"))
    except Exception:
        return jsonify({"ok": False, "error": "Devoir invalide."}), 400
    devoirs = data.get("students", [])[idx].setdefault("devoirs", [])
    if di < 0 or di >= len(devoirs):
        return jsonify({"ok": False, "error": "Devoir introuvable."}), 404
    d = devoirs[di]
    d.setdefault("id", str(uuid.uuid4()))
    d["status"] = "🧑‍🏫 À corriger"
    d["student_submission"] = {
        "date": now_fr(),
        "text": (body.get("text") or "").strip(),
        "image_url": (body.get("image_url") or "").strip(),
        "attachments": body.get("attachments") or ([] if not (body.get("image_url") or "").strip() else [(body.get("image_url") or "").strip()]),
        "url": (body.get("url") or "").strip(),
    }
    add_client_notification(data, "Devoir rendu", f"{user.get('name')} a rendu : {d.get('title','devoir')}", "homework", user.get("id"), idx)
    save_data(data)
    return jsonify({"ok": True, "devoir": d})

@app.route("/api/admin/homework/review", methods=["POST"])
def api_admin_homework_review():
    body = request.get_json(force=True, silent=True) or {}
    data = load_data()
    try:
        idx = int(body.get("student_index")); di = int(body.get("devoir_index"))
    except Exception:
        return jsonify({"ok": False, "error": "Devoir invalide."}), 400
    if idx < 0 or idx >= len(data.get("students", [])):
        return jsonify({"ok": False, "error": "Élève introuvable."}), 404
    devoirs = data["students"][idx].setdefault("devoirs", [])
    if di < 0 or di >= len(devoirs):
        return jsonify({"ok": False, "error": "Devoir introuvable."}), 404
    d = devoirs[di]
    d.setdefault("id", str(uuid.uuid4()))
    d["status"] = body.get("status") or "✅ Corrigé"
    d["coach_correction"] = {
        "date": now_fr(),
        "text": (body.get("text") or "").strip(),
        "image_url": (body.get("image_url") or "").strip(),
        "attachments": body.get("attachments") or ([] if not (body.get("image_url") or "").strip() else [(body.get("image_url") or "").strip()]),
        "position_fen": (body.get("position_fen") or "").strip(),
    }
    title = "Correction de devoir"
    text = d["coach_correction"].get("text") or f"Ton devoir ‘{d.get('title','')}’ a été corrigé."
    feedback = add_student_feedback(data, idx, title, text, "homework", "devoir", d.get("id"), image_url=d["coach_correction"].get("image_url"), position_fen=d["coach_correction"].get("position_fen"))
    save_data(data)
    return jsonify({"ok": True, "devoir": d, "feedback": feedback})

@app.route("/api/client/payment/claim", methods=["POST"])
def api_client_payment_claim():
    data, user, resp, code = require_client_json()
    if resp: return resp, code
    body = request.get_json(force=True, silent=True) or {}
    msg = (body.get("message") or "").strip()
    idx = user.get("student_index")
    user["payment_status"] = "pending"
    user["access_restricted"] = False
    user["payment_claimed_at"] = now_fr()
    add_client_notification(data, "Paiement à vérifier", f"{user.get('name')} indique avoir payé. {msg}", "payment", user.get("id"), idx)
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/client/feedback/reply", methods=["POST"])
def api_client_feedback_reply():
    data, user, resp, code = require_client_json()
    if resp: return resp, code
    body = request.get_json(force=True, silent=True) or {}
    feedback_id = body.get("feedback_id")
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Réponse vide."}), 400
    idx = user.get("student_index")
    if not isinstance(idx, int) or idx < 0 or idx >= len(data.get("students", [])):
        return jsonify({"ok": False, "error": "Profil introuvable."}), 404
    feedbacks = data["students"][idx].setdefault("client_feedback", [])
    fb = next((f for f in feedbacks if f.get("id") == feedback_id), None)
    if not fb:
        return jsonify({"ok": False, "error": "Feedback introuvable."}), 404
    reply = {"id": str(uuid.uuid4()), "date": now_fr(), "author": "student", "text": text, "read_by_coach": False}
    fb.setdefault("replies", []).append(reply)
    add_client_notification(data, "Réponse élève", f"{user.get('name')} a répondu au feedback : {fb.get('title','')}", "feedback_reply", user.get("id"), idx)
    save_data(data)
    return jsonify({"ok": True, "reply": reply})

@app.route("/api/client/feedback/read", methods=["POST"])
def api_client_feedback_read():
    data, user, resp, code = require_client_json()
    if resp: return resp, code
    idx = user.get("student_index")
    for f in data.get("students", [])[idx].get("client_feedback", []):
        f["read_by_student"] = True
    save_data(data)
    return jsonify({"ok": True})


@app.route("/api/client/notifications/read", methods=["POST"])
def api_client_notifications_read():
    data, user, resp, code = require_client_json()
    if resp: return resp, code
    body = request.get_json(force=True, silent=True) or {}
    notification_id = body.get("notification_id")
    idx = user.get("student_index")
    marked = 0
    if isinstance(idx, int) and 0 <= idx < len(data.get("students", [])):
        for f in data["students"][idx].get("client_feedback", []):
            if not notification_id or f.get("id") == notification_id:
                if not f.get("read_by_student"):
                    marked += 1
                f["read_by_student"] = True
    save_data(data)
    return jsonify({"ok": True, "marked": marked})

@app.route("/api/client/notifications")
def api_client_notifications_list():
    data = load_data()
    user = get_current_user(data)
    if not user:
        return jsonify({"ok": False, "error": "not_authenticated"}), 401
    idx = user.get("student_index")
    student = data.get("students", [])[idx] if isinstance(idx, int) and 0 <= idx < len(data.get("students", [])) else None
    payload = public_student_payload(student)
    notes = (payload or {}).get("client_notifications", [])
    return jsonify({"ok": True, "count": len(notes), "notifications": notes})

@app.route("/api/client/onboarding/done", methods=["POST"])
def api_client_onboarding_done():
    data, user, resp, code = require_client_json()
    if resp: return resp, code
    user["onboarding_done"] = True
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/client/password/reset_request", methods=["POST"])
def api_client_password_reset_request():
    body = request.get_json(force=True, silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    data = load_data()
    u = next((x for x in data.get("users", []) if (x.get("email") or "").strip().lower() == email), None)
    if u:
        add_client_notification(data, "Mot de passe oublié", f"{u.get('name')} demande une réinitialisation de mot de passe.", "account", u.get("id"), u.get("student_index"), target_url="/admin/clients#accounts")
        save_data(data)
    return jsonify({"ok": True, "message": "Si ce compte existe, le coach a été notifié."})

@app.route("/api/admin/password/reset", methods=["POST"])
def api_admin_password_reset():
    body = request.get_json(force=True, silent=True) or {}
    user_id = body.get("user_id")
    new_password = (body.get("password") or "").strip()
    if len(new_password) < 6:
        return jsonify({"ok": False, "error": "Mot de passe trop court."}), 400
    data = load_data()
    user = next((u for u in data.get("users", []) if u.get("id") == user_id), None)
    if not user:
        return jsonify({"ok": False, "error": "Compte introuvable."}), 404
    user["password_hash"] = generate_password_hash(new_password)
    user.pop("password", None)
    idx = user.get("student_index")
    if isinstance(idx, int):
        add_student_feedback(data, idx, "Mot de passe mis à jour", "Ton mot de passe a été réinitialisé par le coach. Connecte-toi avec le nouveau mot de passe reçu.", "system", "account")
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/admin/tournaments/create", methods=["POST"])
def api_admin_tournament_create():
    body = request.get_json(force=True, silent=True) or {}
    data = load_data()
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"ok": False, "error": "Titre obligatoire."}), 400
    target_all = bool(body.get("target_all", True))
    targets = body.get("targets") or []
    entry = {"id": str(uuid.uuid4()), "date": now_fr(), "title": title, "day": (body.get("day") or "").strip(), "time": (body.get("time") or "").strip(), "link": (body.get("link") or "").strip(), "description": (body.get("description") or "").strip(), "preparation": (body.get("preparation") or "").strip(), "target_all": target_all, "targets": targets, "responses": {}}
    data.setdefault("tournaments", []).insert(0, entry)
    data["tournaments"] = data.get("tournaments", [])[:80]
    if target_all:
        for u in data.get("users", []):
            idx = u.get("student_index")
            if isinstance(idx, int):
                add_student_feedback(data, idx, "Tournoi planifié", f"{title} — {entry.get('day')} {entry.get('time')}. Consulte l’onglet Tournois.", "tournament", "tournament", entry["id"])
    else:
        for idx in targets:
            try: idx = int(idx)
            except Exception: continue
            add_student_feedback(data, idx, "Tournoi planifié", f"{title} — {entry.get('day')} {entry.get('time')}. Consulte l’onglet Tournois.", "tournament", "tournament", entry["id"])
    save_data(data)
    return jsonify({"ok": True, "tournament": entry})

@app.route("/api/admin/tournaments/delete", methods=["POST"])
def api_admin_tournament_delete():
    body = request.get_json(force=True, silent=True) or {}
    tid = body.get("tournament_id")
    if not tid:
        return jsonify({"ok": False, "error": "ID tournoi manquant."}), 400
    data = load_data()
    before = len(data.get("tournaments", []))
    data["tournaments"] = [t for t in data.get("tournaments", []) if t.get("id") != tid]
    if len(data.get("tournaments", [])) == before:
        return jsonify({"ok": False, "error": "Tournoi introuvable."}), 404
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/client/tournament/respond", methods=["POST"])
def api_client_tournament_respond():
    data, user, resp, code = require_client_json()
    if resp: return resp, code
    body = request.get_json(force=True, silent=True) or {}
    tid = body.get("tournament_id")
    response = (body.get("response") or "").strip()
    if response not in ("participe", "indisponible", "retract"):
        response = "participe"
    t = next((x for x in data.get("tournaments", []) if x.get("id") == tid), None)
    if not t:
        return jsonify({"ok": False, "error": "Tournoi introuvable."}), 404
    responses = t.setdefault("responses", {})
    key = str(user.get("student_index"))
    if response == "retract":
        responses.pop(key, None)
        notif_text = f"{user.get('name')} a retiré sa réponse au tournoi {t.get('title')}."
    else:
        responses[key] = {"response": response, "date": now_fr(), "name": user.get("name")}
        notif_text = f"{user.get('name')} a répondu au tournoi {t.get('title')} : {response}."
    enriched = enrich_tournament(data, t, user.get("student_index"))
    add_client_notification(data, "Réponse tournoi", notif_text, "tournament", user.get("id"), user.get("student_index"), target_url="/admin/clients#tournaments")
    save_data(data)
    return jsonify({"ok": True, "tournament": enriched, "response": enriched.get("_my_response", "")})

@app.route("/api/client/message/send", methods=["POST"])
def api_client_message_send():
    data, user, resp, code = require_client_json()
    if resp: return resp, code
    if client_is_restricted(user): return restricted_response(user)
    body = request.get_json(force=True, silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Message vide."}), 400
    mode = (body.get("target_mode") or "pairs").strip()
    targets = []
    target_all = False
    target_label = "mes binômes"

    if mode == "all":
        target_all = True
        targets = [i for i, _s in enumerate(data.get("students", [])) if i != user.get("student_index")]
        target_label = "tous les élèves"
    elif mode == "student":
        try:
            target_idx = int(body.get("target_student"))
        except Exception:
            return jsonify({"ok": False, "error": "Choisis un élève destinataire."}), 400
        if target_idx == user.get("student_index") or target_idx < 0 or target_idx >= len(data.get("students", [])):
            return jsonify({"ok": False, "error": "Destinataire invalide."}), 400
        targets = [target_idx]
        target_label = data.get("students", [])[target_idx].get("name") or "un élève"
    else:
        # Binômes : option gardée par défaut, mais ce n'est plus la seule possibilité.
        pairs = student_pairs_payload(data, user.get("student_index"))
        name_to_idx = { (s.get("name") or "").strip(): i for i,s in enumerate(data.get("students", [])) }
        for p in pairs:
            partner = p.get("partner") or ""
            if partner in name_to_idx and name_to_idx[partner] != user.get("student_index"):
                targets.append(name_to_idx[partner])
        if not targets:
            return jsonify({"ok": False, "error": "Aucun binôme disponible. Choisis un élève précis ou tous les élèves."}), 400

    entry = {"id": str(uuid.uuid4()), "date": now_fr(), "from_user_id": user.get("id"), "from_student_index": user.get("student_index"), "from_name": user.get("name"), "targets": targets, "target_all": target_all, "target_label": target_label, "text": text, "url": (body.get("url") or "").strip(), "fen": (body.get("fen") or "").strip(), "pgn": (body.get("pgn") or "").strip(), "attachments": body.get("attachments") or []}
    data.setdefault("student_messages", []).insert(0, entry)
    data["student_messages"] = data.get("student_messages", [])[:300]
    add_client_notification(data, "Message élève", f"{user.get('name')} a partagé un message/une partie avec son binôme.", "message", user.get("id"), user.get("student_index"))
    save_data(data)
    return jsonify({"ok": True, "message": entry})

@app.route("/api/admin/message/reply", methods=["POST"])
def api_admin_message_reply():
    body = request.get_json(force=True, silent=True) or {}
    data = load_data()
    message_id = body.get("message_id")
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Réponse vide."}), 400
    msg = next((m for m in data.get("student_messages", []) if m.get("id") == message_id), None)
    if not msg:
        return jsonify({"ok": False, "error": "Message introuvable."}), 404
    reply = {"id": str(uuid.uuid4()), "date": now_fr(), "author": "coach", "text": text}
    msg.setdefault("replies", []).append(reply)
    msg["coach_read"] = True
    msg["updated_at"] = now_fr()
    recipients = []
    sender_idx = msg.get("from_student_index")
    if isinstance(sender_idx, int):
        recipients.append(sender_idx)
    for idx in msg.get("targets") or []:
        try:
            idx = int(idx)
        except Exception:
            continue
        if idx not in recipients:
            recipients.append(idx)
    for idx in recipients:
        add_student_feedback(data, idx, "Réponse coach", text, "message", "student_message", message_id)
    save_data(data)
    return jsonify({"ok": True, "reply": reply, "message": msg})


@app.route("/api/client/profile/update", methods=["POST"])
def api_client_profile_update():
    data, user, resp, code = require_client_json()
    if resp: return resp, code
    body = request.get_json(force=True, silent=True) or {}
    idx = user.get("student_index")
    if not isinstance(idx, int) or idx < 0 or idx >= len(data.get("students", [])):
        return jsonify({"ok": False, "error": "Profil introuvable."}), 404
    # Champs simples et volontairement limités pour éviter le désordre et l'injection.
    allowed = {
        "name": 60, "email": 120, "phone": 40, "telegram_chat_id": 60, "city": 60, "birthdate": 10,
        "lichess": 60, "chesscom": 60, "goal": 240, "style": 120,
        "interests": 300, "strengths": 300, "weaknesses": 300, "special_difficulties": 400
    }
    stu = data["students"][idx]
    for key, max_len in allowed.items():
        val = (body.get(key) or "").strip()
        # On garde du texte pur : Jinja échappe déjà l'affichage, mais on nettoie les balises évidentes.
        val = val.replace("<", "").replace(">", "")[:max_len]
        if key == "birthdate":
            # format attendu côté navigateur : YYYY-MM-DD
            if val:
                try:
                    datetime.strptime(val[:10], "%Y-%m-%d")
                    val = val[:10]
                except Exception:
                    val = ""
        if key in ("name", "email") and not val:
            continue
        stu[key] = val
        if key in ("name", "email"):
            user[key] = val
    # âge calculé automatiquement côté coach depuis la date de naissance
    if stu.get("birthdate"):
        age = calculate_age_from_birthdate(stu.get("birthdate"))
        if age != "":
            stu["age"] = age
            user["age"] = age
            user["birthdate"] = stu.get("birthdate")
    user.setdefault("profile_updated_at", now_fr())
    user["profile_updated_at"] = now_fr()
    data["students"][idx] = stu
    save_data(data)
    return jsonify({"ok": True, "student": public_student_payload(stu)})

@app.route("/api/admin/exercise/create", methods=["POST"])
def api_admin_exercise_create():
    body = request.get_json(force=True, silent=True) or {}
    data = load_data()
    title = (body.get("title") or "Exercice tactique").strip()[:90]
    text = (body.get("text") or "").strip()[:1200]
    level = (body.get("level") or "Tous niveaux").strip()[:60]
    solution = (body.get("solution") or "").strip()[:1200]
    targets = body.get("targets") or []
    attachments = body.get("attachments") or []
    if not title or not text:
        return jsonify({"ok": False, "error": "Titre et consigne obligatoires."}), 400
    clean_targets=[]
    if body.get("target_all"):
        clean_targets = list(range(len(data.get("students", []))))
    else:
        for t in targets:
            try: clean_targets.append(int(t))
            except Exception: pass
    clean_targets = [i for i in dict.fromkeys(clean_targets) if 0 <= i < len(data.get("students", []))]
    if not clean_targets:
        return jsonify({"ok": False, "error": "Choisis au moins un Ghost."}), 400
    exercise = {"id": str(uuid.uuid4()), "date": now_fr(), "title": title, "text": text, "level": level, "solution": solution, "attachments": attachments, "targets": clean_targets, "status": "envoyé"}
    data.setdefault("exercises", []).insert(0, exercise)
    data["exercises"] = data.get("exercises", [])[:200]
    for idx in clean_targets:
        devoir = {"title": "🎯 " + title, "text": text, "status": "📋 À faire", "date": now_fr(), "level": level, "attachments": attachments, "exercise_id": exercise["id"], "solution": solution, "submissions": []}
        data["students"][idx].setdefault("devoirs", []).append(devoir)
        add_student_feedback(data, idx, "Nouvel exercice tactique", f"{title} — réponds dans l’onglet Devoirs avec les coups par écrit.", "homework", "exercise", exercise["id"])
    save_data(data)
    return jsonify({"ok": True, "exercise": exercise})

@app.route("/api/admin/backup/export")
def api_admin_backup_export():
    data = load_data()
    resp = jsonify(data)
    resp.headers["Content-Disposition"] = "attachment; filename=ghost_backup.json"
    return resp

@app.route("/api/admin/plans/save", methods=["POST"])
def api_admin_plans_save():
    body = request.get_json(force=True, silent=True) or {}
    plans = body.get("plans") or []
    clean = []
    for p in plans:
        clean.append({
            "key": (p.get("key") or p.get("name") or "plan").lower().replace(" ", "_"),
            "name": p.get("name") or "Formule",
            "price": p.get("price") or "",
            "period": p.get("period") or "",
            "sessions_total": 1 if ((p.get("key") or "").lower()=="session_30" or "découverte" in (p.get("name") or "").lower() or "decouverte" in (p.get("name") or "").lower()) else safe_int(p.get("sessions_total"), 1),
            "desc": p.get("desc") or "",
        })
    data = load_data()
    data["client_price_plans"] = clean or default_client_price_plans()
    reconcile_client_plan_state(data)
    save_data(data)
    return jsonify({"ok": True, "plans": data["client_price_plans"]})

if __name__ == "__main__":
    import webbrowser, time
    scheduler.start()
    threading.Thread(target=lambda:(time.sleep(0.8),webbrowser.open("http://127.0.0.1:5031")),daemon=True).start()
    print("\n♟ GHOST Chess Manager v34 Stabilisation → http://127.0.0.1:5031\n")
    app.run(debug=False, port=5031)
