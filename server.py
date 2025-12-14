from flask import Flask, request, Response, jsonify
from flask_cors import CORS
import secrets
import hmac
import hashlib
import time
import os
import sqlite3

# ===============================
# APP SETUP
# ===============================
app = Flask(__name__)

CORS(
    app,
    resources={
        r"/api/*": {
            "origins": [
                "https://luadec.net",
                "https://api.luadec.net"
            ]
        }
    }
)

# ===============================
# SECURITY
# ===============================
SECRET_KEY = os.environ.get("LUADEC_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("LUADEC_SECRET_KEY is not set")

SECRET_KEY = SECRET_KEY.encode()

# ===============================
# DATABASE
# ===============================
DB_PATH = "scripts.db"

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scripts (
                id TEXT PRIMARY KEY,
                script TEXT NOT NULL,
                token TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)

def save_script(script_id, script, token):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO scripts (id, script, token, created_at) VALUES (?, ?, ?, ?)",
            (script_id, script, token, int(time.time()))
        )

def get_script(script_id):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT script, token FROM scripts WHERE id = ?",
            (script_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "content": row[0],
            "token": row[1]
        }

# ===============================
# HELPERS
# ===============================
def generate_id():
    return secrets.token_hex(4)

def generate_token():
    return secrets.token_urlsafe(16)

def roblox_only(req):
    ua = (req.headers.get("User-Agent") or "").lower()

    blocked = [
        "curl", "wget", "python", "requests",
        "powershell", "httpclient", "java",
        "node", "httpx", "aiohttp"
    ]

    if any(b in ua for b in blocked):
        return False

    return ua.startswith("roblox")

# ===============================
# API: UPLOAD SCRIPT
# ===============================
@app.route("/api/upload", methods=["POST"])
def upload():
    data = request.get_json(silent=True) or {}
    script = data.get("script")

    if not script or not isinstance(script, str):
        return jsonify({"error": "Invalid script"}), 400

    script_id = generate_id()
    token = generate_token()

    save_script(script_id, script, token)

    loader = f'loadstring(game:HttpGet("https://luadec.net/signed/{script_id}"))()'

    return jsonify({
        "success": True,
        "script_id": script_id,
        "loader": loader
    })

# ===============================
# SIGNED LOADER
# ===============================
@app.route("/signed/<script_id>")
def signed(script_id):
    data = get_script(script_id)
    if not data:
        return Response("Not found", status=404)

    ts = str(int(time.time()))
    msg = f"{script_id}{ts}".encode()

    sig = hmac.new(
        SECRET_KEY,
        msg,
        hashlib.sha256
    ).hexdigest()

    raw_url = (
        f"https://luadec.net/raw/{script_id}"
        f"?token={data['token']}&ts={ts}&sig={sig}"
    )

    return Response(
        f'loadstring(game:HttpGet("{raw_url}"))()',
        mimetype="text/plain"
    )

# ===============================
# RAW SCRIPT DELIVERY
# ===============================
@app.route("/raw/<script_id>")
def raw(script_id):
    data = get_script(script_id)
    if not data:
        return Response("Not found", status=404)

    token = request.args.get("token")
    ts = request.args.get("ts")
    sig = request.args.get("sig")

    if not token or not ts or not sig:
        return Response("Missing parameters", status=403)

    if token != data["token"]:
        return Response("Invalid token", status=403)

    try:
        ts_int = int(ts)
    except ValueError:
        return Response("Invalid timestamp", status=403)

    if abs(time.time() - ts_int) > 10:
        return Response("Expired request", status=403)

    msg = f"{script_id}{ts}".encode()
    expected = hmac.new(
        SECRET_KEY,
        msg,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, sig):
        return Response("Invalid signature", status=403)

    if not roblox_only(request):
        return Response("Access denied", status=403)

    return Response(
        data["content"],
        mimetype="text/plain"
    )

# ===============================
# HEALTH CHECK
# ===============================
@app.route("/")
def index():
    return "LuaDec backend running"

# ===============================
# STARTUP
# ===============================
if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
