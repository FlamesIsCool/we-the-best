from flask import Flask, request, Response, jsonify, send_from_directory
from flask_cors import CORS
import secrets
import hmac
import hashlib
import time
import os
import json
import requests

# ===============================
# FIREBASE SETUP
# ===============================
import firebase_admin
from firebase_admin import credentials, firestore

if not firebase_admin._apps:
    cred_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    if not cred_json:
        raise RuntimeError("FIREBASE_SERVICE_ACCOUNT not set")

    cred = credentials.Certificate(json.loads(cred_json))
    firebase_admin.initialize_app(cred)

db = firestore.client()

# ===============================
# APP SETUP
# ===============================
app = Flask(__name__)
CORS(app)

# ===============================
# ENV
# ===============================
SECRET_KEY = os.environ.get("LUADEC_SECRET_KEY")
LOOTLABS_API_KEY = os.environ.get("LOOTLABS_API_KEY")

if not SECRET_KEY:
    raise RuntimeError("LUADEC_SECRET_KEY not set")

if not LOOTLABS_API_KEY:
    raise RuntimeError("LOOTLABS_API_KEY not set")

SECRET_KEY = SECRET_KEY.encode()

# ===============================
# HELPERS
# ===============================
def gen_id():
    return secrets.token_hex(4)

def gen_token():
    return secrets.token_urlsafe(16)

def gen_key():
    return secrets.token_urlsafe(12)

def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()

def roblox_only(req):
    ua = (req.headers.get("User-Agent") or "").lower()
    blocked = ["curl", "python", "requests", "httpx", "aiohttp", "node"]
    if not ua.startswith("roblox"):
        return False
    return not any(b in ua for b in blocked)

# ===============================
# LOOTLABS (CONTENT LOCKER)
# ===============================
def create_lootlabs_link(script_id: str) -> str:
    r = requests.post(
        "https://creators.lootlabs.gg/api/public/content_locker",
        headers={
            "Authorization": f"Bearer {LOOTLABS_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "title": f"Luadec Script {script_id}",
            "url": f"https://luadec.net/key/{script_id}",
            "tier_id": 2,
            "number_of_tasks": 3,
            "theme": 1
        },
        timeout=10
    )

    r.raise_for_status()
    return r.json()["message"]["loot_url"]

# ===============================
# API: UPLOAD SCRIPT
# ===============================
@app.route("/api/upload", methods=["POST"])
def upload():
    data = request.get_json(silent=True) or {}
    script = data.get("script")

    if not isinstance(script, str):
        return jsonify({"error": "Invalid script"}), 400

    script_id = gen_id()
    token = gen_token()
    raw_key = gen_key()

    loot_url = create_lootlabs_link(script_id)

    db.collection("scripts").document(script_id).set({
        "script": script,
        "token": token,
        "key_hash": hash_key(raw_key),
        "loot_url": loot_url,
        "created_at": int(time.time())
    })

    loader = f'loadstring(game:HttpGet("https://luadec.net/signed/{script_id}"))()'

    return jsonify({
        "success": True,
        "script_id": script_id,
        "loader": loader,
        "lootlabs": loot_url
    })

# ===============================
# API: VERIFY KEY
# ===============================
@app.route("/api/verify_key", methods=["POST"])
def verify_key():
    data = request.get_json(silent=True) or {}
    script_id = data.get("script_id")
    key = data.get("key")

    if not script_id or not key:
        return jsonify({"success": False})

    doc = db.collection("scripts").document(script_id).get()
    if not doc.exists:
        return jsonify({"success": False})

    if hash_key(key) != doc.to_dict().get("key_hash"):
        return jsonify({"success": False})

    return jsonify({"success": True})

# ===============================
# SIGNED LOADER
# ===============================
@app.route("/signed/<script_id>")
def signed(script_id):
    doc = db.collection("scripts").document(script_id).get()
    if not doc.exists:
        return Response("Not found", 404)

    data = doc.to_dict()

    ts = str(int(time.time()))
    sig = hmac.new(
        SECRET_KEY,
        f"{script_id}{ts}".encode(),
        hashlib.sha256
    ).hexdigest()

    raw_url = (
        f"https://luadec.net/raw/{script_id}"
        f"?token={data['token']}&ts={ts}&sig={sig}"
    )

    lua = f'''
local HttpService = game:GetService("HttpService")
local Players = game:GetService("Players")
local player = Players.LocalPlayer
local SCRIPT_ID = "{script_id}"

local gui = Instance.new("ScreenGui")
gui.Name = "LuadecKeySystem"
gui.Parent = player.PlayerGui

local frame = Instance.new("Frame", gui)
frame.Size = UDim2.fromScale(0.35, 0.25)
frame.Position = UDim2.fromScale(0.5, 0.5)
frame.AnchorPoint = Vector2.new(0.5, 0.5)
frame.BackgroundColor3 = Color3.fromRGB(15, 15, 15)
frame.BorderSizePixel = 0

local box = Instance.new("TextBox", frame)
box.Size = UDim2.fromScale(0.85, 0.3)
box.Position = UDim2.fromScale(0.075, 0.35)
box.PlaceholderText = "Enter Key"
box.Text = ""
box.TextColor3 = Color3.new(1,1,1)
box.BackgroundColor3 = Color3.fromRGB(25,25,25)
box.BorderSizePixel = 0

local btn = Instance.new("TextButton", frame)
btn.Size = UDim2.fromScale(0.4, 0.25)
btn.Position = UDim2.fromScale(0.3, 0.7)
btn.Text = "Verify"
btn.TextColor3 = Color3.new(1,1,1)
btn.BackgroundColor3 = Color3.fromRGB(0,170,255)
btn.BorderSizePixel = 0

btn.MouseButton1Click:Connect(function()
    local res = HttpService:PostAsync(
        "https://luadec.net/api/verify_key",
        HttpService:JSONEncode({{script_id = SCRIPT_ID, key = box.Text}}),
        Enum.HttpContentType.ApplicationJson
    )

    local decoded = HttpService:JSONDecode(res)
    if decoded.success then
        gui:Destroy()
        loadstring(game:HttpGet("{raw_url}"))()
    else
        box.Text = "Invalid Key"
    end
end)
'''

    return Response(lua, mimetype="text/plain")

# ===============================
# RAW SCRIPT DELIVERY
# ===============================
@app.route("/raw/<script_id>")
def raw(script_id):
    doc = db.collection("scripts").document(script_id).get()
    if not doc.exists:
        return Response("Not found", 404)

    data = doc.to_dict()

    token = request.args.get("token")
    ts = request.args.get("ts")
    sig = request.args.get("sig")

    if not token or not ts or not sig:
        return Response("Forbidden", 403)

    if token != data["token"]:
        return Response("Forbidden", 403)

    try:
        if abs(time.time() - int(ts)) > 10:
            return Response("Expired", 403)
    except ValueError:
        return Response("Invalid timestamp", 403)

    expected = hmac.new(
        SECRET_KEY,
        f"{script_id}{ts}".encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, sig):
        return Response("Invalid signature", 403)

    if not roblox_only(request):
        return Response("Access denied", 403)

    return Response(data["script"], mimetype="text/plain")

# ===============================
# WEBSITE
# ===============================
@app.route("/")
def website():
    ua = (request.headers.get("User-Agent") or "").lower()
    if ua.startswith("roblox"):
        return Response("Not found", 404)
    return send_from_directory(".", "index.html")

# ===============================
# START
# ===============================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
