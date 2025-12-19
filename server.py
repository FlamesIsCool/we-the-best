from flask import Flask, request, Response, jsonify, send_from_directory
from flask_cors import CORS
import secrets, hmac, hashlib, time, os, json, requests

# ===============================
# FIREBASE
# ===============================
import firebase_admin
from firebase_admin import credentials, firestore

if not firebase_admin._apps:
    cred = credentials.Certificate(
        json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"])
    )
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
SECRET_KEY = os.environ["LUADEC_SECRET_KEY"].encode()
LOOTLABS_API_KEY = os.environ["LOOTLABS_API_KEY"]

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

def roblox_only(req):
    ua = (req.headers.get("User-Agent") or "").lower()
    return ua.startswith("roblox")

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

    doc = db.collection("keys").document(script_id).get()
    if not doc.exists:
        return jsonify({"success": False})

    hashes = doc.to_dict().get("hashes", [])
    if hash_key(key) not in hashes:
        return jsonify({"success": False})

    return jsonify({"success": True})

# ===============================
# KEY PAGE (GENERATES KEY PER USER)
# ===============================
@app.route("/key/<script_id>")
def key_page(script_id):
    script_doc = db.collection("scripts").document(script_id).get()
    if not script_doc.exists:
        return Response("Invalid link", 404)

    # generate a new key per visit
    raw_key = gen_key()
    key_hash = hash_key(raw_key)

    key_ref = db.collection("keys").document(script_id)
    key_doc = key_ref.get()

    if key_doc.exists:
        hashes = key_doc.to_dict().get("hashes", [])
    else:
        hashes = []

    hashes.append(key_hash)

    key_ref.set({
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
    font-family:Inter,Arial,sans-serif;
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
    box-shadow:0 20px 60px rgba(0,0,0,.6);
}}
h1 {{
    margin-bottom:10px;
}}
.key {{
    background:#0b0b0b;
    padding:14px;
    border-radius:10px;
    font-family:monospace;
    word-break:break-all;
    margin:16px 0;
}}
button {{
    background:#00aaff;
    color:black;
    border:none;
    padding:12px 18px;
    border-radius:10px;
    cursor:pointer;
    font-weight:600;
    margin:6px;
}}
button.secondary {{
    background:#2a2a2a;
    color:white;
}}
button:hover {{
    opacity:.85;
}}
.small {{
    opacity:.75;
    font-size:13px;
}}
</style>
<script>
function copy(text) {{
    navigator.clipboard.writeText(text);
    alert("Copied!");
}}
</script>
</head>
<body>
<div class="box">
<h1>✅ Key Generated</h1>
<p class="small">Copy your key and paste it into Roblox.</p>

<div class="key" id="key">{raw_key}</div>

<button onclick="copy('{raw_key}')">Copy Key</button>
<button class="secondary" onclick="copy(`{loader}`)">Copy Loadstring</button>

<p class="small" style="margin-top:16px;">
You may now return to Roblox.
</p>
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
# RAW SCRIPT
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

    expected = hmac.new(
        SECRET_KEY,
        f"{script_id}{ts}".encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, sig):
        return Response("Invalid signature", 403)

    if not roblox_only(request):
        return Response("Denied", 403)

    return Response(d["script"], mimetype="text/plain")

# ===============================
# ROOT
# ===============================
@app.route("/")
def home():
    return send_from_directory(".", "index.html")
