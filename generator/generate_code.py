"""
EntropyAuth — generate_code.py
Place this file in the  generator/  folder (same folder as server.py).

Usage:
    python generate_code.py                      <- webcam takes a photo
    python generate_code.py /path/to/photo.jpg   <- use an existing image

What happens step by step:
    1. Photo taken from webcam (or loaded from path you gave)
       -> if this fails for ANY reason, script exits. No photo = no code.
    2. Image resized to 320x240 and pixel bytes SHA-256 hashed
    3. Hash uploaded to Render server  (POST /upload-hash)
       -> browser now reads the exact same hash from GET /hash
    4. chaos_seed fetched once from server (POST /register)
       and saved to config.json so future runs skip this step
    5. Code = HMAC-SHA256(chaos_seed, "{hash}:{window}")[:8].upper()
       -> identical to what index.html computes in the browser
"""

import sys
import os
import json
import time
import hmac
import hashlib
import urllib.request
import urllib.error

import cv2
import numpy as np

# ── Paths ─────────────────────────────────────────────────────────────────────
# This file lives in  generator/  — BASE_DIR points to that folder.
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

# ── Constants ─────────────────────────────────────────────────────────────────
SERVER_URL = "https://mini-project-a8ql.onrender.com"
ADMIN_KEY  = "miniproject825"   # must match ADMIN_API_KEY env var on Render
IMG_W      = 320
IMG_H      = 240


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 1 — get image pixels
# ══════════════════════════════════════════════════════════════════════════════

def _from_webcam() -> np.ndarray:
    """Snap one frame from the default webcam. Returns RGB uint8 (H,W,3)."""
    print("  Opening webcam ...")
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise RuntimeError(
            "No webcam detected.\n"
            "  Fix: plug in a webcam, OR pass an image path as argument:\n"
            "       python generate_code.py /path/to/photo.jpg"
        )
    # Discard first 5 frames so exposure/white-balance can settle
    for _ in range(5):
        cap.read()
    ret, frame = cap.read()
    cap.release()
    if not ret or frame is None:
        raise RuntimeError("Webcam opened but failed to capture a frame.")
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return cv2.resize(rgb, (IMG_W, IMG_H), interpolation=cv2.INTER_AREA).astype(np.uint8)


def _from_file(path: str) -> np.ndarray:
    """Load any image file, resize to standard size. Returns RGB uint8 (H,W,3)."""
    print(f"  Loading image: {path}")
    img = cv2.imread(path)
    if img is None:
        raise RuntimeError(f"Cannot open image: {path}")
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return cv2.resize(rgb, (IMG_W, IMG_H), interpolation=cv2.INTER_AREA).astype(np.uint8)


def get_pixels() -> np.ndarray:
    """
    Returns normalised pixel array.
    Uses sys.argv[1] as file path if provided, otherwise webcam.
    Raises RuntimeError on any failure.
    """
    if len(sys.argv) > 1:
        return _from_file(sys.argv[1])
    return _from_webcam()


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 2 — hash pixels
# ══════════════════════════════════════════════════════════════════════════════

def hash_pixels(pixels: np.ndarray) -> str:
    """
    SHA-256 of raw pixel bytes.
    server.py uses the same formula in _hash_pixels().
    index.html reads this value from GET /hash after we upload it in step 3.
    """
    return hashlib.sha256(pixels.tobytes()).hexdigest()


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 3 — upload hash to server
# ══════════════════════════════════════════════════════════════════════════════

def upload_hash(frame_hash: str) -> None:
    """
    POST /upload-hash  { "hash": "<64-char hex>", "admin_key": "..." }
    Server stores it so GET /hash returns it to the browser.
    Raises on any failure — no silent swallowing.
    """
    payload = json.dumps({"hash": frame_hash, "admin_key": ADMIN_KEY}).encode()
    req = urllib.request.Request(
        url     = f"{SERVER_URL}/upload-hash",
        data    = payload,
        headers = {"Content-Type": "application/json"},
        method  = "POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
        if not body.get("ok"):
            raise RuntimeError(f"Server rejected upload: {body}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Server HTTP {e.code}: {e.read().decode()}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach server ({SERVER_URL}): {e.reason}")


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 4 — get chaos_seed (fetched once, cached in config.json)
# ══════════════════════════════════════════════════════════════════════════════

def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}

def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

def _fetch_chaos_seed_from_server(user_key: str) -> str:
    """POST /register -> returns the chaos_seed assigned to this user_key."""
    payload = json.dumps({"uid": user_key, "email": f"{user_key}@local"}).encode()
    req = urllib.request.Request(
        url     = f"{SERVER_URL}/register",
        data    = payload,
        headers = {"Content-Type": "application/json"},
        method  = "POST"
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

def get_chaos_seed() -> str:
    """
    Returns chaos_seed for the configured user_key.
    - Reads user_key from config.json (asks interactively on first run)
    - Returns cached chaos_seed immediately if already in config.json
    - Otherwise fetches from /register and saves it
    """
    cfg = load_config()

    if not cfg.get("user_key"):
        print()
        print("  First-time setup")
        print("  Enter a personal key (any word or name, e.g. SanchiSharma)")
        key = input("  Your user key: ").strip()
        if not key:
            raise RuntimeError("User key cannot be empty.")
        cfg["user_key"] = key
        save_config(cfg)

    user_key = cfg["user_key"]

    if cfg.get("chaos_seed"):
        return cfg["chaos_seed"]

    print(f"  Fetching chaos_seed for '{user_key}' from server ...")
    chaos_seed = _fetch_chaos_seed_from_server(user_key)
    cfg["chaos_seed"] = chaos_seed
    save_config(cfg)
    print(f"  Saved to {CONFIG_PATH}")
    return chaos_seed


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 5 — generate OTP
# ══════════════════════════════════════════════════════════════════════════════

def generate_otp(chaos_seed: str, frame_hash: str) -> str:
    """
    Exact mirror of index.html makeCode() and server.py _generate_code():
        HMAC-SHA256(key=chaos_seed, msg="{frame_hash}:{window}")[:8].upper()
    All three must stay identical or codes will not match.
    """
    window  = int(time.time()) // 30
    message = f"{frame_hash}:{window}".encode()
    digest  = hmac.new(chaos_seed.encode(), message, hashlib.sha256).hexdigest()
    return digest[:8].upper()

def secs_left() -> int:
    return 30 - (int(time.time()) % 30)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 48)
    print("  EntropyAuth  Code Generator")
    print("=" * 48)

    # STEP 1 — image
    try:
        pixels = get_pixels()
    except RuntimeError as e:
        print(f"\n[FAIL] {e}")
        print("  No code generated.")
        sys.exit(1)
    print(f"[OK] Image captured ({IMG_W}x{IMG_H})")

    # STEP 2 — hash
    frame_hash = hash_pixels(pixels)
    print(f"[OK] Hash : {frame_hash[:24]}...")

    # STEP 3 — upload
    print(f"[..] Uploading hash to server ...")
    try:
        upload_hash(frame_hash)
        print("[OK] Server updated — browser will use your photo's hash")
    except RuntimeError as e:
        print(f"\n[FAIL] Upload failed: {e}")
        print("  No code generated.")
        sys.exit(1)

    # STEP 4 — chaos_seed
    try:
        chaos_seed = get_chaos_seed()
    except RuntimeError as e:
        print(f"\n[FAIL] {e}")
        sys.exit(1)

    # STEP 5 — OTP
    code = generate_otp(chaos_seed, frame_hash)
    secs = secs_left()
    cfg  = load_config()

    print()
    print("=" * 48)
    print(f"  YOUR CODE  :  {code}")
    print(f"  Expires in :  {secs} seconds")
    print(f"  User key   :  {cfg.get('user_key', '')}")
    print("=" * 48)
    print()
    print("Open index.html — it will show the same code.")


if __name__ == "__main__":
    main()
