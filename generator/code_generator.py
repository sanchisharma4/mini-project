import hashlib
import hmac
import time
import os
import json

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")


def get_current_window() -> int:
    """Returns which 30-second window we are in right now."""
    return int(time.time()) // 30


def seconds_remaining() -> int:
    """How many seconds until the current code expires."""
    return 30 - (int(time.time()) % 30)


def generate_code(user_key: str) -> str:
    """
    Generates an 8-character uppercase hex code for a given user key.

    Steps:
      1. Import get_latest_frame_hash from entropy_image.py
      2. Get the current 30-second window number
      3. Mix both with the user key using HMAC-SHA256
      4. Return first 8 characters as uppercase hex

    Two users with different keys always get different codes.
    The same user gets the same code for a full 30-second window.
    """
    from entropy_image import get_latest_frame_hash
    frame_hash = get_latest_frame_hash()
    window     = get_current_window()

    message  = f"{frame_hash}:{window}".encode("utf-8")
    raw_hmac = hmac.new(
        key=user_key.encode("utf-8"),
        msg=message,
        digestmod=hashlib.sha256
    ).hexdigest()

    return raw_hmac[:8].upper()


def load_user_key() -> str:
    """
    Loads the user key from config.json (stored in the generator folder).
    If config.json does not exist, asks the user to create a key and
    saves it automatically.
    """
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            config = json.load(f)
        key = config.get("user_key", "").strip()
        if key:
            return key

    # First-time setup
    print("=" * 45)
    print("First time setup — create your personal key")
    print("=" * 45)
    print("Your key decides your code. Two users with")
    print("different keys never get the same code.")
    print()
    print("Pick anything — your name, a word, a phrase.")
    print("Example:  rahul42   or   mySecretKey")
    print()

    key = input("Enter your personal key: ").strip()
    if not key:
        raise ValueError("Key cannot be empty.")

    with open(CONFIG_PATH, "w") as f:
        json.dump({"user_key": key}, f, indent=2)

    print(f"\nSaved to: {CONFIG_PATH}")
    print("Keep this file safe. Do not share your key.\n")
    return key


if __name__ == "__main__":
    print("EntropyAuth — Code Generator")
    print("-" * 35)

    user_key = load_user_key()

    try:
        code = generate_code(user_key)
        secs = seconds_remaining()

        print(f"\nYour code  :  {code}")
        print(f"Expires in :  {secs} seconds")
        print(f"User key   :  {user_key}")
        print()
        print("Run again after 30 seconds to get a new code.")

    except FileNotFoundError as e:
        print(f"\nError: {e}")
