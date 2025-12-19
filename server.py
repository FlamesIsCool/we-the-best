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

SIGNED_TTL_SECONDS = 10

WORKINK_CREATE_URL = "https://dashboard.work.ink/_api/v1/link"
WORKINK_VALIDATE_URL = "https://work.ink/_api/v2/token/isValid/{}"

# ==========================================================
# ENV
# ==========================================================
SECRET_KEY = os.environ.get("LUADEC_SECRET_KEY")
WORKINK_API_KEY = os.environ.get("WORKINK_API_KEY")
FIREBASE_JSON = os.environ.get("FIREBASE_SERVICE_ACCOUNT")

if not SECRET_KEY:
    raise RuntimeError("LUADEC_SECRET_KEY missing")
if not WORKINK_API_KEY:
    raise RuntimeError("WORKINK_API_KEY missing")
if not FIREBASE_JSON:
    raise RuntimeError("FIREBASE_SERVICE_ACCOUNT missing")

SECRET_KEY = SECRET_KEY.encode()

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

def is_roblox_request(req):
    ua = (req.headers.get("User-Agent") or "").lower()
    blocked = ["curl", "wget", "python", "requests", "httpx", "aiohttp"]
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

    response = requests.post(
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

    data = response.json()

    if data.get("error"):
        raise RuntimeError(f"Work.ink error: {data}")

    return data["response"]["url"]

def validate_workink_token(token, user_ip):
    response = requests.get(
        WORKINK_VALIDATE_URL.format(token),
        timeout=10
    )

    data = response.json()

    if not data.get("valid"):
        return False

    info = data.get("info") or {}
    if info.get("byIp") != user_ip:
        return False

    return True

# ==========================================================
# STORAGE (KEEP FIELD NAME = script)
# ==========================================================
def save_script(script_id, script_text, internal_token, workink_url):
    db.collection(SCRIPTS_COLLECTION).document(script_id).set({
        "script": script_text,  # <-- DO NOT CHANGE THIS
        "token": internal_token,
        "workink": workink_url,
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
    internal_token = gen_internal_token()

    workink_url = create_workink_link(script_id)

    save_script(script_id, script_text, internal_token, workink_url)

    return jsonify({
        "success": True,
        "script_id": script_id,
        "loader": f"https://luadec.net/loader/{script_id}.lua",
        "workink": workink_url
    })

# ==========================================================
# WORK.INK CALLBACK (ISSUE KEY)
# ==========================================================
@app.route("/workink/consume")
def workink_consume():
    token = request.args.get("token")
    script_id = request.args.get("script_id")
    user_ip = request.remote_addr

    if not token or not script_id:
        return Response("Invalid request", 400)

    if not validate_workink_token(token, user_ip):
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
# API: VERIFY KEY
# ==========================================================
@app.route("/api/verify-key", methods=["POST"])
def verify_key():
    body = request.get_json(silent=True) or {}
    key = body.get("key")
    script_id = body.get("script_id")

    if not key or not script_id:
        return jsonify({"success": False}), 400

    doc = db.collection(KEYS_COLLECTION).document(key).get()
    if not doc.exists:
        return jsonify({"success": False}), 403

    data = doc.to_dict()
    if not data.get("active") or data.get("script_id") != script_id:
        return jsonify({"success": False}), 403

    return jsonify({
        "success": True,
        "loader": f"https://luadec.net/signed/{script_id}"
    })

# ==========================================================
# LUA LOADER
# ==========================================================
@app.route("/loader/<script_id>.lua")
def lua_loader(script_id):
    script = get_script(script_id)
    if not script:
        return Response("Not found", 404)

    lua = f'''
local SCRIPT_ID = "{script_id}"
local VERIFY_API = "https://luadec.net/api/verify-key"
local KEY_LINK = "{script["workink"]}"

local HttpService = game:GetService("HttpService")

local Fluent = loadstring(game:HttpGet(
"https://raw.githubusercontent.com/dawid-scripts/Fluent/master/main.lua"
))()

if not Fluent then error("Fluent failed to load") end

local Window = Fluent:CreateWindow({{
    Title = "Lume Key System",
    SubTitle = "Complete the link to get a key",
    Size = UDim2.fromOffset(420,240),
    Acrylic = true,
    Theme = "Dark"
}})

local Tab = Window:AddTab({{ Title = "Key", Icon = "key" }})

Tab:AddButton({{
    Title = "Copy Key Link",
    Callback = function()
        if setclipboard then setclipboard(KEY_LINK) end
    end
}})

local entered = ""

Tab:AddInput("KeyInput", {{
    Title = "Enter Key",
    Callback = function(v)
        entered = v
    end
}})

Tab:AddButton({{
    Title = "Verify & Load",
    Callback = function()
        local res = game:HttpPost(
            VERIFY_API,
            HttpService:JSONEncode({{
                key = entered,
                script_id = SCRIPT_ID
            }})
        )

        local data = HttpService:JSONDecode(res)

        if data.success then
            Window:Destroy()
            loadstring(game:HttpGet(data.loader))()
        end
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
# RAW (USES script FIELD — NO CHANGES)
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

    if abs(time.time() - ts) > SIGNED_TTL_SECONDS:
        return Response("Expired", 403)

    expected = hmac.new(
        SECRET_KEY,
        f"{script_id}{ts}".encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, sig):
        return Response("Forbidden", 403)

    if not is_roblox_request(request):
        return Response("Forbidden", 403)

    return Response(script["script"], mimetype="text/plain")

# ==========================================================
# START
# ==========================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
