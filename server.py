from flask import Flask, request, Response, jsonify
from flask_cors import CORS
import secrets, hmac, hashlib, time, os

app = Flask(__name__)

SECRET_KEY = b"dontdeletethisyouwillbreakscript"

CORS(
    app,
    resources={
        r"/api/*": {
            "origins": [
                "https://luadec.net",
                "https://api.luadec.net"
            ]
        }
    }
)

script_storage = {}

def generate_id():
    return secrets.token_hex(4)

def generate_token():
    return secrets.token_urlsafe(12)

def roblox_only(req):
    ua = (req.headers.get("User-Agent") or "").lower()

    blocked = [
        "curl", "wget", "python", "requests", "powershell",
        "fetch", "httpclient", "java", "node", "httpx", "aiohttp"
    ]

    if any(b in ua for b in blocked):
        return False

    return ua.startswith("roblox")

@app.route("/api/upload", methods=["POST"])
def upload():
    data = request.get_json(silent=True) or {}
    script = data.get("script")

    if not script:
        return jsonify({"error": "Missing script"}), 400

    script_id = generate_id()
    token = generate_token()

    script_storage[script_id] = {
        "content": script,
        "token": token
    }

    loader = f'loadstring(game:HttpGet("https://luadec.net/signed/{script_id}"))()'

    return jsonify({
        "success": True,
        "script_id": script_id,
        "loader": loader
    })

@app.route("/signed/<script_id>")
def signed(script_id):
    data = script_storage.get(script_id)
    if not data:
        return Response("Not found", status=404)

    ts = str(int(time.time()))
    msg = f"{script_id}{ts}".encode()
    sig = hmac.new(SECRET_KEY, msg, hashlib.sha256).hexdigest()

    raw_url = (
        f"https://luadec.net/raw/{script_id}"
        f"?token={data['token']}&ts={ts}&sig={sig}"
    )

    return Response(
        f'loadstring(game:HttpGet("{raw_url}"))()',
        mimetype="text/plain"
    )

@app.route("/raw/<script_id>")
def raw(script_id):
    data = script_storage.get(script_id)
    if not data:
        return Response("Not found", status=404)

    token = request.args.get("token")
    ts = request.args.get("ts")
    sig = request.args.get("sig")

    if not token or not ts or not sig:
        return Response("Missing fields", status=403)

    if token != data["token"]:
        return Response("Invalid token", status=403)

    try:
        ts_int = int(ts)
    except ValueError:
        return Response("Bad timestamp", status=403)

    if abs(time.time() - ts_int) > 10:
        return Response("Expired", status=403)

    msg = f"{script_id}{ts}".encode()
    expected = hmac.new(SECRET_KEY, msg, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, sig):
        return Response("Bad signature", status=403)

    if not roblox_only(request):
        return Response("Access denied", status=403)

    return Response(data["content"], mimetype="text/plain")

@app.route("/")
def index():
    return "LuaDec backend running"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5004))
    app.run(host="0.0.0.0", port=port)
