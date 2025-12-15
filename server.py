from flask import Flask, request, Response, jsonify
from flask_cors import CORS
import secrets
import hmac
import hashlib
import time
import os
import json

# ===============================
# FIREBASE SETUP (INLINE)
# ===============================
import firebase_admin
from firebase_admin import credentials, firestore

if not firebase_admin._apps:
    cred_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    if not cred_json:
        raise RuntimeError("FIREBASE_SERVICE_ACCOUNT is not set")

    cred = credentials.Certificate(json.loads(cred_json))
    firebase_admin.initialize_app(cred)

db = firestore.client()

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
# CONSTANTS
# ===============================
CHUNK_SIZE = 200_000  # ~200 KB per chunk (SAFE)

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
# DATABASE HELPERS (CHUNKING)
# ===============================
def save_script(script_id, script, token):
    chunks = [script[i:i+CHUNK_SIZE] for i in range(0, len(script), CHUNK_SIZE)]

    # metadata document
    db.collection("scripts").document(script_id).set({
        "chunk_count": len(chunks),
        "token": token,
        "created_at": int(time.time())
    })

    # chunk subcollection
    for index, chunk in enumerate(chunks):
        db.collection("scripts") \
          .document(script_id) \
          .collection("chunks") \
          .document(str(index)) \
          .set({"data": chunk})

def get_script(script_id):
    meta = db.collection("scripts").document(script_id).get()
    if not meta.exists:
        return None

    meta_data = meta.to_dict()
    chunk_count = meta_data["chunk_count"]
    token = meta_data["token"]

    parts = []
    for i in range(chunk_count):
        doc = db.collection("scripts") \
                .document(script_id) \
                .collection("chunks") \
                .document(str(i)) \
                .get()
        if not doc.exists:
            return None
        parts.append(doc.to_dict()["data"])

    return {
        "content": "".join(parts),
        "token": token
    }

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

    sig = hmac.new(SECRET_KEY, msg, hashlib.sha256).hexdigest()

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
    expected = hmac.new(SECRET_KEY, msg, hashlib.sha256).hexdigest()

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
    return "LuaDec backend running (Firestore chunking)"

# ===============================
# STARTUP
# ===============================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
