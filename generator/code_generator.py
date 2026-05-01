"""
EntropyAuth — Local Code Generator
====================================
Run this script on your computer to generate the 8-digit hex OTP
from a photo you select locally.

HOW IT WORKS
------------
1. You supply a photo path (or drag-drop a file).
2. The image is normalised to 320x240 RGB and SHA-256 hashed
   (pixel bytes, identical method to the browser + server).
3. That hash is uploaded to the Render server via POST /upload-hash,
   so the browser's /hash endpoint returns the SAME value.
4. The code is: HMAC-SHA256(chaos_seed, "{frame_hash}:{window}")[:8].upper()
   where chaos_seed comes from the server's /register endpoint — the
   same seed the browser uses.

SETUP
-----
- config.json must contain:
    {
      "user_key":    "...",          <- your personal label (any string)
      "chaos_seed":  "...",          <- from server /register, set automatically
      "server_url":  "https://mini-project-a8ql.onrender.com",
      "admin_key":   "miniproject825"
    }

  Run the script once with no arguments to trigger first-time setup.
"""

import hashlib
import hmac
import time
import os
import sys
import json

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

DEFAULT_SERVER_URL = "https://mini-project-a8ql.onrender.com"
DEFAULT_ADMIN_KEY  = "miniproject825"


# ──────────────────────────────────────────────────────────────────
#  Time helpers
# ──────────────────────────────────────────────────────────────────

def get_current_window() -> int:
    return int(time.time()) // 30

def seconds_remaining() -> int:
    return 30 - (int(time.time()) % 30)


# ──────────────────────────────────────────────────────────────────
#  Config load / save
# ──────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    return {}

def save_config(cfg: dict):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"Config saved → {CONFIG_PATH}")


# ──────────────────────────────────────────────────────────────────
#  Register with server to get chaos_seed
# ──────────────────────────────────────────────────────────────────

def fetch_chaos_seed(user_key: str, server_url: str) -> str:
    """
    POST /register with uid=user_key.
    Returns the chaos_seed the server assigns to this uid.
    This seed is what the browser also uses — so codes match.
    """
    import urllib.request
    payload = json.dumps({"uid": user_key, "email": f"{user_key}@local"}).encode()
    req = urllib.request.Request(
        url     = f"{server_url}/register",
        data    = payload,
        headers = {"Content-Type": "application/json"},
        method  = "POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            seed = data.get("chaos_seed", "")
            if seed:
                return seed
            raise ValueError(f"Server returned no chaos_seed: {data}")
    except Exception as e:
        raise RuntimeError(f"Could not reach server to get chaos_seed: {e}")


# ──────────────────────────────────────────────────────────────────
#  First-time setup
# ──────────────────────────────────────────────────────────────────

def first_time_setup() -> dict:
    print("=" * 50)
    print("  EntropyAuth — First-time Setup")
    print("=" * 50)
    print()
    print("Your 'user_key' is just a label that identifies you")
    print("on the server. Use anything: your name, a word, etc.")
    print("Example:  rahul42   or   SanchiSharma")
    print()

    user_key   = input("Enter your personal key: ").strip()
    if not user_key:
        raise ValueError("Key cannot be empty.")

    server_url = input(f"Server URL [{DEFAULT_SERVER_URL}]: ").strip()
    if not server_url:
        server_url = DEFAULT_SERVER_URL

    admin_key  = input(f"Admin key [{DEFAULT_ADMIN_KEY}]: ").strip()
    if not admin_key:
        admin_key = DEFAULT_ADMIN_KEY

    print(f"\nRegistering '{user_key}' with server …")
    chaos_seed = fetch_chaos_seed(user_key, server_url)
    print(f"chaos_seed : {chaos_seed[:8]}…{chaos_seed[-4:]}")

    cfg = {
        "user_key":   user_key,
        "chaos_seed": chaos_seed,
        "server_url": server_url,
        "admin_key":  admin_key
    }
    save_config(cfg)
    print()
    return cfg


def ensure_config() -> dict:
    cfg = load_config()

    # Re-fetch chaos_seed if missing (e.g. old config.json that only had user_key)
    if not cfg.get("chaos_seed") and cfg.get("user_key"):
        server_url = cfg.get("server_url", DEFAULT_SERVER_URL)
        print(f"Fetching chaos_seed for '{cfg['user_key']}' from server …")
        try:
            cfg["chaos_seed"] = fetch_chaos_seed(cfg["user_key"], server_url)
            cfg.setdefault("server_url", server_url)
            cfg.setdefault("admin_key",  DEFAULT_ADMIN_KEY)
            save_config(cfg)
        except RuntimeError as e:
            print(f"[WARN] {e}")

    if not cfg.get("user_key") or not cfg.get("chaos_seed"):
        cfg = first_time_setup()

    return cfg


# ──────────────────────────────────────────────────────────────────
#  Core: generate code from a frame hash + chaos_seed
# ──────────────────────────────────────────────────────────────────

def generate_code(chaos_seed: str, frame_hash: str) -> str:
    """
    HMAC-SHA256(key=chaos_seed, msg="{frame_hash}:{window}")[:8].upper()

    ✅ FIX 1: key is chaos_seed (same as browser) — not user_key.
    The browser does:
        makeCode(chaosSeed, frameHash, win)
        → hmac256(chaosSeed, `${frameHash}:${win}`)[:8].upper()
    This function mirrors that exactly.
    """
    window  = get_current_window()
    message = f"{frame_hash}:{window}".encode("utf-8")
    raw     = hmac.new(
        key      = chaos_seed.encode("utf-8"),
        msg      = message,
        digestmod= hashlib.sha256
    ).hexdigest()
    return raw[:8].upper()


# ──────────────────────────────────────────────────────────────────
#  Main: accept a local photo → hash → upload → generate code
# ──────────────────────────────────────────────────────────────────

def main():
    print("EntropyAuth — Local Code Generator")
    print("-" * 40)

    cfg        = ensure_config()
    user_key   = cfg["user_key"]
    chaos_seed = cfg["chaos_seed"]
    server_url = cfg.get("server_url", DEFAULT_SERVER_URL)
    admin_key  = cfg.get("admin_key",  DEFAULT_ADMIN_KEY)

    # ── Pick the image ──────────────────────────────────────────
    if len(sys.argv) > 1:
        image_path = sys.argv[1]
    else:
        print()
        print("Provide the path to the photo you took on your computer.")
        print("Example:  /Users/you/Desktop/photo.jpg")
        print("          C:\\Users\\you\\Pictures\\photo.png")
        print()
        image_path = input("Image path: ").strip().strip("'\"")

    if not os.path.isfile(image_path):
        print(f"\nError: file not found — {image_path}")
        sys.exit(1)

    # ── Hash the image pixels (canonical function) ──────────────
    print(f"\nHashing image: {os.path.basename(image_path)}")
    from entropy_image import hash_image_file, upload_hash_to_server
    frame_hash = hash_image_file(image_path)
    print(f"Frame hash   : {frame_hash[:24]}…")

    # ── Upload hash to server so browser sees the same hash ─────
    print(f"Uploading hash to server …")
    ok = upload_hash_to_server(frame_hash, server_url, admin_key)
    if ok:
        print("Server updated ✓  (browser will now use your photo's hash)")
    else:
        print("[WARN] Server upload failed — code may not match browser.")
        print("       Make sure the server is running and admin_key is correct.")

    # ── Generate code ────────────────────────────────────────────
    code = generate_code(chaos_seed, frame_hash)
    secs = seconds_remaining()

    print()
    print("=" * 40)
    print(f"  YOUR CODE   :  {code}")
    print(f"  Expires in  :  {secs} seconds")
    print(f"  User key    :  {user_key}")
    print(f"  Chaos seed  :  {chaos_seed[:8]}…{chaos_seed[-4:]}")
    print("=" * 40)
    print()
    print("Open the browser authenticator — it should show the same code.")


if __name__ == "__main__":
    main()
