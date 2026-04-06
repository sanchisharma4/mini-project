"""
EntropyAuth — Flask API Server
==============================
Place this file in your project root alongside entropy_image.py.

Folder structure expected:
  project/
  ├── server.py            ← this file
  ├── entropy_image.py
  ├── code_generator.py
  ├── noise_frames/        ← auto-created by entropy_image.py
  ├── actual_images/       ← auto-created by entropy_image.py
  └── users.json           ← auto-created by this server

Install deps:
  pip install flask flask-cors firebase-admin

Run locally:
  python server.py

Deploy on Render.com (free):
  1. Push project folder to a GitHub repo
  2. Go to render.com → New → Web Service → connect repo
  3. Build command : pip install -r requirements.txt
  4. Start command : python server.py
  5. Add environment variable  ADMIN_API_KEY = any-secret-string-you-choose
  6. Copy the public URL Render gives you → paste into app.html as SERVER_URL
"""

import os, json, time, hmac, hashlib, threading, secrets
from flask import Flask, request, jsonify
from flask_cors import CORS

# ── Optional: only needed if you want server-side Google token verification ──
# import firebase_admin
# from firebase_admin import credentials, auth as fb_auth
# cred = credentials.Certificate("serviceAccountKey.json")
# firebase_admin.initialize_app(cred)

app = Flask(__name__)
CORS(app)  # allow requests from your GitHub Pages / Netlify frontend

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
USERS_FILE     = os.path.join(BASE_DIR, "users.json")
ADMIN_API_KEY  = os.environ.get("ADMIN_API_KEY", "dev-secret-change-me")
# This key protects /verify so only YOUR demo website can call it.
# Set it as an env variable on Render; hardcode only for local dev.

# ── User store (JSON file — good enough for a mini project) ──────────────────
def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE) as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)

# ── Entropy background thread ─────────────────────────────────────────────────
# Runs entropy_image.py's generate function every 30 seconds in the background.
# The latest hash is stored in memory so /hash responds instantly.
_latest_hash   = {"value": None, "window": -1}
_hash_lock     = threading.Lock()

def _entropy_loop():
    """Background thread: generates a new entropy frame every 30-second window."""
    while True:
        try:
            from entropy_image import generate_entropy_image
            frame_hash, _, _ = generate_entropy_image()
            win = int(time.time()) // 30
            with _hash_lock:
                _latest_hash["value"]  = frame_hash
                _latest_hash["window"] = win
            print(f"[entropy] window={win} hash={frame_hash[:16]}…")
        except Exception as e:
            print(f"[entropy] Error: {e}")
            # If webcam not available on server, fall back to time-based hash
            win = int(time.time()) // 30
            fallback = hashlib.sha256(f"server_entropy_fallback:{win}".encode()).hexdigest()
            with _hash_lock:
                _latest_hash["value"]  = fallback
                _latest_hash["window"] = win

        # Sleep until the next 30-second boundary
        now = time.time()
        time.sleep(30 - (now % 30))

threading.Thread(target=_entropy_loop, daemon=True).start()

# ── Code generation (same logic as code_generator.py) ────────────────────────
def _generate_code(user_key: str, frame_hash: str, window: int) -> str:
    message  = f"{frame_hash}:{window}".encode()
    raw_hmac = hmac.new(user_key.encode(), message, hashlib.sha256).hexdigest()
    return raw_hmac[:8].upper()

def _get_current_hash_and_window():
    with _hash_lock:
        h = _latest_hash["value"]
        w = _latest_hash["window"]
    if h is None:
        # First request before loop fires — generate inline
        w = int(time.time()) // 30
        h = hashlib.sha256(f"server_entropy_fallback:{w}".encode()).hexdigest()
    return h, w

# ═════════════════════════════════════════════════════════════════════════════
#  ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════════

# ── Health check ──────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return jsonify({"service": "EntropyAuth API", "status": "ok"})


# ── GET /hash ─────────────────────────────────────────────────────────────────
# Called by app.html every 30 seconds to get the current entropy frame hash.
# app.html then computes the OTP client-side using HMAC (user key never leaves browser).
@app.route("/hash")
def get_hash():
    h, w = _get_current_hash_and_window()
    secs_left = 30 - (int(time.time()) % 30)
    return jsonify({
        "hash":        h,
        "window":      w,
        "seconds_left": secs_left
    })


# ── POST /register ────────────────────────────────────────────────────────────
# Called once when a user logs in for the first time via Google.
# Stores the user's Google uid → assigns them a chaos_seed.
# The chaos_seed is what links app.html ↔ demo website.
#
# Body: { "uid": "google-uid-string", "email": "user@gmail.com" }
# Returns: { "chaos_seed": "hex-string", "registered": true/false }
@app.route("/register", methods=["POST"])
def register():
    data  = request.get_json(silent=True) or {}
    uid   = data.get("uid", "").strip()
    email = data.get("email", "").strip()

    if not uid:
        return jsonify({"error": "uid required"}), 400

    users = load_users()

    if uid in users:
        # Already registered — return existing seed
        return jsonify({
            "chaos_seed":  users[uid]["chaos_seed"],
            "email":       users[uid].get("email", email),
            "registered":  False,   # was already registered
            "message":     "Welcome back"
        })

    # New user — generate a unique chaos_seed
    # chaos_seed = HMAC(uid, server_secret) so it's deterministic but private
    chaos_seed = hmac.new(
        ADMIN_API_KEY.encode(),
        uid.encode(),
        hashlib.sha256
    ).hexdigest()[:32]

    users[uid] = {
        "email":      email,
        "chaos_seed": chaos_seed,
        "created_at": int(time.time())
    }
    save_users(users)

    return jsonify({
        "chaos_seed": chaos_seed,
        "email":      email,
        "registered": True,
        "message":    "Registration successful"
    })


# ── POST /verify ──────────────────────────────────────────────────────────────
# Called by YOUR DEMO WEBSITE when a user submits their OTP.
# Protected by ADMIN_API_KEY so only your site can call it.
#
# Body:  { "api_key": "...", "uid": "google-uid", "code": "ABCD1234" }
# Returns: { "valid": true/false, "email": "..." }
@app.route("/verify", methods=["POST"])
def verify():
    data    = request.get_json(silent=True) or {}
    api_key = data.get("api_key", "")
    uid     = data.get("uid", "").strip()
    code    = data.get("code", "").strip().upper()

    # Protect endpoint
    if not secrets.compare_digest(api_key, ADMIN_API_KEY):
        return jsonify({"error": "Invalid API key"}), 403

    if not uid or not code:
        return jsonify({"error": "uid and code required"}), 400

    users = load_users()
    if uid not in users:
        return jsonify({"valid": False, "error": "User not registered"}), 404

    chaos_seed = users[uid]["chaos_seed"]
    h, w       = _get_current_hash_and_window()

    # Check current window AND previous window (clock-skew tolerance ±30s)
    valid = False
    for window_offset in [0, -1, 1]:
        expected = _generate_code(chaos_seed, h, w + window_offset)
        if secrets.compare_digest(code, expected):
            valid = True
            break

    return jsonify({
        "valid":  valid,
        "email":  users[uid].get("email", ""),
        "window": w
    })


# ── GET /user/<uid> ───────────────────────────────────────────────────────────
# Called by app.html to fetch the user's chaos_seed after login.
# Returns masked seed + registration info.
@app.route("/user/<uid>")
def get_user(uid):
    users = load_users()
    if uid not in users:
        return jsonify({"error": "Not found"}), 404
    u = users[uid]
    return jsonify({
        "email":            u.get("email"),
        "chaos_seed_prefix": u["chaos_seed"][:8] + "····" + u["chaos_seed"][-4:],
        "created_at":       u.get("created_at")
    })


# ── GET /annotated-frame ──────────────────────────────────────────────────────
# Serves the latest annotated entropy image as base64 so app.html can show it.
@app.route("/annotated-frame")
def annotated_frame():
    import base64, glob
    pattern = os.path.join(BASE_DIR, "actual_images", "annotated_*.png")
    files   = sorted(glob.glob(pattern), reverse=True)
    if not files:
        return jsonify({"error": "No frames yet"}), 404
    with open(files[0], "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return jsonify({"image": f"data:image/png;base64,{b64}", "file": os.path.basename(files[0])})


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\nEntropyAuth server running on http://localhost:{port}")
    print(f"Admin API key: {ADMIN_API_KEY}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
