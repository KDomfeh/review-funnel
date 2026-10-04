#!/usr/bin/env python3
"""Review Funnel - production app for The Foreman Agency."""
import os
import sqlite3
import json
import urllib.request
from datetime import datetime, timezone
from flask import Flask, request, jsonify, redirect, make_response, g

app = Flask(__name__, static_folder="static", static_url_path="/static")

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
GOOGLE_REVIEW_URL_DEFAULT = os.environ.get(
    "GOOGLE_REVIEW_URL",
    "https://search.google.com/local/writereview?placeid=ChIJyfP0lbUN9YgRhx_1kkVHBJU",
)
ALERT_EMAIL_DEFAULT = os.environ.get("ALERT_EMAIL", "Kd@arcadianpartners.org")
OWNER_EMAIL_DEFAULT = os.environ.get("OWNER_EMAIL", "Kd@arcadianpartners.org")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "Review Funnel <reviews@theforemanagency.com>")
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "data.db"))

app.secret_key = SECRET_KEY

from itsdangerous import URLSafeTimedSerializer
serializer = URLSafeTimedSerializer(SECRET_KEY, salt="dashboard-magic-link")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute(
        """CREATE TABLE IF NOT EXISTS responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            stars INTEGER NOT NULL,
            agent_name TEXT DEFAULT '',
            feedback TEXT DEFAULT '',
            alert_queued INTEGER DEFAULT 0,
            alert_sent INTEGER DEFAULT 0
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )"""
    )
    db.execute(
        "INSERT OR IGNORE INTO settings(key,value) VALUES('google_review_url',?)",
        (GOOGLE_REVIEW_URL_DEFAULT,),
    )
    db.execute(
        "INSERT OR IGNORE INTO settings(key,value) VALUES('alert_email',?)",
        (ALERT_EMAIL_DEFAULT,),
    )
    db.commit()
    db.close()


def get_setting(key, default=""):
    db = get_db()
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    db = get_db()
    db.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    db.commit()


def send_email(to, subject, text):
    """Send via Resend if configured, else log and return False."""
    if not RESEND_API_KEY:
        app.logger.info(f"[email disabled] to={to} subject={subject}\n{text}")
        return False
    data = json.dumps({"from": FROM_EMAIL, "to": [to], "subject": subject, "text": text}).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=data,
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            app.logger.info(f"Resend status {resp.status}")
            return resp.status in (200, 201, 202)
    except Exception as e:
        app.logger.error(f"Resend failed: {e}")
        return False


def is_dashboard_authed():
    token = request.cookies.get("rf_auth", "")
    if not token:
        # also allow ?token= for magic link click
        token = request.args.get("token", "")
    if not token:
        return False
    try:
        email = serializer.loads(token, max_age=60 * 60 * 12)
        return email.strip().lower() == OWNER_EMAIL_DEFAULT.strip().lower()
    except Exception:
        return False


INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Review Funnel - The Foreman Agency</title>
<style>
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f7f7f5;margin:0;padding:24px;color:#1a1a1a}
  .card{max-width:520px;margin:40px auto;background:#fff;border-radius:16px;padding:32px;box-shadow:0 8px 30px rgba(0,0,0,.08);text-align:center}
  .logo{max-width:220px;margin:0 auto 16px;display:block}
  h1{font-size:22px;margin:8px 0 20px}
  .stars{display:flex;gap:8px;justify-content:center;margin:16px 0}
  .star{font-size:44px;cursor:pointer;color:#ddd;border:none;background:none;padding:4px}
  .star.active{color:#f5a623}
  .btn{display:inline-block;background:#0b5fff;color:#fff;border:none;border-radius:10px;padding:12px 20px;font-size:16px;cursor:pointer;margin-top:12px;text-decoration:none}
  .btn.secondary{background:#eee;color:#111}
  input,textarea{width:100%;padding:10px;border:1px solid #ddd;border-radius:8px;margin:8px 0;font-size:15px;box-sizing:border-box}
  .inspire{background:#f0f4ff;border-radius:10px;padding:12px;margin:12px 0;font-size:15px}
  .copy{margin-top:8px}
  .hidden{display:none}
  p.muted{color:#666;font-size:14px}
</style>
</head>
<body>
<div class="card" id="step1">
  <img src="/static/logo.jpg" class="logo" alt="The Foreman Agency"/>
  <h1>We love ratings! Please give us one!</h1>
  <p class="muted">Tap a star to rate your experience</p>
  <div class="stars" id="stars"></div>
</div>

<div class="card hidden" id="step5">
  <img src="/static/logo.jpg" class="logo" alt="The Foreman Agency"/>
  <h1>Thank you! You made our day.</h1>
  <p>Would you mind sharing that on Google? It helps other families find us.</p>
  <a class="btn" id="googleBtn" href="#" target="_blank" rel="noopener">Leave us a Google review</a>
  <div style="margin-top:20px;text-align:left">
    <label><strong>Who helped you?</strong></label>
    <input id="agentName5" placeholder="Agent name"/>
    <div class="inspire" id="inspireText">I worked with [Agent Name] at The Foreman Agency and they were great.</div>
    <button class="btn secondary copy" onclick="copyInspire()">Copy text</button>
    <p class="muted" id="copyMsg"></p>
  </div>
</div>

<div class="card hidden" id="stepLow">
  <img src="/static/logo.jpg" class="logo" alt="The Foreman Agency"/>
  <h1>Thanks for your honest feedback.</h1>
  <p class="muted">Tell us what happened — this goes privately to our team.</p>
  <div style="text-align:left">
    <label>Who helped you? (optional)</label>
    <input id="agentNameLow" placeholder="Agent name"/>
    <label>We love great feedback. How did we do?</label>
    <textarea id="feedback" rows="4" placeholder="Your private feedback"></textarea>
    <button class="btn" onclick="submitLow()">Send private feedback</button>
  </div>
  <p class="muted" style="margin-top:16px">You can also <a id="googleLinkLow" href="#" target="_blank" rel="noopener">leave us a Google review</a> if you'd like.</p>
</div>

<div class="card hidden" id="stepDone">
  <img src="/static/logo.jpg" class="logo" alt="The Foreman Agency"/>
  <h1>Thank you.</h1>
  <p class="muted" id="doneMsg">We've received your feedback.</p>
  <p class="muted">If you'd like to share publicly too:</p>
  <a class="btn" id="googleBtnDone" href="#" target="_blank" rel="noopener">Leave us a Google review</a>
</div>

<script>
let starsVal=0, googleUrl="";
const starsEl=document.getElementById('stars');
for(let i=1;i<=5;i++){
  const b=document.createElement('button');
  b.className='star'; b.textContent='★'; b.dataset.v=i;
  b.onclick=()=>selectStars(i);
  b.onmouseover=()=>paint(i); b.onmouseleave=()=>paint(starsVal);
  starsEl.appendChild(b);
}
function paint(n){[...starsEl.children].forEach(s=>s.classList.toggle('active', s.dataset.v<=n));}
async function loadConfig(){
  try{const r=await fetch('/api/config');const j=await r.json();googleUrl=j.googleReviewUrl;
    document.getElementById('googleBtn').href=googleUrl;
    document.getElementById('googleLinkLow').href=googleUrl;
    document.getElementById('googleBtnDone').href=googleUrl;}catch(e){}
}
loadConfig();
function selectStars(n){
  starsVal=n; paint(n);
  fetch('/api/submit',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({stars:n, agent_name:'', feedback:''})});
  document.getElementById('step1').classList.add('hidden');
  if(n===5){document.getElementById('step5').classList.remove('hidden');}
  else{document.getElementById('stepLow').classList.remove('hidden'); window._stars=n;}
}
document.getElementById('agentName5').addEventListener('input',e=>{
  const name=e.target.value.trim()||'[Agent Name]';
  document.getElementById('inspireText').textContent=`I worked with ${name} at The Foreman Agency and they were great.`;
});
function copyInspire(){
  const t=document.getElementById('inspireText').textContent;
  navigator.clipboard.writeText(t).then(()=>{document.getElementById('copyMsg').textContent='Copied!';});
}
async function submitLow(){
  const agent=document.getElementById('agentNameLow').value;
  const fb=document.getElementById('feedback').value;
  await fetch('/api/submit',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({stars:window._stars||0, agent_name:agent, feedback:fb, update_last:true})});
  document.getElementById('stepLow').classList.add('hidden');
  document.getElementById('stepDone').classList.remove('hidden');
  document.getElementById('doneMsg').textContent='Your private feedback was sent to our team. Thank you.';
}
</script>
</body>
</html>
"""

DASH_LOGIN_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Dashboard login</title><style>body{font-family:sans-serif;background:#f7f7f5;padding:40px}.card{max-width:400px;margin:auto;background:#fff;padding:24px;border-radius:12px}</style></head>
<body><div class="card"><h2>Owner login</h2><p>Enter your owner email to receive a magic link.</p>
<form method="post"><input name="email" type="email" required placeholder="you@agency.com" style="width:100%;padding:10px;margin:8px 0"/><button style="padding:10px 16px">Send magic link</button></form></div></body></html>"""


@app.route("/")
def index():
    return INDEX_HTML


@app.route("/healthz")
def healthz():
    return jsonify(ok=True)


@app.route("/api/config")
def api_config():
    return jsonify(googleReviewUrl=get_setting("google_review_url", GOOGLE_REVIEW_URL_DEFAULT))


@app.route("/api/submit", methods=["POST"])
def api_submit():
    data = request.get_json(force=True, silent=True) or {}
    try:
        stars = int(data.get("stars", 0))
    except:
        stars = 0
    agent_name = (data.get("agent_name") or "")[:200]
    feedback = (data.get("feedback") or "")[:5000]
    update_last = bool(data.get("update_last"))

    db = get_db()
    now = datetime.now(timezone.utc).isoformat()

    if update_last:
        # update most recent row with same stars (simple heuristic: latest row)
        row = db.execute("SELECT id FROM responses ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            db.execute(
                "UPDATE responses SET agent_name=?, feedback=?, alert_queued=1 WHERE id=?",
                (agent_name, feedback, row["id"]),
            )
            rid = row["id"]
        else:
            cur = db.execute(
                "INSERT INTO responses(created_at,stars,agent_name,feedback,alert_queued,alert_sent) VALUES(?,?,?,?,?,?)",
                (now, stars, agent_name, feedback, 1, 0),
            )
            rid = cur.lastrowid
    else:
        alert_queued = 1 if 0 < stars < 5 else 0
        cur = db.execute(
            "INSERT INTO responses(created_at,stars,agent_name,feedback,alert_queued,alert_sent) VALUES(?,?,?,?,?,?)",
            (now, stars, agent_name, feedback, alert_queued, 0),
        )
        rid = cur.lastrowid
    db.commit()

    # send alert email for low ratings with feedback
    if update_last or (0 < stars < 5):
        alert_email = get_setting("alert_email", ALERT_EMAIL_DEFAULT)
        row = db.execute("SELECT * FROM responses WHERE id=?", (rid,)).fetchone()
        if row and row["alert_queued"] and not row["alert_sent"]:
            subject = f"New private feedback: {stars} stars"
            text = f"Stars: {stars}\nAgent: {agent_name}\nFeedback:\n{feedback}\n\nTime: {now}\nID: {rid}"
            sent = send_email(alert_email, subject, text)
            db.execute("UPDATE responses SET alert_sent=? WHERE id=?", (1 if sent else 0, rid))
            db.commit()

    return jsonify(ok=True, alertQueued=bool(0 < stars < 5))


@app.route("/dashboard/login", methods=["GET", "POST"])
def dash_login():
    if request.method == "GET":
        return DASH_LOGIN_HTML
    email = (request.form.get("email") or "").strip()
    if email.lower() != OWNER_EMAIL_DEFAULT.strip().lower():
        return "Not authorized for this dashboard.", 403
    token = serializer.dumps(email)
    # Build magic link
    base = request.host_url.rstrip("/")
    link = f"{base}/dashboard?token={token}"
    sent = send_email(email, "Your Review Funnel dashboard link", f"Click to sign in:\n\n{link}\n\nValid 12 hours.")
    if sent:
        return "Magic link sent! Check your email."
    else:
        # fallback when email not configured: show link (only for initial setup)
        app.logger.info(f"Magic link (email disabled): {link}")
        return f"Email not configured yet. Use this link to sign in:<br><a href='{link}'>{link}</a>"


@app.route("/dashboard")
def dashboard():
    # handle ?token= login
    token = request.args.get("token", "")
    if token:
        try:
            email = serializer.loads(token, max_age=60 * 60 * 12)
            if email.strip().lower() == OWNER_EMAIL_DEFAULT.strip().lower():
                resp = make_response(redirect("/dashboard"))
                resp.set_cookie("rf_auth", token, httponly=True, samesite="Lax", max_age=60 * 60 * 12)
                return resp
        except Exception:
            pass
    if not is_dashboard_authed():
        return redirect("/dashboard/login")
    db = get_db()
    rows = db.execute("SELECT * FROM responses ORDER BY id DESC LIMIT 200").fetchall()
    gurl = get_setting("google_review_url", GOOGLE_REVIEW_URL_DEFAULT)
    aemail = get_setting("alert_email", ALERT_EMAIL_DEFAULT)
    rows_html = "".join(
        f"<tr><td>{r['id']}</td><td>{r['created_at'][:19]}</td><td>{r['stars']}</td><td>{(r['agent_name'] or '')[:40]}</td><td>{(r['feedback'] or '')[:80]}</td><td>{'yes' if r['alert_queued'] else ''}</td></tr>"
        for r in rows
    )
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Review Funnel Dashboard</title><style>body{{font-family:sans-serif;background:#f7f7f5;padding:24px}}table{{border-collapse:collapse;width:100%;background:#fff}}td,th{{border:1px solid #ddd;padding:8px;font-size:13px}}.card{{background:#fff;padding:16px;border-radius:12px;margin-bottom:16px}}</style></head>
<body><h1>Review Funnel — Dashboard</h1>
<div class="card"><h3>Settings</h3>
<form method="post" action="/api/settings">
<label>Google review URL<br><input name="google_review_url" value="{gurl}" style="width:100%"/></label><br><br>
<label>Alert email<br><input name="alert_email" value="{aemail}" style="width:60%"/></label><br><br>
<button>Save</button></form></div>
<div class="card"><h3>Responses ({len(rows)})</h3>
<table><tr><th>ID</th><th>Time</th><th>Stars</th><th>Agent</th><th>Feedback</th><th>Alert?</th></tr>{rows_html}</table></div>
</body></html>"""


@app.route("/api/settings", methods=["POST"])
def api_settings():
    if not is_dashboard_authed():
        return jsonify(error="unauthorized"), 401
    data = request.get_json(silent=True) if request.is_json else request.form
    if data.get("google_review_url"):
        set_setting("google_review_url", data["google_review_url"][:500])
    if data.get("alert_email"):
        set_setting("alert_email", data["alert_email"][:200])
    if request.is_json:
        return jsonify(ok=True)
    return redirect("/dashboard")


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
