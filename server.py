from flask import Flask, request, Response, jsonify, send_from_directory
from flask_cors import CORS
import secrets, hmac, hashlib, time, os, json, requests

# ===============================
# FIREBASE
# ===============================
import firebase_admin
from firebase_admin import credentials, firestore

if not firebase_admin._apps:
    cred = credentials.Certificate(json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"]))
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
def gen_id(): return secrets.token_hex(4)
def gen_token(): return secrets.token_urlsafe(16)
def gen_key(): return secrets.token_urlsafe(12)
def hash_key(k): return hashlib.sha256(k.encode()).hexdigest()

def roblox_only(req):
    ua = (req.headers.get("User-Agent") or "").lower()
    return ua.startswith("roblox") and not any(
        x in ua for x in ["curl","python","requests","httpx","aiohttp"]
    )

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
            "title": f"Luadec Script {script_id}",
            "url": f"https://luadec.net/key/{script_id}",
            "tier_id": 2,
            "number_of_tasks": 3,
            "theme": 1
        }
    )
    r.raise_for_status()
    return r.json()["message"]["loot_url"]

# ===============================
# UPLOAD
# ===============================
@app.route("/api/upload", methods=["POST"])
def upload():
    data = request.get_json() or {}
    script = data.get("script")

    if not isinstance(script, str):
        return jsonify({"error":"Invalid script"}),400

    script_id = gen_id()
    token = gen_token()
    raw_key = gen_key()

    loot_url = create_lootlabs_link(script_id)

    db.collection("scripts").document(script_id).set({
        "script": script,
        "token": token,
        "key_hash": hash_key(raw_key),
        "loot_url": loot_url,
        "created": int(time.time())
    })

    return jsonify({
        "success": True,
        "script_id": script_id,
        "loader": f'loadstring(game:HttpGet("https://luadec.net/signed/{script_id}"))()',
        "lootlabs": loot_url
    })

# ===============================
# KEY VERIFY
# ===============================
@app.route("/api/verify_key", methods=["POST"])
def verify_key():
    d = request.get_json() or {}
    doc = db.collection("scripts").document(d.get("script_id","")).get()

    if not doc.exists:
        return jsonify({"success":False})

    if hash_key(d.get("key","")) != doc.to_dict()["key_hash"]:
        return jsonify({"success":False})

    return jsonify({"success":True})

# ===============================
# SIGNED
# ===============================
@app.route("/signed/<sid>")
def signed(sid):
    doc = db.collection("scripts").document(sid).get()
    if not doc.exists:
        return Response("Not found",404)

    ts = str(int(time.time()))
    sig = hmac.new(SECRET_KEY, f"{sid}{ts}".encode(), hashlib.sha256).hexdigest()

    raw = f"https://luadec.net/raw/{sid}?token={doc.to_dict()['token']}&ts={ts}&sig={sig}"

    lua = f'''
local h=game:GetService("HttpService")
local p=game.Players.LocalPlayer
local id="{sid}"

local g=Instance.new("ScreenGui",p.PlayerGui)
local f=Instance.new("Frame",g)
f.Size=UDim2.fromScale(.35,.25)
f.Position=UDim2.fromScale(.5,.5)
f.AnchorPoint=Vector2.new(.5,.5)
f.BackgroundColor3=Color3.fromRGB(15,15,15)

local b=Instance.new("TextBox",f)
b.Size=UDim2.fromScale(.85,.3)
b.Position=UDim2.fromScale(.075,.35)
b.PlaceholderText="Enter Key"
b.TextColor3=Color3.new(1,1,1)

local t=Instance.new("TextButton",f)
t.Size=UDim2.fromScale(.4,.25)
t.Position=UDim2.fromScale(.3,.7)
t.Text="Verify"

t.MouseButton1Click:Connect(function()
    local r=h:PostAsync(
        "https://luadec.net/api/verify_key",
        h:JSONEncode({script_id=id,key=b.Text}),
        Enum.HttpContentType.ApplicationJson
    )
    if h:JSONDecode(r).success then
        g:Destroy()
        loadstring(game:HttpGet("{raw}"))()
    else
        b.Text="Invalid Key"
    end
end)
'''

    return Response(lua,mimetype="text/plain")

# ===============================
# RAW
# ===============================
@app.route("/raw/<sid>")
def raw(sid):
    doc = db.collection("scripts").document(sid).get()
    if not doc.exists:
        return Response("Not found",404)

    d = doc.to_dict()
    if request.args.get("token") != d["token"]:
        return Response("Forbidden",403)

    if not roblox_only(request):
        return Response("Denied",403)

    return Response(d["script"],mimetype="text/plain")

# ===============================
# SITE
# ===============================
@app.route("/")
def site():
    if (request.headers.get("User-Agent") or "").lower().startswith("roblox"):
        return Response("Not found",404)
    return send_from_directory(".", "index.html")
