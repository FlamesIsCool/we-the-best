# ==========================================================
# IMPORTS
# ==========================================================
from flask import Flask, request, Response, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore
import secrets
import hmac
import hashlib
import time
import os
import json
import requests

# ==========================================================
# CONSTANTS
# ==========================================================
SCRIPTS_COLLECTION = "scripts"
KEYS_COLLECTION = "keys"

SIGNED_TTL = 10

WORKINK_CREATE_URL = "https://dashboard.work.ink/_api/v1/link"
WORKINK_VALIDATE_URL = "https://work.ink/_api/v2/token/isValid/{}"

# ==========================================================
# ENV
# ==========================================================
SECRET_KEY = os.environ["LUADEC_SECRET_KEY"].encode()
WORKINK_API_KEY = os.environ["WORKINK_API_KEY"]
FIREBASE_JSON = os.environ["FIREBASE_SERVICE_ACCOUNT"]

# ==========================================================
# FIREBASE
# ==========================================================
if not firebase_admin._apps:
    cred = credentials.Certificate(json.loads(FIREBASE_JSON))
    firebase_admin.initialize_app(cred)

db = firestore.client()

# ==========================================================
# FLASK
# ==========================================================
app = Flask(__name__)
CORS(app)

# ==========================================================
# HELPERS
# ==========================================================
def gen_script_id():
    return secrets.token_hex(4)

def gen_internal_token():
    return secrets.token_urlsafe(16)

def gen_key():
    return "LUME-" + secrets.token_hex(6).upper()

def is_roblox(req):
    ua = (req.headers.get("User-Agent") or "").lower()
    blocked = ["curl", "wget", "python", "requests", "httpx"]
    if any(b in ua for b in blocked):
        return False
    return ua.startswith("roblox")

# ==========================================================
# WORK.INK
# ==========================================================
def create_workink_link(script_id):
    destination = (
        f"https://luadec.net/workink/consume"
        f"?script_id={script_id}&token={{TOKEN}}"
    )

    r = requests.post(
        WORKINK_CREATE_URL,
        headers={
            "X-Api-Key": WORKINK_API_KEY,
            "Content-Type": "application/json"
        },
        json={
            "title": f"Lume Script {script_id}",
            "destination": destination,
            "link_description": "Lume protected script access"
        },
        timeout=10
    )

    data = r.json()
    if data.get("error"):
        raise RuntimeError(data)

    return data["response"]["url"]

def validate_workink_token(token, ip):
    r = requests.get(WORKINK_VALIDATE_URL.format(token), timeout=10)
    data = r.json()

    if not data.get("valid"):
        return False

    info = data.get("info") or {}
    return info.get("byIp") == ip

# ==========================================================
# STORAGE (UNCHANGED FIELD NAMES)
# ==========================================================
def save_script(script_id, script_text, token, workink):
    db.collection(SCRIPTS_COLLECTION).document(script_id).set({
        "script": script_text,   # KEEP THIS
        "token": token,
        "workink": workink,
        "created_at": int(time.time())
    })

def get_script(script_id):
    doc = db.collection(SCRIPTS_COLLECTION).document(script_id).get()
    return doc.to_dict() if doc.exists else None

# ==========================================================
# API: UPLOAD
# ==========================================================
@app.route("/api/upload", methods=["POST"])
def upload():
    body = request.get_json(silent=True) or {}
    script_text = body.get("script")

    if not script_text or not isinstance(script_text, str):
        return jsonify({"error": "INVALID_SCRIPT"}), 400

    script_id = gen_script_id()
    token = gen_internal_token()

    workink = create_workink_link(script_id)
    save_script(script_id, script_text, token, workink)

    return jsonify({
        "success": True,
        "script_id": script_id,
        "loader": f"https://luadec.net/loader/{script_id}.lua",
        "workink": workink
    })

# ==========================================================
# WORK.INK CALLBACK → ISSUE KEY
# ==========================================================
@app.route("/workink/consume")
def workink_consume():
    token = request.args.get("token")
    script_id = request.args.get("script_id")

    if not token or not script_id:
        return Response("Invalid request", 400)

    if not validate_workink_token(token, request.remote_addr):
        return Response("Invalid token", 403)

    key = gen_key()

    db.collection(KEYS_COLLECTION).document(key).set({
        "active": True,
        "script_id": script_id,
        "created_at": int(time.time())
    })

    return Response(
        f"Your key:\n\n{key}\n\nPaste this into the loader.",
        mimetype="text/plain"
    )

# ==========================================================
# VERIFY (GET-ONLY, EXECUTOR SAFE)
# ==========================================================
@app.route("/verify/<script_id>")
def verify(script_id):
    key = request.args.get("key")
    if not key:
        return Response("error('Missing key')", mimetype="text/plain")

    script = get_script(script_id)
    if not script:
        return Response("error('Script not found')", mimetype="text/plain")

    key_doc = db.collection(KEYS_COLLECTION).document(key).get()
    if not key_doc.exists:
        return Response("error('Invalid key')", mimetype="text/plain")

    data = key_doc.to_dict()
    if not data.get("active") or data.get("script_id") != script_id:
        return Response("error('Key not valid')", mimetype="text/plain")

    return Response(
        f'loadstring(game:HttpGet("https://luadec.net/signed/{script_id}"))()',
        mimetype="text/plain"
    )

# ==========================================================
# LOADER (NO POST, NO JSON)
# ==========================================================
@app.route("/loader/<script_id>.lua")
def loader(script_id):
    script = get_script(script_id)
    if not script:
        return Response("Not found", 404)

    lua = f'''
local SCRIPT_ID = "{script_id}"
local KEY_LINK = "{script["workink"]}"

local Fluent = loadstring(game:HttpGet(
"https://raw.githubusercontent.com/dawid-scripts/Fluent/master/main.lua"
))()

local Window = Fluent:CreateWindow({{
    Title = "Lume Key System",
    SubTitle = "Complete the link to get a key",
    Size = UDim2.fromOffset(420,240),
    Theme = "Dark"
}})

local Tab = Window:AddTab({{ Title = "Key", Icon = "key" }})

Tab:AddButton({{
    Title = "Copy Key Link",
    Callback = function()
        if setclipboard then setclipboard(KEY_LINK) end
    end
}})

local key = ""

Tab:AddInput("Key", {{
    Title = "Enter Key",
    Callback = function(v)
        key = v
    end
}})

Tab:AddButton({{
    Title = "Verify",
    Callback = function()
        local code = game:HttpGet(
            "https://luadec.net/verify/" .. SCRIPT_ID .. "?key=" .. key
        )
        loadstring(code)()
    end
}})
'''
    return Response(lua, mimetype="text/plain")

# ==========================================================
# SIGNED
# ==========================================================
@app.route("/signed/<script_id>")
def signed(script_id):
    script = get_script(script_id)
    if not script:
        return Response("Not found", 404)

    ts = str(int(time.time()))
    sig = hmac.new(
        SECRET_KEY,
        f"{script_id}{ts}".encode(),
        hashlib.sha256
    ).hexdigest()

    raw_url = (
        f"https://luadec.net/raw/{script_id}"
        f"?token={script['token']}&ts={ts}&sig={sig}"
    )

    return Response(
        f'loadstring(game:HttpGet("{raw_url}"))()',
        mimetype="text/plain"
    )

# ==========================================================
# RAW
# ==========================================================
@app.route("/raw/<script_id>")
def raw(script_id):
    script = get_script(script_id)
    if not script:
        return Response("Not found", 404)

    token = request.args.get("token")
    ts = request.args.get("ts")
    sig = request.args.get("sig")

    if not token or not ts or not sig:
        return Response("Forbidden", 403)

    if token != script["token"]:
        return Response("Forbidden", 403)

    try:
        ts = int(ts)
    except ValueError:
        return Response("Forbidden", 403)

    if abs(time.time() - ts) > SIGNED_TTL:
        return Response("Expired", 403)

    expected = hmac.new(
        SECRET_KEY,
        f"{script_id}{ts}".encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, sig):
        return Response("Forbidden", 403)

    if not is_roblox(request):
        return Response("Forbidden", 403)

    return Response(script["script"], mimetype="text/plain")

# ==========================================================
# START
# ==========================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
