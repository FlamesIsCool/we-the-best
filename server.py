from flask import Flask, request, Response, jsonify
from flask_cors import CORS
import os, json, time, hmac, hashlib, secrets, requests

# ============================================================
# ENV
# ============================================================
SECRET_KEY_RAW = os.environ.get("LUADEC_SECRET_KEY")
EXECUTOR_SECRET = os.environ.get("LUADEC_EXECUTOR_SECRET")
LOOTLABS_API_KEY = os.environ.get("LOOTLABS_API_KEY")
FIREBASE_JSON = os.environ.get("FIREBASE_SERVICE_ACCOUNT")

if not all([SECRET_KEY_RAW, EXECUTOR_SECRET, LOOTLABS_API_KEY, FIREBASE_JSON]):
    raise RuntimeError("Missing environment variables")

SECRET_KEY = SECRET_KEY_RAW.encode()
EXECUTOR_HEADER = "X-LuaDec-Client"

# ============================================================
# FIREBASE
# ============================================================
import firebase_admin
from firebase_admin import credentials, firestore

if not firebase_admin._apps:
    cred = credentials.Certificate(json.loads(FIREBASE_JSON))
    firebase_admin.initialize_app(cred)

db = firestore.client()

# ============================================================
# APP
# ============================================================
app = Flask(__name__)
CORS(app)

# ============================================================
# SECURITY
# ============================================================
def require_executor():
    hdr = request.headers.get(EXECUTOR_HEADER)
    return bool(hdr and hmac.compare_digest(hdr, EXECUTOR_SECRET))

# ============================================================
# HELPERS
# ============================================================
def gen_id():
    return secrets.token_hex(4)

def gen_token():
    return secrets.token_urlsafe(16)

def gen_key():
    return secrets.token_urlsafe(12)

def hash_key(k: str):
    return hashlib.sha256(k.encode()).hexdigest()

# ============================================================
# LOOTLABS
# ============================================================
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
    except:
        pass
    return None

# ============================================================
# UPLOAD SCRIPT
# ============================================================
@app.route("/api/upload", methods=["POST"])
def upload():
    data = request.get_json(silent=True) or {}
    script = data.get("script")

    if not isinstance(script, str):
        return jsonify({"error": "Invalid script"}), 400

    script_id = gen_id()
    token = gen_token()

    db.collection("scripts").document(script_id).set({
        "script": script,
        "token": token,
        "created": int(time.time())
    })

    loot = create_lootlabs_link(script_id)
    loader = f'loadstring(game:HttpGet("https://luadec.net/signed/{script_id}"))()'

    return jsonify({
        "success": True,
        "loader": loader,
        "lootlabs": loot
    })

# ============================================================
# KEY PAGE
# ============================================================
@app.route("/key/<script_id>")
def key_page(script_id):
    if not db.collection("scripts").document(script_id).get().exists:
        return "Invalid link", 404

    key = gen_key()
    key_hash = hash_key(key)

    ref = db.collection("keys").document(script_id)
    doc = ref.get()
    hashes = doc.to_dict().get("hashes", []) if doc.exists else []
    hashes.append(key_hash)

    ref.set({
        "hashes": hashes,
        "updated": int(time.time())
    })

    return f"""
<!DOCTYPE html>
<html>
<body style="background:#0f0f0f;color:white;font-family:Arial;
display:flex;align-items:center;justify-content:center;height:100vh;">
<div style="background:#151515;padding:30px;border-radius:14px;width:420px;text-align:center;">
<h2>Key Generated</h2>
<div style="background:#0b0b0b;padding:14px;border-radius:10px;
font-family:monospace;margin:16px 0;">{key}</div>
<button onclick="navigator.clipboard.writeText('{key}')"
style="padding:10px 16px;border:none;border-radius:8px;
background:#00aaff;font-weight:bold;">Copy Key</button>
<p>Paste this key into the executor UI</p>
</div>
</body>
</html>
"""

# ============================================================
# VERIFY KEY
# ============================================================
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

# ============================================================
# SIGNED LOADER (EXECUTOR SAFE)
# ============================================================
@app.route("/signed/<script_id>")
def signed(script_id):
    if not require_executor():
        return Response("Forbidden", 403)

    doc = db.collection("scripts").document(script_id).get()
    if not doc.exists:
        return "Not found", 404

    token = doc.to_dict()["token"]
    ts = str(int(time.time()))

    sig = hmac.new(
        SECRET_KEY,
        f"{script_id}{ts}".encode(),
        hashlib.sha256
    ).hexdigest()

    raw_url = f"https://luadec.net/raw/{script_id}?token={token}&ts={ts}&sig={sig}"

    lua = f'''
-- LuaDec Secure Loader

local HttpService = game:GetService("HttpService")

local http =
    request
    or http_request
    or (syn and syn.request)
    or (http and http.request)

if not http then
    return
end

local SECRET = "{EXECUTOR_SECRET}"

local function httpget(url)
    return http({{
        Url = url,
        Method = "GET",
        Headers = {{
            ["{EXECUTOR_HEADER}"] = SECRET
        }}
    }}).Body
end

local gui = Instance.new("ScreenGui", game.CoreGui)
gui.Name = "LuaDecKeyUI"

local frame = Instance.new("Frame", gui)
frame.Size = UDim2.fromScale(0.35,0.25)
frame.Position = UDim2.fromScale(0.325,0.375)
frame.BackgroundColor3 = Color3.fromRGB(20,20,20)
Instance.new("UICorner", frame).CornerRadius = UDim.new(0,14)

local box = Instance.new("TextBox", frame)
box.Size = UDim2.fromScale(0.9,0.3)
box.Position = UDim2.fromScale(0.05,0.35)
box.PlaceholderText = "Enter Key"
box.BackgroundColor3 = Color3.fromRGB(10,10,10)
box.TextColor3 = Color3.new(1,1,1)

local btn = Instance.new("TextButton", frame)
btn.Size = UDim2.fromScale(0.4,0.25)
btn.Position = UDim2.fromScale(0.3,0.7)
btn.Text = "Verify Key"
btn.BackgroundColor3 = Color3.fromRGB(0,170,255)

btn.MouseButton1Click:Connect(function()
    local r = http({{
        Url = "https://luadec.net/api/verify_key",
        Method = "POST",
        Headers = {{
            ["Content-Type"] = "application/json",
            ["{EXECUTOR_HEADER}"] = SECRET
        }},
        Body = HttpService:JSONEncode({{
            script_id = "{script_id}",
            key = box.Text
        }})
    }})

    local d = HttpService:JSONDecode(r.Body)
    if d.success then
        gui:Destroy()
        loadstring(httpget("{raw_url}"))()
    else
        btn.Text = "Invalid Key"
        task.wait(1)
        btn.Text = "Verify Key"
    end
end)
'''

    return Response(lua, mimetype="text/plain")

# ============================================================
# RAW SCRIPT (SIGNED + HEADER)
# ============================================================
@app.route("/raw/<script_id>")
def raw(script_id):
    if not require_executor():
        return Response("Forbidden", 403)

    doc = db.collection("scripts").document(script_id).get()
    if not doc.exists:
        return "Not found", 404

    d = doc.to_dict()
    token = request.args.get("token")
    ts = request.args.get("ts")
    sig = request.args.get("sig")

    if token != d["token"]:
        return "Forbidden", 403

    if abs(time.time() - int(ts)) > 10:
        return "Expired", 403

    expected = hmac.new(
        SECRET_KEY,
        f"{script_id}{ts}".encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, sig):
        return "Invalid", 403

    return Response(d["script"], mimetype="text/plain")

# ============================================================
# START
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print("LuaDec backend running on", port)
    app.run("0.0.0.0", port)
