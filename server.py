from flask import Flask, request, Response, jsonify, send_from_directory
from flask_cors import CORS
import secrets, hmac, hashlib, time, os, json, requests

# ===============================
# FIREBASE
# ===============================
import firebase_admin
from firebase_admin import credentials, firestore

if not firebase_admin._apps:
    cred_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    if not cred_json:
        raise RuntimeError("FIREBASE_SERVICE_ACCOUNT missing")

    cred = credentials.Certificate(json.loads(cred_json))
    firebase_admin.initialize_app(cred)

db = firestore.client()

# ===============================
# APP
# ===============================
app = Flask(__name__)
CORS(app)

# ===============================
# ENV
# ===============================
SECRET_KEY = os.environ.get("LUADEC_SECRET_KEY")
LOOTLABS_API_KEY = os.environ.get("LOOTLABS_API_KEY")

if not SECRET_KEY:
    raise RuntimeError("LUADEC_SECRET_KEY missing")
if not LOOTLABS_API_KEY:
    raise RuntimeError("LOOTLABS_API_KEY missing")

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

def hash_key(key: str):
    return hashlib.sha256(key.encode()).hexdigest()

def roblox_only(req):
    ua = (req.headers.get("User-Agent") or "").lower()
    if not ua.startswith("roblox"):
        return False
    blocked = ["curl", "python", "requests", "httpx", "aiohttp", "node"]
    return not any(b in ua for b in blocked)

# ===============================
# LOOTLABS (ROBUST)
# ===============================
def create_lootlabs_link(script_id: str):
    try:
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
        print("LootLabs:", data)

        msg = data.get("message")

        if isinstance(msg, dict):
            return msg.get("loot_url")

        if isinstance(msg, list) and msg:
            return msg[0].get("loot_url")

    except Exception as e:
        print("LootLabs error:", e)

    return None

# ===============================
# API: UPLOAD
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
        "lootlabs": loot_url,
        "key": raw_key
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
# KEY LANDING PAGE (OPTION 1)
# ===============================
@app.route("/key/<script_id>")
def key_page(script_id):
    doc = db.collection("scripts").document(script_id).get()
    if not doc.exists:
        return Response("Invalid key link.", status=404)

    return """
<!DOCTYPE html>
<html>
<head>
<title>LuaDec Key</title>
<style>
body {
    background:#0f0f0f;
    color:white;
    font-family:Arial,sans-serif;
    display:flex;
    align-items:center;
    justify-content:center;
    height:100vh;
    margin:0;
}
.box {
    background:#151515;
    padding:32px;
    border-radius:14px;
    width:420px;
    text-align:center;
}
p { opacity:.85; }
</style>
</head>
<body>
<div class="box">
<h1>✅ Key Unlocked</h1>
<p>Your key has been unlocked successfully.</p>
<p>Return to Roblox and paste the key into the LuaDec window.</p>
<p>You may now close this page.</p>
</div>
</body>
</html>
"""

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

local gui = Instance.new("ScreenGui", player.PlayerGui)
gui.Name = "LuaDecKeySystem"

local frame = Instance.new("Frame", gui)
frame.Size = UDim2.fromScale(0.35, 0.25)
frame.Position = UDim2.fromScale(0.5, 0.5)
frame.AnchorPoint = Vector2.new(0.5, 0.5)
frame.BackgroundColor3 = Color3.fromRGB(15,15,15)

local box = Instance.new("TextBox", frame)
box.Size = UDim2.fromScale(0.85, 0.3)
box.Position = UDim2.fromScale(0.075, 0.35)
box.PlaceholderText = "Enter Key"
box.TextColor3 = Color3.new(1,1,1)
box.BackgroundColor3 = Color3.fromRGB(25,25,25)

local btn = Instance.new("TextButton", frame)
btn.Size = UDim2.fromScale(0.4, 0.25)
btn.Position = UDim2.fromScale(0.3, 0.7)
btn.Text = "Verify"
btn.BackgroundColor3 = Color3.fromRGB(0,170,255)
btn.TextColor3 = Color3.new(1,1,1)

btn.MouseButton1Click:Connect(function()
    local res = HttpService:PostAsync(
        "https://luadec.net/api/verify_key",
        HttpService:JSONEncode({{script_id = SCRIPT_ID, key = box.Text}}),
        Enum.HttpContentType.ApplicationJson
    )

    if HttpService:JSONDecode(res).success then
        gui:Destroy()
        loadstring(game:HttpGet("{raw_url}"))()
    else
        box.Text = "Invalid Key"
    end
end)
'''

    return Response(lua, mimetype="text/plain")

# ===============================
# RAW SCRIPT
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

    if token != data["token"]:
        return Response("Forbidden", 403)

    try:
        if abs(time.time() - int(ts)) > 10:
            return Response("Expired", 403)
    except:
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
