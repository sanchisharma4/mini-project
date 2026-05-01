import numpy as np
import hashlib
import time
import os
import zlib
import struct
import json

import cv2

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
NOISE_DIR   = os.path.join(BASE_DIR, "noise_frames")
ACTUAL_DIR  = os.path.join(BASE_DIR, "actual_images")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

os.makedirs(NOISE_DIR,  exist_ok=True)
os.makedirs(ACTUAL_DIR, exist_ok=True)


# ------------------------------------------------------------------ #
#  PNG writer  (no Pillow needed)                                      #
# ------------------------------------------------------------------ #

def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    length = struct.pack(">I", len(data))
    crc    = struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    return length + chunk_type + data + crc


def _write_png(pixels: np.ndarray, path: str):
    """Save an RGB uint8 numpy array as a PNG file."""
    height, width, _ = pixels.shape
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    raw_rows = b"".join(b"\x00" + row.tobytes() for row in pixels)
    idat = _png_chunk(b"IDAT", zlib.compress(raw_rows, level=1))
    iend = _png_chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(signature + ihdr + idat + iend)


# ------------------------------------------------------------------ #
#  THE canonical hash function — used everywhere                       #
# ------------------------------------------------------------------ #

def hash_pixels(pixels: np.ndarray) -> str:
    """
    SHA-256 of raw pixel bytes (tobytes()).
    Every part of the system must call THIS function — never hash file
    bytes directly — so the hash is always identical no matter who
    computes it (server, CLI, local photo path).
    """
    return hashlib.sha256(pixels.tobytes()).hexdigest()


# ------------------------------------------------------------------ #
#  Load any local image file as a normalised 320x240 RGB array        #
# ------------------------------------------------------------------ #

def load_image_as_rgb(image_path: str, width: int = 320, height: int = 240) -> np.ndarray:
    """
    Load a JPEG / PNG / etc. from disk, resize to (width x height),
    and return as RGB uint8 numpy array.
    Raises FileNotFoundError if OpenCV cannot open the file.
    """
    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(f"Could not load image: {image_path}")
    rgb     = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb, (width, height), interpolation=cv2.INTER_AREA)
    return resized.astype(np.uint8)


# ------------------------------------------------------------------ #
#  Public: hash a local photo file                                     #
# ------------------------------------------------------------------ #

def hash_image_file(image_path: str) -> str:
    """
    Load a local image, normalise it to 320x240 RGB, and return
    SHA-256 of the raw pixel bytes via hash_pixels().

    Called by code_generator.py when the user picks a photo.
    The server stores the same hash so the browser code matches.
    """
    pixels = load_image_as_rgb(image_path, width=320, height=240)
    return hash_pixels(pixels)


# ------------------------------------------------------------------ #
#  Upload hash to Render server                                        #
# ------------------------------------------------------------------ #

def upload_hash_to_server(frame_hash: str, server_url: str, admin_key: str) -> bool:
    """
    Push the locally-computed frame hash to the Render server so that
    the browser's /hash endpoint returns the same value.

    POST /upload-hash  { "hash": "<64-char hex>", "admin_key": "<key>" }
    Returns True on success, False on any error.
    """
    import urllib.request
    payload = json.dumps({"hash": frame_hash, "admin_key": admin_key}).encode("utf-8")
    req = urllib.request.Request(
        url     = f"{server_url}/upload-hash",
        data    = payload,
        headers = {"Content-Type": "application/json"},
        method  = "POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
            return body.get("ok", False)
    except Exception as e:
        print(f"[WARN] Could not upload hash to server: {e}")
        return False


# ------------------------------------------------------------------ #
#  Webcam capture (used by server loop)                                #
# ------------------------------------------------------------------ #

def _capture_frame(width: int = 320, height: int = 240) -> np.ndarray:
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[WARN] No webcam found — using random noise as fallback.")
        return np.random.randint(0, 256, (height, width, 3), dtype=np.uint8)

    for _ in range(3):
        cap.read()

    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        print("[WARN] Frame capture failed — using random noise as fallback.")
        return np.random.randint(0, 256, (height, width, 3), dtype=np.uint8)

    rgb     = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb, (width, height), interpolation=cv2.INTER_AREA)
    return resized.astype(np.uint8)


# ------------------------------------------------------------------ #
#  Bimodal hotspot detection  (bright + dark)                          #
# ------------------------------------------------------------------ #

def _find_bimodal_hotspots(pixels: np.ndarray, count: int = 5):
    R = pixels[:, :, 0].astype(np.float32)
    G = pixels[:, :, 1].astype(np.float32)
    B = pixels[:, :, 2].astype(np.float32)
    lum  = 0.299 * R + 0.587 * G + 0.114 * B
    flat = lum.ravel()
    h, w = lum.shape

    bright_idx = np.argsort(flat)[::-1]
    dark_idx   = np.argsort(flat)

    bright_spots, dark_spots = [], []
    seen = set()

    for idx in bright_idx:
        if idx not in seen and len(bright_spots) < count:
            bright_spots.append((int(idx // w), int(idx % w)))
            seen.add(idx)

    for idx in dark_idx:
        if idx not in seen and len(dark_spots) < count:
            dark_spots.append((int(idx // w), int(idx % w)))
            seen.add(idx)

    return bright_spots, dark_spots


# ------------------------------------------------------------------ #
#  Circle drawing  (pure numpy — no OpenCV)                           #
# ------------------------------------------------------------------ #

def _draw_circle(pixels: np.ndarray, cy: int, cx: int, radius: int, color: tuple):
    h, w      = pixels.shape[:2]
    color_arr = np.array(color, dtype=np.uint8)
    x, y, err = radius, 0, 0
    while x >= y:
        for dx, dy in [(x,y),(y,x),(-y,x),(-x,y),(-x,-y),(-y,-x),(y,-x),(x,-y)]:
            pr, pc = cy + dy, cx + dx
            if 0 <= pr < h and 0 <= pc < w:
                pixels[pr, pc] = color_arr
        y   += 1
        err += 2 * y + 1
        if 2 * (err - x) + 1 > 0:
            x   -= 1
            err += 1 - 2 * x


def _annotate_image(pixels: np.ndarray, bright_spots: list, dark_spots: list) -> np.ndarray:
    scale = 3
    big   = np.repeat(np.repeat(pixels, scale, axis=0), scale, axis=1).copy()
    YELLOW = (255, 220,   0)
    CYAN   = (  0, 220, 255)

    for row, col in bright_spots:
        cy, cx = row * scale + scale // 2, col * scale + scale // 2
        for r, c in [(10, YELLOW), (11, (200, 170, 0)), (12, (150, 120, 0))]:
            _draw_circle(big, cy, cx, r, c)

    for row, col in dark_spots:
        cy, cx = row * scale + scale // 2, col * scale + scale // 2
        for r, c in [(10, CYAN), (11, (0, 170, 200)), (12, (0, 120, 150))]:
            _draw_circle(big, cy, cx, r, c)

    return big


# ------------------------------------------------------------------ #
#  Full webcam pipeline (used by server entropy loop)                  #
# ------------------------------------------------------------------ #

def generate_entropy_image() -> tuple:
    """
    1. Capture webcam frame (320x240 RGB)
    2. Save raw frame  → noise_frames/
    3. hash_pixels()   → frame_hash   ✅ canonical hash
    4. Bimodal hotspots
    5. Annotate + save → actual_images/
    6. Return (frame_hash, raw_path, annotated_path)
    """
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    pixels   = _capture_frame(width=320, height=240)
    raw_path = os.path.join(NOISE_DIR, f"frame_{timestamp}.png")
    _write_png(pixels, raw_path)

    frame_hash = hash_pixels(pixels)   # ✅ always pixel bytes

    bright_spots, dark_spots = _find_bimodal_hotspots(pixels, count=5)

    annotated   = _annotate_image(pixels, bright_spots, dark_spots)
    actual_path = os.path.join(ACTUAL_DIR, f"annotated_{timestamp}.png")
    _write_png(annotated, actual_path)

    return frame_hash, raw_path, actual_path


# ------------------------------------------------------------------ #
#  get_latest_frame_hash — used by code_generator.py (webcam mode)    #
# ------------------------------------------------------------------ #

def get_latest_frame_hash() -> str:
    """
    Returns SHA-256 pixel hash of the most recently saved raw webcam frame.

    ✅ FIX: loads PNG pixels via OpenCV then calls hash_pixels().
    The old code called hashlib.sha256(file_bytes) which gave a
    completely different digest from generate_entropy_image().
    """
    files = sorted(
        [f for f in os.listdir(NOISE_DIR) if f.endswith(".png")],
        reverse=True
    )
    if not files:
        raise FileNotFoundError(
            "No frames in noise_frames/.\n"
            "Run entropy_image.py first to populate the folder."
        )
    latest_path = os.path.join(NOISE_DIR, files[0])

    img     = cv2.imread(latest_path)
    rgb     = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb, (320, 240), interpolation=cv2.INTER_AREA)
    pixels  = resized.astype(np.uint8)

    return hash_pixels(pixels)


# ------------------------------------------------------------------ #
#  Run loop (standalone: webcam every 30 s)                           #
# ------------------------------------------------------------------ #

def run_loop():
    print("EntropyAuth — webcam entropy generator")
    print(f"  Raw frames  → {NOISE_DIR}")
    print(f"  Annotated   → {ACTUAL_DIR}")
    print("  Yellow rings = bright hotspots  |  Cyan rings = dark coldspots")
    print("Press Ctrl+C to stop.\n")

    while True:
        frame_hash, raw_path, actual_path = generate_entropy_image()
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] hash     : {frame_hash[:24]}...")
        print(f"       raw      : {os.path.basename(raw_path)}")
        print(f"       annotated: {os.path.basename(actual_path)}\n")

        now = time.time()
        time.sleep(30 - (now % 30))


if __name__ == "__main__":
    run_loop()
