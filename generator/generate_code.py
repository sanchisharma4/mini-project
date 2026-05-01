"""
EntropyAuth — generate_code.py

Runs continuously. Every 30 seconds:
  1. Captures a webcam photo (or reuses a provided image path)
  2. Hashes the pixels and uploads the hash to the server
  3. Server pushes the new hash to all connected browsers automatically
  4. Prints a live dashboard of every active user's current code

Usage:
    python generate_code.py                   # webcam, loops forever
    python generate_code.py photo.jpg         # fixed image, loops forever
    python generate_code.py --setup           # first-time UID configuration
"""

import sys, os, json, time, hmac, hashlib, urllib.request, urllib.error

try:
    import cv2, numpy as np
    _CV2_OK = True
except ImportError:
    _CV2_OK = False

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

SERVER_URL   = "https://mini-project-a8ql.onrender.com"
ADMIN_KEY    = "miniproject825"
IMG_W, IMG_H = 320, 240
INTERVAL     = 30   # seconds between captures


# ── Config ─────────────────────────────────────────────────────────────────────

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


# ── Image capture ──────────────────────────────────────────────────────────────

def capture_webcam():
    if not _CV2_OK:
        raise RuntimeError("opencv-python not installed. Run: pip install opencv-python numpy")
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise RuntimeError("No webcam found. Pass an image path instead: python generate_code.py photo.jpg")
    for _ in range(5):          # let exposure settle
        cap.read()
    ret, frame = cap.read()
    cap.release()
    if not ret or frame is None:
        raise RuntimeError("Webcam opened but failed to read a frame.")
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return cv2.resize(rgb, (IMG_W, IMG_H), interpolation=cv2.INTER_AREA).astype(np.uint8)

def load_image(path):
    if not _CV2_OK:
        raise RuntimeError("opencv-python not installed. Run: pip install opencv-python numpy")
    img = cv2.imread(path)
    if img is None:
        raise RuntimeError(f"Cannot open image: {path}")
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return cv2.resize(rgb, (IMG_W, IMG_H), interpolation=cv2.INTER_AREA).astype(np.uint8)

def hash_pixels(pixels):
    return hashlib.sha256(pixels.tobytes()).hexdigest()


# ── Server calls ───────────────────────────────────────────────────────────────

def _post(path, payload):
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        url=f"{SERVER_URL}{path}", data=data,
        headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def _get(path):
    with urllib.request.urlopen(f"{SERVER_URL}{path}", timeout=15) as r:
        return json.loads(r.read())

def upload_hash(frame_hash):
    body = _post("/upload-hash", {"hash": frame_hash, "admin_key": ADMIN_KEY})
    if not body.get("ok"):
        raise RuntimeError(f"Server rejected hash: {body}")
    return body.get("window", int(time.time()) // 30)

def fetch_chaos_seed(uid, email):
    body = _post("/register", {"uid": uid, "email": email})
    seed = body.get("chaos_seed", "")
    if not seed:
        raise RuntimeError(f"Server returned no chaos_seed: {body}")
    return seed

def fetch_all_users():
    try:
        body = _get(f"/admin/codes?admin_key={ADMIN_KEY}")
        return body.get("users", [])
    except Exception:
        return []


# ── OTP ────────────────────────────────────────────────────────────────────────

def make_code(chaos_seed, frame_hash, window):
    msg = f"{frame_hash}:{window}".encode()
    return hmac.new(chaos_seed.encode(), msg, hashlib.sha256).hexdigest()[:8].upper()

def secs_left():
    return INTERVAL - (int(time.time()) % INTERVAL)


# ── Terminal UI ────────────────────────────────────────────────────────────────

CYAN  = "\033[96m"
GREEN = "\033[92m"
DIM   = "\033[2m"
BOLD  = "\033[1m"
RESET = "\033[0m"
LINE  = "─" * 52

def clear_line():
    print("\r" + " " * 60 + "\r", end="", flush=True)

def print_dashboard(my_email, my_code, window, users, secs):
    # Build a clean table
    ts = time.strftime("%H:%M:%S")
    print(f"\n{LINE}")
    print(f"  {BOLD}{CYAN}ENTROPY AUTH{RESET}   {DIM}{ts}   window {window}{RESET}")
    print(LINE)
    for u in users:
        is_me  = u.get("email") == my_email
        code   = u.get("code", "--------")
        email  = u.get("email", "unknown")
        marker = f"  {GREEN}← you{RESET}" if is_me else ""
        tag    = f"{CYAN}{code}{RESET}" if is_me else code
        print(f"  {email:<36} {tag}{marker}")
    if not users:
        print(f"  {DIM}no active users{RESET}")
    print(LINE)
    print(f"  {DIM}Next capture in {secs}s{RESET}", end="", flush=True)


# ── Setup ──────────────────────────────────────────────────────────────────────

def run_setup():
    print(f"\n{LINE}")
    print(f"  {BOLD}EntropyAuth — Setup{RESET}")
    print(LINE)
    print()
    print("  Your user_key must be your Google account UID.")
    print("  To find it: open index.html → sign in with Google")
    print("  → open browser console → type:  window.__user.uid")
    print()
    uid = input("  Google UID: ").strip()
    if not uid:
        print("  [error] UID cannot be empty.")
        sys.exit(1)
    email = input("  Gmail address: ").strip()
    if not email:
        email = f"{uid[:8]}@google.com"

    cfg = load_config()
    cfg["user_key"] = uid
    cfg["email"]    = email
    cfg.pop("chaos_seed", None)
    save_config(cfg)

    print("\n  Registering with server …", end="", flush=True)
    try:
        seed = fetch_chaos_seed(uid, email)
        cfg["chaos_seed"] = seed
        save_config(cfg)
        print(f"\r  Registered.                              ")
        print(f"\n  Setup complete. Run:  python generate_code.py\n")
    except Exception as e:
        print(f"\n  [error] {e}")
        sys.exit(1)


# ── Main loop ──────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if "--setup" in args:
        run_setup()
        return

    # Load identity
    cfg   = load_config()
    uid   = cfg.get("user_key", "").strip()
    email = cfg.get("email", "").strip()

    if not uid:
        print("\n  [error] Not configured. Run:  python generate_code.py --setup\n")
        sys.exit(1)

    # Ensure chaos_seed is cached
    chaos_seed = cfg.get("chaos_seed")
    if not chaos_seed:
        try:
            chaos_seed = fetch_chaos_seed(uid, email)
            cfg["chaos_seed"] = chaos_seed
            save_config(cfg)
        except Exception as e:
            print(f"\n  [error] Cannot register with server: {e}\n")
            sys.exit(1)

    image_path = args[0] if args else None

    print(f"\n{LINE}")
    print(f"  {BOLD}{CYAN}ENTROPY AUTH{RESET}  running  {DIM}(Ctrl+C to stop){RESET}")
    print(f"  Capturing every {INTERVAL}s and pushing to all browsers")
    print(LINE)

    while True:
        # ── Capture ──────────────────────────────────────────────────────────
        try:
            pixels = load_image(image_path) if image_path else capture_webcam()
        except RuntimeError as e:
            print(f"\n  [error] {e}")
            sys.exit(1)

        frame_hash = hash_pixels(pixels)

        # ── Upload ───────────────────────────────────────────────────────────
        try:
            window = upload_hash(frame_hash)
        except Exception as e:
            print(f"\n  [warn] Upload failed: {e}  — retrying next cycle")
            time.sleep(INTERVAL)
            continue

        # ── Generate my code ─────────────────────────────────────────────────
        my_code = make_code(chaos_seed, frame_hash, window)

        # ── Fetch all users ───────────────────────────────────────────────────
        users = fetch_all_users()

        # ── Print dashboard ───────────────────────────────────────────────────
        clear_line()
        print_dashboard(email, my_code, window, users, secs_left())

        # ── Wait for next window ─────────────────────────────────────────────
        while secs_left() > 1:
            time.sleep(1)
            clear_line()
            print(f"  {DIM}Next capture in {secs_left()}s{RESET}", end="", flush=True)

        clear_line()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n  {DIM}Stopped.{RESET}\n")
