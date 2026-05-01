"""
EntropyAuth — visualize_entropy.py

Run this alongside generate_code.py (in a second terminal) to save visual
proof of every capture for your college demo.

What it produces every 30 seconds
──────────────────────────────────
  generator/actual_images/
      frame_<timestamp>.jpg      — the raw webcam photo, exactly as captured

  generator/noise_frames/
      frame_<timestamp>.jpg      — the same photo with an overlay that shows:
                                     • a grid marking every pixel block
                                     • the SHA-256 byte-scan direction (arrow)
                                     • top-5 brightest pixels  (cyan rings)
                                     • top-5 darkest pixels    (magenta rings)
                                     • the resulting hash burned into the image
                                     • the OTP code burned into the image

Usage
──────
    python visualize_entropy.py           # webcam, loops forever
    python visualize_entropy.py photo.jpg # fixed image, loops forever

Requirements: opencv-python, numpy  (pip install opencv-python numpy)
              Same config.json as generate_code.py
"""

import os, sys, json, time, hmac, hashlib, math
import cv2
import numpy as np

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH   = os.path.join(BASE_DIR, "config.json")
ACTUAL_DIR    = os.path.join(BASE_DIR, "actual_images")
NOISE_DIR     = os.path.join(BASE_DIR, "noise_frames")

os.makedirs(ACTUAL_DIR, exist_ok=True)
os.makedirs(NOISE_DIR,  exist_ok=True)

# ── Constants ──────────────────────────────────────────────────────────────────
IMG_W, IMG_H = 320, 240
INTERVAL     = 30

# Colours (BGR for OpenCV)
CYAN    = (255, 220,  50)   # bright cyan-ish yellow — visible on any bg
MAGENTA = ( 60,  20, 220)   # red-magenta
WHITE   = (255, 255, 255)
BLACK   = (  0,   0,   0)
GREEN   = ( 60, 220,  60)
GREY    = (180, 180, 180)
OVERLAY = ( 20,  20,  20)   # near-black for text bars


# ── Config ─────────────────────────────────────────────────────────────────────

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


# ── Capture ────────────────────────────────────────────────────────────────────

def capture_webcam():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise RuntimeError("No webcam found.")
    for _ in range(5):
        cap.read()
    ret, frame = cap.read()
    cap.release()
    if not ret or frame is None:
        raise RuntimeError("Webcam opened but failed to read a frame.")
    return cv2.resize(frame, (IMG_W, IMG_H), interpolation=cv2.INTER_AREA)

def load_image(path):
    img = cv2.imread(path)
    if img is None:
        raise RuntimeError(f"Cannot open image: {path}")
    return cv2.resize(img, (IMG_W, IMG_H), interpolation=cv2.INTER_AREA)


# ── Hash ───────────────────────────────────────────────────────────────────────

def hash_frame(bgr_frame):
    """Convert BGR to RGB (same as generate_code.py), then SHA-256 the bytes."""
    rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB).astype(np.uint8)
    return hashlib.sha256(rgb.tobytes()).hexdigest()

def make_code(chaos_seed, frame_hash, window):
    msg = f"{frame_hash}:{window}".encode()
    return hmac.new(chaos_seed.encode(), msg, hashlib.sha256).hexdigest()[:8].upper()

def secs_left():
    return INTERVAL - (int(time.time()) % INTERVAL)


# ── Save actual frame ──────────────────────────────────────────────────────────

def save_actual(bgr_frame, timestamp):
    path = os.path.join(ACTUAL_DIR, f"frame_{timestamp}.jpg")
    cv2.imwrite(path, bgr_frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return path


# ── Build annotated noise frame ────────────────────────────────────────────────

def save_noise_frame(bgr_frame, frame_hash, otp_code, window, timestamp):
    """
    Draws the full annotation overlay on a copy of the frame:

      1. Pixel-scan grid  — thin grey lines every 16px show the raster order
         SHA-256 reads raw bytes left→right, top→bottom
      2. Scan-direction arrow  — white arrow across the top row
      3. Brightest-5 pixels  — cyan circle + dot
      4. Darkest-5 pixels    — magenta circle + dot
      5. Hash snippet bar    — bottom strip with first 32 hex chars
      6. OTP code bar        — top strip with the resulting 8-char code
      7. Legend              — small key bottom-right
    """
    vis = bgr_frame.copy()

    # ── Convert to greyscale for luminance analysis ───────────────────────────
    gray = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
    flat = gray.flatten()

    # ── 1. Pixel-scan grid ────────────────────────────────────────────────────
    GRID = 16   # one grid cell = 16×16 pixels
    for x in range(0, IMG_W, GRID):
        cv2.line(vis, (x, 0), (x, IMG_H), (50, 50, 50), 1)
    for y in range(0, IMG_H, GRID):
        cv2.line(vis, (0, y), (IMG_W, y), (50, 50, 50), 1)

    # ── 2. Scan-direction arrow (top row, shows bytes read L→R T→B) ──────────
    arrow_y = 8
    cv2.arrowedLine(vis, (8, arrow_y), (IMG_W - 8, arrow_y),
                    WHITE, 1, tipLength=0.03)
    cv2.putText(vis, "SHA-256 byte scan", (10, arrow_y - 1),
                cv2.FONT_HERSHEY_PLAIN, 0.7, BLACK, 2, cv2.LINE_AA)
    cv2.putText(vis, "SHA-256 byte scan", (10, arrow_y - 1),
                cv2.FONT_HERSHEY_PLAIN, 0.7, WHITE, 1, cv2.LINE_AA)

    # ── 3. Brightest pixels (top 5 by luminance) — CYAN rings ────────────────
    bright_idx = np.argsort(flat)[-5:][::-1]   # descending
    for rank, idx in enumerate(bright_idx):
        py, px = divmod(int(idx), IMG_W)
        # outer ring
        cv2.circle(vis, (px, py), 9, CYAN, 1, cv2.LINE_AA)
        # centre dot
        cv2.circle(vis, (px, py), 2, CYAN, -1, cv2.LINE_AA)
        # rank label
        cv2.putText(vis, str(rank + 1), (px + 6, py - 6),
                    cv2.FONT_HERSHEY_PLAIN, 0.6, CYAN, 1, cv2.LINE_AA)

    # ── 4. Darkest pixels (bottom 5 by luminance) — MAGENTA rings ────────────
    dark_idx = np.argsort(flat)[:5]            # ascending
    for rank, idx in enumerate(dark_idx):
        py, px = divmod(int(idx), IMG_W)
        cv2.circle(vis, (px, py), 9, MAGENTA, 1, cv2.LINE_AA)
        cv2.circle(vis, (px, py), 2, MAGENTA, -1, cv2.LINE_AA)
        cv2.putText(vis, str(rank + 1), (px + 6, py - 6),
                    cv2.FONT_HERSHEY_PLAIN, 0.6, MAGENTA, 1, cv2.LINE_AA)

    # ── 5. Hash bar (bottom strip) ────────────────────────────────────────────
    bar_h = 28
    hash_bar = np.zeros((bar_h, IMG_W, 3), dtype=np.uint8)
    hash_bar[:] = OVERLAY
    # colour each hex nibble pair (= 1 byte) with a unique hue
    n_bytes_shown = min(32, IMG_W // 10)
    for i in range(n_bytes_shown):
        byte_val = int(frame_hash[i*2: i*2+2], 16)
        hue = int(byte_val / 255 * 179)   # OpenCV hue: 0-179
        hsv_pixel = np.uint8([[[hue, 200, 220]]])
        bgr_col   = cv2.cvtColor(hsv_pixel, cv2.COLOR_HSV2BGR)[0][0].tolist()
        x_pos = i * 10
        cv2.putText(hash_bar, frame_hash[i*2: i*2+2],
                    (x_pos, 19), cv2.FONT_HERSHEY_PLAIN, 0.75,
                    tuple(bgr_col), 1, cv2.LINE_AA)
    # label
    cv2.putText(hash_bar, "SHA-256:", (n_bytes_shown * 10 + 4, 19),
                cv2.FONT_HERSHEY_PLAIN, 0.7, GREY, 1, cv2.LINE_AA)

    # ── 6. OTP code bar (top strip) ───────────────────────────────────────────
    otp_bar = np.zeros((32, IMG_W, 3), dtype=np.uint8)
    otp_bar[:] = OVERLAY
    # large centred code
    text_size = cv2.getTextSize(otp_code, cv2.FONT_HERSHEY_DUPLEX, 0.9, 1)[0]
    tx = (IMG_W - text_size[0]) // 2
    cv2.putText(otp_bar, otp_code, (tx, 24),
                cv2.FONT_HERSHEY_DUPLEX, 0.9, GREEN, 1, cv2.LINE_AA)
    # label
    cv2.putText(otp_bar, f"OTP  win={window}",
                (4, 24), cv2.FONT_HERSHEY_PLAIN, 0.7, GREY, 1, cv2.LINE_AA)

    # ── 7. Legend (bottom-right corner on the image itself) ───────────────────
    leg_x, leg_y = IMG_W - 110, IMG_H - 55
    cv2.rectangle(vis, (leg_x - 4, leg_y - 12),
                  (IMG_W - 2, IMG_H - 2), OVERLAY, -1)
    cv2.circle(vis, (leg_x + 6, leg_y), 5, CYAN, 1, cv2.LINE_AA)
    cv2.putText(vis, "brightest px", (leg_x + 14, leg_y + 4),
                cv2.FONT_HERSHEY_PLAIN, 0.65, CYAN, 1, cv2.LINE_AA)
    cv2.circle(vis, (leg_x + 6, leg_y + 18), 5, MAGENTA, 1, cv2.LINE_AA)
    cv2.putText(vis, "darkest px", (leg_x + 14, leg_y + 22),
                cv2.FONT_HERSHEY_PLAIN, 0.65, MAGENTA, 1, cv2.LINE_AA)
    cv2.line(vis, (leg_x, leg_y + 34), (leg_x + 12, leg_y + 34),
             (50, 50, 50), 1)
    cv2.putText(vis, "scan grid (16px)", (leg_x + 14, leg_y + 38),
                cv2.FONT_HERSHEY_PLAIN, 0.65, GREY, 1, cv2.LINE_AA)

    # ── Composite: stack otp_bar / vis / hash_bar vertically ─────────────────
    composite = np.vstack([otp_bar, vis, hash_bar])

    path = os.path.join(NOISE_DIR, f"frame_{timestamp}.jpg")
    cv2.imwrite(path, composite, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return path


# ── Terminal colours ───────────────────────────────────────────────────────────

TCYAN  = "\033[96m"
TGREEN = "\033[92m"
TDIM   = "\033[2m"
TBOLD  = "\033[1m"
TRESET = "\033[0m"
TLINE  = "─" * 56

def clear_line():
    print("\r" + " " * 70 + "\r", end="", flush=True)


# ── Main loop ──────────────────────────────────────────────────────────────────

def main():
    cfg        = load_config()
    chaos_seed = cfg.get("chaos_seed", "")
    email      = cfg.get("email", "unknown")

    if not chaos_seed:
        print("\n  [error] chaos_seed not found in config.json.")
        print("  Run generate_code.py --setup first, then start generate_code.py")
        print("  so it registers and caches the seed.\n")
        sys.exit(1)

    image_path = sys.argv[1] if len(sys.argv) > 1 else None

    print(f"\n{TLINE}")
    print(f"  {TBOLD}{TCYAN}ENTROPY AUTH  —  Frame Visualizer{TRESET}  {TDIM}(Ctrl+C to stop){TRESET}")
    print(f"  Saving to:  actual_images/   noise_frames/")
    print(f"  Capture every {INTERVAL}s")
    print(TLINE)

    cycle = 0
    while True:
        cycle += 1
        ts = time.strftime("%Y%m%d_%H%M%S")

        # ── Capture ───────────────────────────────────────────────────────────
        try:
            bgr = capture_webcam() if not image_path else load_image(image_path)
        except RuntimeError as e:
            print(f"\n  [error] {e}\n")
            sys.exit(1)

        # ── Hash + code ───────────────────────────────────────────────────────
        frame_hash = hash_frame(bgr)
        window     = int(time.time()) // 30
        otp_code   = make_code(chaos_seed, frame_hash, window)

        # ── Save files ────────────────────────────────────────────────────────
        actual_path = save_actual(bgr, ts)
        noise_path  = save_noise_frame(bgr, frame_hash, otp_code, window, ts)

        # ── Terminal output ───────────────────────────────────────────────────
        clear_line()
        print(f"\n{TLINE}")
        print(f"  {TBOLD}Cycle #{cycle}{TRESET}  {TDIM}{time.strftime('%H:%M:%S')}  window {window}{TRESET}")
        print(TLINE)
        print(f"  Hash    {TDIM}{frame_hash[:32]}…{TRESET}")
        print(f"  Code    {TCYAN}{TBOLD}{otp_code}{TRESET}")
        print(f"  Actual  {TDIM}{os.path.relpath(actual_path, BASE_DIR)}{TRESET}")
        print(f"  Noise   {TDIM}{os.path.relpath(noise_path, BASE_DIR)}{TRESET}")
        print(TLINE)
        print(f"  {TDIM}Next capture in {secs_left()}s{TRESET}", end="", flush=True)

        # ── Wait ──────────────────────────────────────────────────────────────
        while secs_left() > 1:
            time.sleep(1)
            clear_line()
            print(f"  {TDIM}Next capture in {secs_left()}s{TRESET}", end="", flush=True)

        clear_line()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n  {TDIM}Stopped.{TRESET}\n")
