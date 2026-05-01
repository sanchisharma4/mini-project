"""
EntropyAuth — Flask API Server  (fixed)
========================================

KEY FIXES vs original
---------------------
1. New POST /upload-hash endpoint:
   code_generator.py hashes your local photo and pushes that hash here.
   /hash then serves it to the browser, so browser and CLI use the
   SAME frame hash → SAME code.

2. _latest_hash now also stores who set it ("source" field) for debugging.

3. Fallback hash formula kept identical to index.html's JS fallback:
     sha256("server_entropy_fallback:{window}")

Folder structure expected:
  generator/
  ├── server.py
  ├── entropy_image.py
  ├── code_generator.py
  ├── noise_frames/        ← auto-created
  ├── actual_images/       ← auto-created
  └── users.json           ← auto-created

Deploy on Render:
  Build command : pip install -r requirements.txt
  Start command : python server.py
  Env vars      : ADMIN_API_KEY = any-secret-string
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
def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE) as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)

# ── Entropy state ─────────────────────────────────────────────────────────────
# Shared between the background thread and the /upload-hash endpoint.
_latest_hash = {"value": None, "window": -1, "source": "none"}
_hash_lock   = threading.Lock()

# ── Background entropy loop (webcam / fallback) ───────────────────────────────
def _entropy_loop():
    """
    Runs every 30 s.  On Render there is no webcam, so this always falls
    back to a time-based hash.  That's fine — code_generator.py will
    OVERWRITE _latest_hash via /upload-hash with the real photo hash.
    """
    while True:
        try:
            from entropy_image import generate_entropy_image
            frame_hash, _, _ = generate_entropy_image()
            win = int(time.time()) // 30
            with _hash_lock:
                _latest_hash["value"]  = frame_hash
                _latest_hash["window"] = win
                _latest_hash["source"] = "webcam"
            print(f"[entropy/webcam] window={win} hash={frame_hash[:16]}…")
        except Exception as e:
            print(f"[entropy] webcam unavailable ({e}) — using time-based fallback")
            win      = int(time.time()) // 30
            fallback = hashlib.sha256(f"server_entropy_fallback:{win}".encode()).hexdigest()
            with _hash_lock:
                # Only overwrite if no photo hash has been uploaded yet
                if _latest_hash["source"] not in ("upload",):
                    _latest_hash["value"]  = fallback
                    _latest_hash["window"] = win
                    _latest_hash["source"] = "fallback"
                    print(f"[entropy/fallback] window={win} hash={fallback[:16]}…")

        now = time.time()
        time.sleep(30 - (now % 30))

threading.Thread(target=_entropy_loop, daemon=True).start()

# ── Code generation ───────────────────────────────────────────────────────────
def _generate_code(chaos_seed: str, frame_hash: str, window: int) -> str:
    """
    Mirrors index.html's makeCode() and code_generator.py's generate_code().
      HMAC-SHA256(key=chaos_seed, msg="{frame_hash}:{window}")[:8].upper()
    """
    message = f"{frame_hash}:{window}".encode()
    raw     = hmac.new(chaos_seed.encode(), message, hashlib.sha256).hexdigest()
    return raw[:8].upper()

def _get_current_hash_and_window():
    with _hash_lock:
        h = _latest_hash["value"]
        w = _latest_hash["window"]
    if h is None:
        w = int(time.time()) // 30
        h = hashlib.sha256(f"server_entropy_fallback:{w}".encode()).hexdigest()
    return h, w

# ═════════════════════════════════════════════════════════════════════════════
#  ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    with _hash_lock:
        src = _latest_hash["source"]
    return jsonify({"service": "EntropyAuth API", "status": "ok", "hash_source": src})


# ── GET /hash ─────────────────────────────────────────────────────────────────
# Browser calls this every 30 s to get the current frame hash.
@app.route("/hash")
def get_hash():
    h, w      = _get_current_hash_and_window()
    secs_left = 30 - (int(time.time()) % 30)
    with _hash_lock:
        src = _latest_hash["source"]
    return jsonify({
        "hash":         h,
        "window":       w,
        "seconds_left": secs_left,
        "source":       src   # "upload" | "webcam" | "fallback"
    })


# ── POST /upload-hash  ────────────────────────────────────────────────────────
# ✅ NEW ENDPOINT — called by code_generator.py after hashing your local photo.
# Stores the hash so /hash returns it to the browser.
#
# Body:   { "hash": "<64-char hex>", "admin_key": "<key>" }
# Returns:{ "ok": true, "hash": "...", "window": N }
@app.route("/upload-hash", methods=["POST"])
def upload_hash():
    data      = request.get_json(silent=True) or {}
    api_key   = data.get("admin_key", "")
    new_hash  = data.get("hash", "").strip().lower()

    # Authenticate
    if not secrets.compare_digest(api_key, ADMIN_API_KEY):
        return jsonify({"error": "Invalid admin_key"}), 403

    # Validate: must be a 64-char hex string (SHA-256 output)
    if len(new_hash) != 64 or not all(c in "0123456789abcdef" for c in new_hash):
        return jsonify({"error": "hash must be a 64-character hex string"}), 400

    win = int(time.time()) // 30
    with _hash_lock:
        _latest_hash["value"]  = new_hash
        _latest_hash["window"] = win
        _latest_hash["source"] = "upload"

    print(f"[upload-hash] window={win} hash={new_hash[:16]}… (from local photo)")
    return jsonify({"ok": True, "hash": new_hash, "window": win})


# ── POST /register ────────────────────────────────────────────────────────────
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

    valid = False
    for offset in [0, -1, 1]:
        expected = _generate_code(chaos_seed, h, w + offset)
        if secrets.compare_digest(code, expected):
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
        "chaos_seed_prefix": u["chaos_seed"][:8] + "····" + u["chaos_seed"][-4:],
        "created_at":        u.get("created_at")
    })


# ── GET /annotated-frame ──────────────────────────────────────────────────────
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
    print(f"Admin API key : {ADMIN_API_KEY}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
