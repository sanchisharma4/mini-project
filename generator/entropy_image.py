import numpy as np
import hashlib
import time
import os
import zlib
import struct

# cv2 is used ONLY for webcam capture (one line).
# Everything else — PNG writing, circle drawing, hotspot detection — is pure numpy/stdlib.
import cv2

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
NOISE_DIR   = os.path.join(BASE_DIR, "noise_frames")
ACTUAL_DIR  = os.path.join(BASE_DIR, "actual_images")

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
#  Webcam capture                                                      #
# ------------------------------------------------------------------ #

def _capture_frame(width: int = 320, height: int = 240) -> np.ndarray:
    """
    Grab one frame from the default webcam and return it as an RGB uint8
    numpy array of shape (height, width, 3).

    Falls back to pure random noise if no camera is found — so the rest of
    the pipeline always works even on a headless machine.
    """
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[WARN] No webcam found — using random noise as fallback.")
        return np.random.randint(0, 256, (height, width, 3), dtype=np.uint8)

    # Discard the first few frames; many cameras need a moment to stabilise
    # exposure / white-balance before the image is meaningful.
    for _ in range(3):
        cap.read()

    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        print("[WARN] Frame capture failed — using random noise as fallback.")
        return np.random.randint(0, 256, (height, width, 3), dtype=np.uint8)

    # OpenCV gives BGR; convert to RGB then resize to target resolution.
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb, (width, height), interpolation=cv2.INTER_AREA)
    return resized.astype(np.uint8)


# ------------------------------------------------------------------ #
#  Bimodal hotspot detection  (bright + dark)                          #
# ------------------------------------------------------------------ #

def _find_bimodal_hotspots(pixels: np.ndarray, count: int = 5):
    """
    Find the `count` brightest AND `count` darkest pixel positions using
    luminance (perceptually weighted grayscale), avoiding duplicates.

    Why bimodal?
    ─────────────
    Entropy comes from the FULL distribution of pixel values, not just
    the bright end.  Marking both extremes makes the randomness argument
    visually obvious to an examiner:

        • Yellow circles  →  high-luminance hotspots  (lots of light energy)
        • Cyan  circles   →  low-luminance  coldspots  (near-black regions)

    Both sets of pixels are already baked into the SHA-256 hash of the
    entire frame, so the circles are annotations, not the source of entropy.

    Perceptual luminance weights (ITU-R BT.601):
        L = 0.299·R + 0.587·G + 0.114·B
    """
    R = pixels[:, :, 0].astype(np.float32)
    G = pixels[:, :, 1].astype(np.float32)
    B = pixels[:, :, 2].astype(np.float32)
    lum = 0.299 * R + 0.587 * G + 0.114 * B   # shape: (H, W), float32

    flat = lum.ravel()
    h, w = lum.shape

    # Brightest pixels (descending)
    bright_idx = np.argsort(flat)[::-1]
    # Darkest pixels (ascending)
    dark_idx   = np.argsort(flat)

    bright_spots = []
    dark_spots   = []
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
    """Draw a hollow circle on pixels in-place using the midpoint algorithm."""
    h, w = pixels.shape[:2]
    color_arr = np.array(color, dtype=np.uint8)
    x, y, err = radius, 0, 0
    while x >= y:
        for dx, dy in [(x,y),(y,x),(-y,x),(-x,y),(-x,-y),(-y,-x),(y,-x),(x,-y)]:
            pr, pc = cy + dy, cx + dx
            if 0 <= pr < h and 0 <= pc < w:
                pixels[pr, pc] = color_arr
        y += 1
        err += 2 * y + 1
        if 2 * (err - x) + 1 > 0:
            x -= 1
            err += 1 - 2 * x


def _annotate_image(pixels: np.ndarray,
                    bright_spots: list,
                    dark_spots: list) -> np.ndarray:
    """
    Scale the captured image up to 960×720 so it is comfortable to view,
    then annotate:

        Yellow  rings  →  bright hotspots  (high luminance)
        Cyan    rings  →  dark  coldspots  (low  luminance)

    Three concentric rings per point give a visible halo without hiding
    the underlying pixel colour.
    """
    # Original size is 320×240; scale factor 3 → 960×720
    scale = 3
    big = np.repeat(np.repeat(pixels, scale, axis=0), scale, axis=1).copy()

    YELLOW = (255, 220,   0)   # bright hotspot colour
    CYAN   = (  0, 220, 255)   # dark  coldspot colour

    for row, col in bright_spots:
        cy = row * scale + scale // 2
        cx = col * scale + scale // 2
        for r, alpha_color in [(10, YELLOW), (11, (200, 170, 0)), (12, (150, 120, 0))]:
            _draw_circle(big, cy, cx, r, alpha_color)

    for row, col in dark_spots:
        cy = row * scale + scale // 2
        cx = col * scale + scale // 2
        for r, alpha_color in [(10, CYAN), (11, (0, 170, 200)), (12, (0, 120, 150))]:
            _draw_circle(big, cy, cx, r, alpha_color)

    return big


# ------------------------------------------------------------------ #
#  Public API                                                          #
# ------------------------------------------------------------------ #

def generate_entropy_image() -> tuple:
    """
    Full pipeline (called every 30 seconds by run_loop):

        1.  Capture a real webcam frame  (320 × 240 RGB)
        2.  Save raw frame  →  noise_frames/frame_<timestamp>.png
        3.  SHA-256 hash of raw pixel bytes  →  frame_hash
        4.  Find 5 brightest + 5 darkest pixels (bimodal hotspots)
        5.  Scale frame × 3 and annotate with yellow / cyan rings
        6.  Save annotated  →  actual_images/annotated_<timestamp>.png
        7.  Return (frame_hash, raw_path, annotated_path)

    The hash is derived from ALL pixels, not just the hotspot pixels.
    The circles are a visual proof-of-randomness aid, not the entropy source.
    """
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    # Step 1 & 2 — capture + save raw
    pixels = _capture_frame(width=320, height=240)
    raw_path = os.path.join(NOISE_DIR, f"frame_{timestamp}.png")
    _write_png(pixels, raw_path)

    # Step 3 — hash entire frame
    frame_hash = hashlib.sha256(pixels.tobytes()).hexdigest()

    # Step 4 — bimodal hotspot detection
    bright_spots, dark_spots = _find_bimodal_hotspots(pixels, count=5)

    # Step 5 & 6 — annotate and save
    annotated = _annotate_image(pixels, bright_spots, dark_spots)
    actual_path = os.path.join(ACTUAL_DIR, f"annotated_{timestamp}.png")
    _write_png(annotated, actual_path)

    return frame_hash, raw_path, actual_path


def get_latest_frame_hash() -> str:
    """
    Returns the SHA-256 hash of the most recently saved raw webcam frame.
    Called by code_generator.py to derive the time-window OTP.
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
    with open(latest_path, "rb") as f:
        raw = f.read()
    return hashlib.sha256(raw).hexdigest()


# ------------------------------------------------------------------ #
#  Run loop                                                            #
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
