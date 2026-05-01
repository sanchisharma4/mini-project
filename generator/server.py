"""
EntropyAuth — server.py
Place this file in the  generator/  folder.

Deploy on Render.com:
  Build command : pip install -r requirements.txt
  Start command : python server.py
  Start path    : generator/server.py   (or set root dir to generator/)
  Env variable  : ADMIN_API_KEY = miniproject825

Folder structure (everything lives in  generator/):
  generator/
    server.py          <- this file
    generate_code.py   <- run locally to snap photo + generate code
    config.json        <- user_key + chaos_seed (local use)
    users.json         <- auto-created by server
"""

import os, json, time, hmac, hashlib, threading, secrets
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ── Config ────────────────────────────────────────────────────────────────────
# BASE_DIR = the generator/ folder where this file lives.
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
USERS_FILE    = os.path.join(BASE_DIR, "users.json")
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "miniproject825")

# ── User store ────────────────────────────────────────────────────────────────
def load_users() -> dict:
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE) as f:
        return json.load(f)

def save_users(users: dict) -> None:
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)

# ── Entropy state ─────────────────────────────────────────────────────────────
# generate_code.py pushes the local photo hash here via POST /upload-hash.
# GET /hash returns it to the browser so both sides use the same value.
_latest_hash = {"value": None, "window": -1}
_hash_lock   = threading.Lock()

def _get_current_hash_and_window():
    with _hash_lock:
        h = _latest_hash["value"]
        w = _latest_hash["window"]
    if h is None:
        # Nothing uploaded yet — use time-based fallback so /hash always responds.
        # generate_code.py will overwrite this the moment it runs.
        w = int(time.time()) // 30
        h = hashlib.sha256(f"server_entropy_fallback:{w}".encode()).hexdigest()
    return h, w

# ── Code generation ───────────────────────────────────────────────────────────
def _generate_code(chaos_seed: str, frame_hash: str, window: int) -> str:
    """
    Mirrors generate_code.py generate_otp() and index.html makeCode() exactly:
        HMAC-SHA256(key=chaos_seed, msg="{frame_hash}:{window}")[:8].upper()
    """
    message = f"{frame_hash}:{window}".encode()
    return hmac.new(chaos_seed.encode(), message, hashlib.sha256).hexdigest()[:8].upper()

# ═════════════════════════════════════════════════════════════════════════════
#  ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return jsonify({"service": "EntropyAuth API", "status": "ok"})


# ── GET /hash ─────────────────────────────────────────────────────────────────
# Browser calls this every 30 s to get the current frame hash.
# Returns whatever generate_code.py last uploaded via /upload-hash.
@app.route("/hash")
def get_hash():
    h, w      = _get_current_hash_and_window()
    secs_left = 30 - (int(time.time()) % 30)
    return jsonify({"hash": h, "window": w, "seconds_left": secs_left})


# ── POST /upload-hash ─────────────────────────────────────────────────────────
# Called by generate_code.py after hashing the local photo.
# Stores the hash in memory so GET /hash returns it to the browser.
#
# Body:    { "hash": "<64-char sha256 hex>", "admin_key": "<key>" }
# Returns: { "ok": true, "hash": "...", "window": N }
@app.route("/upload-hash", methods=["POST"])
def upload_hash():
    data     = request.get_json(silent=True) or {}
    api_key  = data.get("admin_key", "")
    new_hash = data.get("hash", "").strip().lower()

    if not secrets.compare_digest(api_key, ADMIN_API_KEY):
        return jsonify({"error": "Invalid admin_key"}), 403

    if len(new_hash) != 64 or not all(c in "0123456789abcdef" for c in new_hash):
        return jsonify({"error": "hash must be a 64-character hex string (SHA-256)"}), 400

    win = int(time.time()) // 30
    with _hash_lock:
        _latest_hash["value"]  = new_hash
        _latest_hash["window"] = win

    print(f"[upload-hash] window={win}  hash={new_hash[:16]}...  (from local photo)")
    return jsonify({"ok": True, "hash": new_hash, "window": win})


# ── POST /register ────────────────────────────────────────────────────────────
# Called by browser (Google login) and by generate_code.py (first run).
# Assigns a deterministic chaos_seed to each uid.
#
# Body:    { "uid": "...", "email": "..." }
# Returns: { "chaos_seed": "...", "registered": true/false }
@app.route("/register", methods=["POST"])
def register():
    data  = request.get_json(silent=True) or {}
    uid   = data.get("uid", "").strip()
    email = data.get("email", "").strip()

    if not uid:
        return jsonify({"error": "uid required"}), 400

    users = load_users()

    if uid in users:
        return jsonify({
            "chaos_seed": users[uid]["chaos_seed"],
            "email":      users[uid].get("email", email),
            "registered": False,
            "message":    "Welcome back"
        })

    # New user: chaos_seed = HMAC(ADMIN_API_KEY, uid)[:32]
    chaos_seed = hmac.new(
        ADMIN_API_KEY.encode(), uid.encode(), hashlib.sha256
    ).hexdigest()[:32]

    users[uid] = {"email": email, "chaos_seed": chaos_seed, "created_at": int(time.time())}
    save_users(users)

    return jsonify({
        "chaos_seed": chaos_seed,
        "email":      email,
        "registered": True,
        "message":    "Registration successful"
    })


# ── POST /verify ──────────────────────────────────────────────────────────────
# Called by demo website to check a submitted OTP.
# Protected by ADMIN_API_KEY.
#
# Body:    { "api_key": "...", "uid": "...", "code": "ABCD1234" }
# Returns: { "valid": true/false, "email": "..." }
@app.route("/verify", methods=["POST"])
def verify():
    data    = request.get_json(silent=True) or {}
    api_key = data.get("api_key", "")
    uid     = data.get("uid", "").strip()
    code    = data.get("code", "").strip().upper()

    if not secrets.compare_digest(api_key, ADMIN_API_KEY):
        return jsonify({"error": "Invalid API key"}), 403
    if not uid or not code:
        return jsonify({"error": "uid and code required"}), 400

    users = load_users()
    if uid not in users:
        return jsonify({"valid": False, "error": "User not registered"}), 404

    chaos_seed = users[uid]["chaos_seed"]
    h, w       = _get_current_hash_and_window()

    # Allow ±1 window for clock-skew tolerance
    valid = False
    for offset in [0, -1, 1]:
        if secrets.compare_digest(code, _generate_code(chaos_seed, h, w + offset)):
            valid = True
            break

    return jsonify({"valid": valid, "email": users[uid].get("email", ""), "window": w})


# ── GET /user/<uid> ───────────────────────────────────────────────────────────
@app.route("/user/<uid>")
def get_user(uid):
    users = load_users()
    if uid not in users:
        return jsonify({"error": "Not found"}), 404
    u = users[uid]
    return jsonify({
        "email":             u.get("email"),
        "chaos_seed_prefix": u["chaos_seed"][:8] + "..." + u["chaos_seed"][-4:],
        "created_at":        u.get("created_at")
    })


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\nEntropyAuth server running on http://localhost:{port}")
    print(f"BASE_DIR  : {BASE_DIR}")
    print(f"USERS_FILE: {USERS_FILE}")
    print(f"Admin key : {ADMIN_API_KEY}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
