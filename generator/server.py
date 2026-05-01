"""
EntropyAuth — server.py  (FIXED)
Place this file in the  generator/  folder.

Deploy on Render.com:
  Build command : pip install -r requirements.txt
  Start command : python server.py
  Env variable  : ADMIN_API_KEY = miniproject825

KEY FIXES vs original:
  1. /admin/codes endpoint — returns every registered user's current code
     so generate_code.py --watch can show all users in terminal
  2. /admin/codes also embeds uid so terminal can highlight "you"
  3. users.json now stores email reliably on every register call
"""

import os, json, time, hmac, hashlib, threading, secrets
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ── Config ────────────────────────────────────────────────────────────────────
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
# GET /hash returns it to the browser so BOTH sides use the exact same value.
_latest_hash = {"value": None, "window": -1}
_hash_lock   = threading.Lock()

def _get_current_hash_and_window():
    with _hash_lock:
        h = _latest_hash["value"]
        w = _latest_hash["window"]
    if h is None:
        # Nothing uploaded yet — return None so browser knows no image clicked yet
        return None, int(time.time()) // 30
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
# Browser polls this every 30s.
# Returns null hash if no image has been uploaded yet (forces browser to wait).
@app.route("/hash")
def get_hash():
    h, w      = _get_current_hash_and_window()
    secs_left = 30 - (int(time.time()) % 30)
    return jsonify({
        "hash":       h,          # null if no image clicked yet
        "window":     w,
        "seconds_left": secs_left,
        "ready":      h is not None   # browser checks this
    })


# ── POST /upload-hash ─────────────────────────────────────────────────────────
# Called by generate_code.py after the user clicks/captures an image.
# Stores the hash so GET /hash returns it to the browser.
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

    # Log to terminal with all user codes so you can see them immediately
    users = load_users()
    print(f"\n[upload-hash] window={win}  hash={new_hash[:16]}…")
    print(f"{'─'*60}")
    print(f"  {'EMAIL':<35}  CODE")
    for uid, u in users.items():
        code = _generate_code(u["chaos_seed"], new_hash, win)
        print(f"  {u.get('email', uid):<35}  {code}")
    print(f"{'─'*60}\n")

    return jsonify({"ok": True, "hash": new_hash, "window": win})


# ── POST /register ────────────────────────────────────────────────────────────
# Called by browser (Google login) and generate_code.py.
# uid MUST be the Google 'sub' (numeric string) — same in both places.
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
        # Update email if it comes in fresh (browser may supply real email)
        if email and email != f"{uid}@local":
            users[uid]["email"] = email
            save_users(users)
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

    users[uid] = {
        "email":      email or f"{uid[:8]}@unknown",
        "chaos_seed": chaos_seed,
        "created_at": int(time.time())
    }
    save_users(users)
    print(f"[register] New user: {email or uid}  seed={chaos_seed[:8]}…")

    return jsonify({
        "chaos_seed": chaos_seed,
        "email":      email,
        "registered": True,
        "message":    "Registration successful"
    })


# ── POST /verify ──────────────────────────────────────────────────────────────
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

    if h is None:
        return jsonify({"valid": False, "error": "No image uploaded yet"}), 400

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


# ── GET /admin/codes  ─────────────────────────────────────────────────────────
# NEW ENDPOINT — lets generate_code.py --watch show ALL users' current codes.
# Protected by admin_key query param.
#
# GET /admin/codes?admin_key=miniproject825
# Returns: { "users": [ {"uid":..., "email":..., "code":..., "window":...} ] }
@app.route("/admin/codes")
def admin_codes():
    api_key = request.args.get("admin_key", "")
    if not secrets.compare_digest(api_key, ADMIN_API_KEY):
        return jsonify({"error": "Invalid admin_key"}), 403

    h, w = _get_current_hash_and_window()
    users = load_users()

    result = []
    for uid, u in users.items():
        if h is not None:
            code = _generate_code(u["chaos_seed"], h, w)
        else:
            code = "--------"   # no image uploaded yet
        result.append({
            "uid":    uid,
            "email":  u.get("email", uid),
            "code":   code,
            "window": w,
            "hash_ready": h is not None
        })

    return jsonify({"users": result, "window": w, "hash_ready": h is not None})


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\nEntropyAuth server running on http://localhost:{port}")
    print(f"BASE_DIR  : {BASE_DIR}")
    print(f"USERS_FILE: {USERS_FILE}")
    print(f"Admin key : {ADMIN_API_KEY}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
