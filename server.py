from flask import Flask, request, Response, jsonify, send_from_directory
from flask_cors import CORS
import os, json, time, hmac, hashlib, secrets, requests

# ===============================
# ENV
# ===============================
SECRET_KEY_RAW = os.environ.get("LUADEC_SECRET_KEY")
LOOTLABS_API_KEY = os.environ.get("LOOTLABS_API_KEY")
FIREBASE_JSON = os.environ.get("FIREBASE_SERVICE_ACCOUNT")

if not SECRET_KEY_RAW:
    raise RuntimeError("LUADEC_SECRET_KEY missing")
if not LOOTLABS_API_KEY:
    raise RuntimeError("LOOTLABS_API_KEY missing")
if not FIREBASE_JSON:
    raise RuntimeError("FIREBASE_SERVICE_ACCOUNT missing")

SECRET_KEY = SECRET_KEY_RAW.encode()

# ===============================
# FIREBASE
# ===============================
import firebase_admin
from firebase_admin import credentials, firestore

cred = credentials.Certificate(json.loads(FIREBASE_JSON))
firebase_admin.initialize_app(cred)
db = firestore.client()

# ===============================
# APP
# ===============================
app = Flask(__name__)
CORS(app)

# ===============================
# HELPERS
# ===============================
def gen_id():
    return secrets.token_hex(4)

def gen_token():
    return secrets.token_urlsafe(16)

def gen_key():
    return secrets.token_urlsafe(12)

def hash_key(k: str):
    return hashlib.sha256(k.encode()).hexdigest()

# ===============================
# LOOTLABS
# ===============================
def create_lootlabs_link(script_id):
    r = requests.post(
        "https://creators.lootlabs.gg/api/public/content_locker",
        headers={
            "Authorization": f"Bearer {LOOTLABS_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "title": f"LuaDec Script {script_id}",
            "url": f"https://luadec.net/key/{script_id}",
            "tier_id": 2,
            "number_of_tasks": 3,
            "theme": 1
        },
        timeout=10
    )

    data = r.json()
    msg = data.get("message")

    if isinstance(msg, dict):
        return msg.get("loot_url")
    if isinstance(msg, list) and msg:
        return msg[0].get("loot_url")

    return None

# ===============================
# API: UPLOAD SCRIPT
# ===============================
@app.route("/api/upload", methods=["POST"])
def upload():
    payload = request.get_json(silent=True) or {}
    script = payload.get("script")

    if not isinstance(script, str):
        return jsonify({"error": "Invalid script"}), 400

    script_id = gen_id()
    token = gen_token()

    loot_url = create_lootlabs_link(script_id)

    db.collection("scripts").document(script_id).set({
        "script": script,
        "token": token,
        "created_at": int(time.time())
    })

    loader = f'loadstring(game:HttpGet("https://luadec.net/signed/{script_id}"))()'

    return jsonify({
        "success": True,
        "loader": loader,
        "lootlabs": loot_url
    })

# ===============================
# KEY PAGE (GENERATES KEY)
# ===============================
@app.route("/key/<script_id>")
def key_page(script_id):
    script_doc = db.collection("scripts").document(script_id).get()
    if not script_doc.exists:
        return Response("Invalid link", 404)

    raw_key = gen_key()
    key_hash = hash_key(raw_key)

    ref = db.collection("keys").document(script_id)
    doc = ref.get()
    hashes = doc.to_dict().get("hashes", []) if doc.exists else []

    hashes.append(key_hash)

    ref.set({
        "hashes": hashes,
        "updated_at": int(time.time())
    })

    loader = f'loadstring(game:HttpGet("https://luadec.net/signed/{script_id}"))()'

    return f"""
<!DOCTYPE html>
<html>
<head>
<title>LuaDec Key</title>
<style>
body {{
    background:#0f0f0f;
    color:white;
    font-family:Arial;
    display:flex;
    align-items:center;
    justify-content:center;
    height:100vh;
}}
.box {{
    background:#151515;
    padding:32px;
    border-radius:14px;
    width:420px;
    text-align:center;
}}
.key {{
    background:#0b0b0b;
    padding:14px;
    border-radius:10px;
    font-family:monospace;
    margin:16px 0;
}}
button {{
    padding:10px 16px;
    border:none;
    border-radius:8px;
    background:#00aaff;
    font-weight:bold;
    cursor:pointer;
    margin:6px;
}}
</style>
<script>
function copy(v) {{
    navigator.clipboard.writeText(v);
    alert("Copied");
}}
</script>
</head>
<body>
<div class="box">
<h2>Key Generated</h2>
<div class="key">{raw_key}</div>
<button onclick="copy('{raw_key}')">Copy Key</button>
<button onclick="copy(`{loader}`)">Copy Loader</button>
<p>Return to the app and paste your key.</p>
</div>
</body>
</html>
"""

# ===============================
# VERIFY KEY
# ===============================
@app.route("/api/verify_key", methods=["POST"])
def verify_key():
    data = request.get_json(silent=True) or {}
    script_id = data.get("script_id")
    key = data.get("key")

    if not script_id or not key:
        return jsonify({"success": False})

    doc = db.collection("keys").document(script_id).get()
    if not doc.exists:
        return jsonify({"success": False})

    valid = hash_key(key) in doc.to_dict().get("hashes", [])
    return jsonify({"success": valid})

# ===============================
# SIGNED LOADER
# ===============================
@app.route("/signed/<script_id>")
def signed(script_id):
    doc = db.collection("scripts").document(script_id).get()
    if not doc.exists:
        return Response("Not found", 404)

    token = doc.to_dict()["token"]
    ts = str(int(time.time()))

    sig = hmac.new(
        SECRET_KEY,
        f"{script_id}{ts}".encode(),
        hashlib.sha256
    ).hexdigest()

    raw_url = f"https://luadec.net/raw/{script_id}?token={token}&ts={ts}&sig={sig}"

    return Response(
        f'loadstring(game:HttpGet("{raw_url}"))()',
        mimetype="text/plain"
    )

# ===============================
# RAW SCRIPT (SIGNED ONLY)
# ===============================
@app.route("/raw/<script_id>")
def raw(script_id):
    doc = db.collection("scripts").document(script_id).get()
    if not doc.exists:
        return Response("Not found", 404)

    d = doc.to_dict()
    token = request.args.get("token")
    ts = request.args.get("ts")
    sig = request.args.get("sig")

    if token != d["token"]:
        return Response("Forbidden", 403)

    if abs(time.time() - int(ts)) > 10:
        return Response("Expired", 403)

    expected = hmac.new(
        SECRET_KEY,
        f"{script_id}{ts}".encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, sig):
        return Response("Invalid signature", 403)

    return Response(d["script"], mimetype="text/plain")

# ===============================
# ROOT
# ===============================
@app.route("/")
def home():
    return send_from_directory(".", "index.html")

# ===============================
# START
# ===============================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print("LuaDec backend running on", port)
    app.run(host="0.0.0.0", port=port)
