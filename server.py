# ==========================================================
# IMPORTS
# ==========================================================
from flask import Flask, request, Response, jsonify, redirect
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
SCRIPTS = "scripts"
KEYS = "keys"

WORKINK_CREATE_URL = "https://dashboard.work.ink/_api/v1/link"
WORKINK_VALIDATE_URL = "https://work.ink/_api/v2/token/isValid/{}"

SIGNED_TTL = 10

# ==========================================================
# FIREBASE
# ==========================================================
if not firebase_admin._apps:
    cred = credentials.Certificate(json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"]))
    firebase_admin.initialize_app(cred)

db = firestore.client()

# ==========================================================
# FLASK
# ==========================================================
app = Flask(__name__)
CORS(app)

# ==========================================================
# SECRETS
# ==========================================================
SECRET_KEY = os.environ["LUADEC_SECRET_KEY"].encode()
WORKINK_API_KEY = os.environ["WORKINK_API_KEY"]

# ==========================================================
# HELPERS
# ==========================================================
def gen_script_id():
    return secrets.token_hex(4)

def gen_key():
    return "LUME-" + secrets.token_hex(6).upper()

def gen_token():
    return secrets.token_urlsafe(16)

def roblox_only(req):
    ua = (req.headers.get("User-Agent") or "").lower()
    return ua.startswith("roblox")

# ==========================================================
# WORKINK
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

    try:
        data = response.json()
    except Exception:
        raise RuntimeError(f"Work.ink returned invalid JSON: {response.text}")

    # 🔴 Handle Work.ink error properly
    if response.status_code != 200 or data.get("error"):
        raise RuntimeError(
            f"Work.ink error ({response.status_code}): {data}"
        )

    # ✅ Success
    if "url" not in data:
        raise RuntimeError(f"Work.ink response missing url: {data}")

    return data["url"]


def validate_workink_token(token, user_ip):
    res = requests.get(WORKINK_VALIDATE_URL.format(token))
    data = res.json()

    if not data.get("valid"):
        return False

    if data["info"]["byIp"] != user_ip:
        return False

    return True

# ==========================================================
# SCRIPT STORAGE
# ==========================================================
def save_script(script_id, code, token, workink):
    db.collection(SCRIPTS).document(script_id).set({
        "code": code,
        "token": token,
        "workink": workink,
        "created": int(time.time())
    })

def get_script(script_id):
    doc = db.collection(SCRIPTS).document(script_id).get()
    return doc.to_dict() if doc.exists else None

# ==========================================================
# API: UPLOAD
# ==========================================================
@app.route("/api/upload", methods=["POST"])
def upload():
    body = request.get_json() or {}
    code = body.get("script")

    if not code:
        return jsonify({"error": "NO_SCRIPT"}), 400

    script_id = gen_script_id()
    access_token = gen_token()

    workink_url = create_workink_link(script_id)
    save_script(script_id, code, access_token, workink_url)

    return jsonify({
        "success": True,
        "script_id": script_id,
        "loader": f"https://luadec.net/loader/{script_id}.lua",
        "workink": workink_url
    })

# ==========================================================
# WORKINK CALLBACK
# ==========================================================
@app.route("/workink/consume")
def workink_consume():
    token = request.args.get("token")
    script_id = request.args.get("script_id")
    user_ip = request.remote_addr

    if not token or not script_id:
        return Response("Invalid", 400)

    if not validate_workink_token(token, user_ip):
        return Response("Invalid token", 403)

    key = gen_key()

    db.collection(KEYS).document(key).set({
        "active": True,
        "script_id": script_id,
        "created": int(time.time())
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
    body = request.get_json() or {}
    key = body.get("key")
    script_id = body.get("script_id")

    doc = db.collection(KEYS).document(key).get()
    if not doc.exists:
        return jsonify({"success": False}), 403

    if doc.to_dict().get("script_id") != script_id:
        return jsonify({"success": False}), 403

    return jsonify({
        "success": True,
        "loader": f"https://luadec.net/signed/{script_id}"
    })

# ==========================================================
# LOADER
# ==========================================================
@app.route("/loader/<script_id>.lua")
def loader(script_id):
    script = get_script(script_id)
    if not script:
        return Response("Not found", 404)

    lua = f'''
local SCRIPT_ID = "{script_id}"
local VERIFY = "https://luadec.net/api/verify-key"
local KEY_LINK = "{script['workink']}"

local HttpService = game:GetService("HttpService")

local Fluent = loadstring(game:HttpGet(
"https://github.com/dawid-scripts/Fluent/releases/latest/download/main.lua"
))()

local W = Fluent:CreateWindow({{
Title = "Lume Key System",
SubTitle = "Complete the link to get a key",
Size = UDim2.fromOffset(420,240),
Acrylic = true,
Theme = "Darker"
}})

local T = W:AddTab({{ Title = "Key", Icon = "key" }})

T:AddButton({{
Title = "Copy Key Link",
Callback = function()
if setclipboard then setclipboard(KEY_LINK) end
end
}})

local k = ""
T:AddInput("k", {{
Title = "Key",
Callback = function(v) k = v end
}})

T:AddButton({{
Title = "Verify",
Callback = function()
local r = game:HttpPost(
VERIFY,
HttpService:JSONEncode({{
key = k,
script_id = SCRIPT_ID
}})
)
local d = HttpService:JSONDecode(r)
if d.success then
W:Destroy()
loadstring(game:HttpGet(d.loader))()
end
end
}})
'''
    return Response(lua, mimetype="text/plain")

# ==========================================================
# SIGNED / RAW (UNCHANGED)
# ==========================================================
@app.route("/signed/<script_id>")
def signed(script_id):
    s = get_script(script_id)
    ts = str(int(time.time()))
    sig = hmac.new(SECRET_KEY, f"{script_id}{ts}".encode(), hashlib.sha256).hexdigest()

    url = f"https://luadec.net/raw/{script_id}?token={s['token']}&ts={ts}&sig={sig}"
    return Response(f'loadstring(game:HttpGet("{url}"))()', mimetype="text/plain")

@app.route("/raw/<script_id>")
def raw(script_id):
    s = get_script(script_id)
    if not roblox_only(request):
        return Response("Forbidden", 403)
    return Response(s["code"], mimetype="text/plain")

# ==========================================================
# START
# ==========================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
