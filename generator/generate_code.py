"""
EntropyAuth — generate_code.py  (FIXED)
Place in the generator/ folder alongside server.py.

Usage:
    python generate_code.py                      # webcam snap → upload hash → show YOUR code
    python generate_code.py /path/to/photo.jpg   # use existing image

    python generate_code.py --watch              # continuously watch & print ALL users' codes
    python generate_code.py --users              # one-shot: print current code for every user

KEY FIX: user_key in config.json must be your Google UID (the numeric 'sub' value).
         Run once with --setup to save it.

    python generate_code.py --setup
"""

import sys, os, json, time, hmac, hashlib, urllib.request, urllib.error, threading

# ── cv2 / numpy are optional (webcam path only) ──────────────────────────────
try:
    import cv2, numpy as np
    _CV2_OK = True
except ImportError:
    _CV2_OK = False

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

# ── Constants ─────────────────────────────────────────────────────────────────
SERVER_URL = "https://mini-project-a8ql.onrender.com"
ADMIN_KEY  = "miniproject825"
IMG_W, IMG_H = 320, 240


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}

def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 1 — get image pixels  (ONLY if an image is provided or webcam)
# ══════════════════════════════════════════════════════════════════════════════

def _from_webcam():
    if not _CV2_OK:
        raise RuntimeError("cv2 not installed. Run: pip install opencv-python numpy")
    print("  Opening webcam ...")
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise RuntimeError(
            "No webcam detected.\n"
            "  Fix: plug in a webcam, OR pass an image path:\n"
            "       python generate_code.py /path/to/photo.jpg"
        )
    for _ in range(5):
        cap.read()
    ret, frame = cap.read()
    cap.release()
    if not ret or frame is None:
        raise RuntimeError("Webcam opened but failed to capture a frame.")
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return cv2.resize(rgb, (IMG_W, IMG_H), interpolation=cv2.INTER_AREA).astype(np.uint8)

def _from_file(path: str):
    if not _CV2_OK:
        raise RuntimeError("cv2 not installed. Run: pip install opencv-python numpy")
    print(f"  Loading image: {path}")
    img = cv2.imread(path)
    if img is None:
        raise RuntimeError(f"Cannot open image: {path}")
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return cv2.resize(rgb, (IMG_W, IMG_H), interpolation=cv2.INTER_AREA).astype(np.uint8)

def get_pixels(path=None):
    if path:
        return _from_file(path)
    return _from_webcam()


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 2 — hash pixels
# ══════════════════════════════════════════════════════════════════════════════

def hash_pixels(pixels) -> str:
    return hashlib.sha256(pixels.tobytes()).hexdigest()


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 3 — upload hash to server
# ══════════════════════════════════════════════════════════════════════════════

def upload_hash(frame_hash: str) -> int:
    """Returns the window number the server assigned."""
    payload = json.dumps({"hash": frame_hash, "admin_key": ADMIN_KEY}).encode()
    req = urllib.request.Request(
        url=f"{SERVER_URL}/upload-hash",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
        if not body.get("ok"):
            raise RuntimeError(f"Server rejected upload: {body}")
        return body.get("window", int(time.time()) // 30)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Server HTTP {e.code}: {e.read().decode()}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach server ({SERVER_URL}): {e.reason}")


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 4 — get chaos_seed for THIS user
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_chaos_seed(uid: str, email: str) -> str:
    payload = json.dumps({"uid": uid, "email": email}).encode()
    req = urllib.request.Request(
        url=f"{SERVER_URL}/register",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        seed = data.get("chaos_seed", "")
        if not seed:
            raise RuntimeError(f"Server gave no chaos_seed: {data}")
        return seed
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Register HTTP {e.code}: {e.read().decode()}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach server: {e.reason}")

def get_chaos_seed(uid: str, email: str) -> str:
    cfg = load_config()
    if cfg.get("chaos_seed"):
        return cfg["chaos_seed"]
    print(f"  Fetching chaos_seed for UID '{uid[:8]}…' from server ...")
    seed = _fetch_chaos_seed(uid, email)
    cfg["chaos_seed"] = seed
    save_config(cfg)
    print(f"  Saved to {CONFIG_PATH}")
    return seed


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 5 — generate OTP  (identical to index.html makeCode + server _generate_code)
# ══════════════════════════════════════════════════════════════════════════════

def generate_otp(chaos_seed: str, frame_hash: str, window: int = None) -> str:
    if window is None:
        window = int(time.time()) // 30
    message = f"{frame_hash}:{window}".encode()
    digest  = hmac.new(chaos_seed.encode(), message, hashlib.sha256).hexdigest()
    return digest[:8].upper()

def secs_left() -> int:
    return 30 - (int(time.time()) % 30)


# ══════════════════════════════════════════════════════════════════════════════
#  ADMIN — fetch ALL users' current codes from server
# ══════════════════════════════════════════════════════════════════════════════

def fetch_all_user_codes() -> list:
    """
    Calls GET /admin/codes?admin_key=... on the server.
    Returns list of {email, code, window} dicts.
    """
    url = f"{SERVER_URL}/admin/codes?admin_key={ADMIN_KEY}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()).get("users", [])
    except Exception as e:
        print(f"[WARN] Could not fetch user codes: {e}")
        return []

def fetch_current_hash_from_server() -> tuple:
    """Returns (hash, window) currently stored on server."""
    url = f"{SERVER_URL}/hash"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            d = json.loads(resp.read())
            return d.get("hash"), d.get("window", int(time.time()) // 30)
    except Exception as e:
        raise RuntimeError(f"Cannot fetch hash from server: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  SETUP — save Google UID to config
# ══════════════════════════════════════════════════════════════════════════════

def run_setup():
    cfg = load_config()
    print()
    print("=" * 52)
    print("  EntropyAuth — First-time Setup")
    print("=" * 52)
    print()
    print("  You need your Google UID (the numeric 'sub' value).")
    print("  To find it: open index.html → login with Google →")
    print("  open browser console → type:  window.__user.uid")
    print()
    uid = input("  Paste your Google UID: ").strip()
    if not uid:
        print("[FAIL] UID cannot be empty.")
        sys.exit(1)
    email = input("  Your Gmail address (e.g. you@gmail.com): ").strip()
    if not email:
        email = f"{uid}@google"

    # Clear stale seed so it refetches
    cfg["user_key"] = uid
    cfg["email"]    = email
    cfg.pop("chaos_seed", None)
    save_config(cfg)

    print(f"\n  Fetching chaos_seed from server for UID {uid[:8]}…")
    try:
        seed = _fetch_chaos_seed(uid, email)
        cfg["chaos_seed"] = seed
        save_config(cfg)
        print(f"  chaos_seed = {seed[:8]}…{seed[-4:]}")
        print(f"  Saved to {CONFIG_PATH}")
        print()
        print("  Setup complete! Now run:  python generate_code.py")
    except RuntimeError as e:
        print(f"\n[FAIL] {e}")
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
#  WATCH MODE — continuously print all users' codes
# ══════════════════════════════════════════════════════════════════════════════

def run_watch():
    print()
    print("=" * 60)
    print("  EntropyAuth — Watch Mode  (Ctrl+C to stop)")
    print("  Showing codes for ALL active users every 30 seconds")
    print("=" * 60)

    last_win = -1
    while True:
        win  = int(time.time()) // 30
        secs = secs_left()

        if win != last_win:
            last_win = win
            users = fetch_all_user_codes()
            ts = time.strftime("%H:%M:%S")
            print(f"\n[{ts}]  Window={win}  ({secs}s left)")
            print("-" * 60)
            if not users:
                print("  (no registered users found, or server error)")
            for u in users:
                print(f"  {u['email']:<35}  CODE: {u['code']}")
            print("-" * 60)
        else:
            # Just show countdown on same line
            print(f"\r  Next refresh in {secs:2d}s …", end="", flush=True)

        time.sleep(1)


# ══════════════════════════════════════════════════════════════════════════════
#  ONE-SHOT USERS MODE
# ══════════════════════════════════════════════════════════════════════════════

def run_users():
    users = fetch_all_user_codes()
    win   = int(time.time()) // 30
    print()
    print(f"Current codes (window={win}, {secs_left()}s left):")
    print("-" * 60)
    for u in users:
        print(f"  {u['email']:<35}  CODE: {u['code']}")
    if not users:
        print("  (no users registered yet)")
    print("-" * 60)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN — image click → upload → generate YOUR code
# ══════════════════════════════════════════════════════════════════════════════

def main():
    args = sys.argv[1:]

    if "--setup" in args:
        run_setup()
        return

    if "--watch" in args:
        run_watch()
        return

    if "--users" in args:
        run_users()
        return

    # ── Load user identity ────────────────────────────────────────────────────
    cfg = load_config()
    uid   = cfg.get("user_key", "").strip()
    email = cfg.get("email", f"{uid}@google").strip()

    if not uid:
        print()
        print("[ERROR] No user_key found in config.json.")
        print("  Run setup first:  python generate_code.py --setup")
        print("  Your user_key must be your Google UID (numeric sub).")
        sys.exit(1)

    print("=" * 52)
    print("  EntropyAuth — Code Generator")
    print("=" * 52)
    print(f"  User  : {email}")
    print(f"  UID   : {uid[:8]}…")

    # ── STEP 1 — image  (REQUIRED — no image = no code) ──────────────────────
    image_path = args[0] if args else None
    try:
        pixels = get_pixels(image_path)
    except RuntimeError as e:
        print(f"\n[FAIL] {e}")
        print("  No image → no code. Provide a webcam or pass an image path.")
        sys.exit(1)
    print(f"[OK] Image captured ({IMG_W}x{IMG_H})")

    # ── STEP 2 — hash ─────────────────────────────────────────────────────────
    frame_hash = hash_pixels(pixels)
    print(f"[OK] Hash : {frame_hash[:24]}…")

    # ── STEP 3 — upload hash → server broadcasts to all browsers ─────────────
    print("[..] Uploading hash to server …")
    try:
        win = upload_hash(frame_hash)
        print(f"[OK] Server updated (window={win}) — browser will use your photo's hash")
    except RuntimeError as e:
        print(f"\n[FAIL] Upload failed: {e}")
        sys.exit(1)

    # ── STEP 4 — chaos_seed ───────────────────────────────────────────────────
    try:
        chaos_seed = get_chaos_seed(uid, email)
    except RuntimeError as e:
        print(f"\n[FAIL] {e}")
        sys.exit(1)

    # ── STEP 5 — OTP (uses server window so it matches browser exactly) ───────
    code = generate_otp(chaos_seed, frame_hash, win)
    secs = secs_left()

    print()
    print("=" * 52)
    print(f"  YOUR CODE  :  {code}")
    print(f"  Expires in :  {secs} seconds")
    print(f"  User       :  {email}")
    print(f"  Window     :  {win}")
    print("=" * 52)
    print()
    print("Open index.html — it will show the SAME code once you click an image.")
    print()

    # Also print all other users' codes so you can monitor them
    print("── All active users (current window) ──────────────────")
    users = fetch_all_user_codes()
    for u in users:
        marker = " ← YOU" if u.get("uid") == uid or u.get("email") == email else ""
        print(f"  {u['email']:<35}  CODE: {u['code']}{marker}")
    if not users:
        print("  (server returned no user list — /admin/codes may need deployment)")
    print("────────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
