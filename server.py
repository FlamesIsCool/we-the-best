from flask import Flask, request, Response, jsonify, send_from_directory
from flask_cors import CORS
import secrets, hmac, hashlib, time, os, json, requests, sys

# ===============================
# SAFE ENV LOADING
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
# ANTI-BYPASS HELPERS
# ===============================
BLOCKED_AGENTS = [
    "curl", "wget", "python", "requests",
    "httpx", "aiohttp", "node",
    "powershell", "cmd", "postman"
]

def roblox_only(req):
    ua = (req.headers.get("User-Agent") or "").lower()

    if not ua.startswith("roblox"):
        return False

    for b in BLOCKED_AGENTS:
        if b in ua:
            return False

    return True

def reject_if_not_roblox(req):
    if not roblox_only(req):
        return Response("Access denied", 403)
    return None

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
# BASIC IP RATE LIMIT (KEY FARM)
# ===============================
KEY_RATE_LIMIT = {}
KEY_WINDOW = 60  # seconds

def allow_key_request(ip):
    now = time.time()
    last = KEY_RATE_LIMIT.get(ip, 0)
    if now - last < KEY_WINDOW:
        return False
    KEY_RATE_LIMIT[ip] = now
    return True

# ===============================
# LOOTLABS
# ===============================
def create_lootlabs_link(script_id):
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
# API: VERIFY KEY (ROBLOX ONLY)
# ===============================
@app.route("/api/verify_key", methods=["POST"])
def verify_key():
    deny = reject_if_not_roblox(request)
    if deny:
        return deny

    data = request.get_json(silent=True) or {}
    script_id = data.get("script_id")
    key = data.get("key")

    if not script_id or not key:
        return jsonify({"success": False})

    doc = db.collection("keys").document(script_id).get()
    if not doc.exists:
        return jsonify({"success": False})

    hashes = doc.to_dict().get("hashes", [])
    return jsonify({"success": hash_key(key) in hashes})

# ===============================
# KEY PAGE (LOOTLABS REDIRECT)
# ===============================
@app.route("/key/<script_id>")
def key_page(script_id):
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)

    if not allow_key_request(ip):
        return Response("Rate limited", 429)

    if not db.collection("scripts").document(script_id).get().exists:
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
    background:#0e0e0e;
    color:white;
    font-family:Arial,sans-serif;
    display:flex;
    align-items:center;
    justify-content:center;
    height:100vh;
    margin:0;
}}
.box {{
    background:#151515;
    padding:32px;
    border-radius:16px;
    width:440px;
    text-align:center;
}}
.key {{
    background:#0b0b0b;
    padding:14px;
    border-radius:10px;
    font-family:monospace;
    margin:16px 0;
    word-break:break-all;
}}
button {{
    background:#00aaff;
    border:none;
    padding:12px 18px;
    border-radius:10px;
    cursor:pointer;
    font-weight:600;
    margin:6px;
}}
.secondary {{
    background:#2a2a2a;
    color:white;
}}
</style>
<script>
function copy(t) {{
    navigator.clipboard.writeText(t);
    alert("Copied!");
}}
</script>
</head>
<body>
<div class="box">
<h1>✅ Key Generated</h1>
<div class="key">{raw_key}</div>
<button onclick="copy('{raw_key}')">Copy Key</button>
<button class="secondary" onclick="copy(`{loader}`)">Copy Loadstring</button>
<p>Return to Roblox and paste the key.</p>
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

    token = doc.to_dict()["token"]
    ts = str(int(time.time()))

    sig = hmac.new(
        SECRET_KEY,
        f"{script_id}{ts}".encode(),
        hashlib.sha256
    ).hexdigest()

    raw_url = f"https://luadec.net/raw/{script_id}?token={token}&ts={ts}&sig={sig}"

    lua = f'''
local HttpService = game:GetService("HttpService")
local player = game.Players.LocalPlayer
local SCRIPT_ID = "{script_id}"

local gui = Instance.new("ScreenGui", player.PlayerGui)
local f = Instance.new("Frame", gui)
f.Size = UDim2.fromScale(0.36,0.26)
f.Position = UDim2.fromScale(0.5,0.5)
f.AnchorPoint = Vector2.new(0.5,0.5)
f.BackgroundColor3 = Color3.fromRGB(15,15,15)

local box = Instance.new("TextBox", f)
box.Size = UDim2.fromScale(0.85,0.3)
box.Position = UDim2.fromScale(0.075,0.35)
box.PlaceholderText = "Enter Key"

local btn = Instance.new("TextButton", f)
btn.Size = UDim2.fromScale(0.4,0.25)
btn.Position = UDim2.fromScale(0.3,0.7)
btn.Text = "Verify"

btn.MouseButton1Click:Connect(function()
    local r = HttpService:PostAsync(
        "https://luadec.net/api/verify_key",
        HttpService:JSONEncode({{script_id=SCRIPT_ID,key=box.Text}}),
        Enum.HttpContentType.ApplicationJson
    )
    if HttpService:JSONDecode(r).success then
        gui:Destroy()
        loadstring(game:HttpGet("{raw_url}"))()
    else
        box.Text = "Invalid Key"
    end
end)
'''
    return Response(lua, mimetype="text/plain")

# ===============================
# RAW SCRIPT (LOCKED)
# ===============================
@app.route("/raw/<script_id>")
def raw(script_id):
    deny = reject_if_not_roblox(request)
    if deny:
        return deny

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
    print("LuaDec running on port", port)
    app.run(host="0.0.0.0", port=port)
