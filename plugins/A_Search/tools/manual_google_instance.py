import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = Path(os.getenv("GOOGLE_USER_DATA_DIR", ROOT / ".cache" / "google-automation-profile"))
PROFILE_DIR.mkdir(parents=True, exist_ok=True)
PROXY = os.getenv("GOOGLE_PROXY", "system").strip()


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


with sync_playwright() as playwright:
    args = [
        "--lang=zh-CN",
        "--disable-blink-features=AutomationControlled",
        "--remote-debugging-port=9223",
    ]
    if env_bool("GOOGLE_DISABLE_QUIC", True):
        args.append("--disable-quic")

    launch_options = {
        "headless": False,
        "locale": "zh-CN",
        "viewport": {"width": 1365, "height": 900},
        "args": args,
    }

    proxy_mode = PROXY.lower()
    if proxy_mode in {"none", "direct"}:
        args.append("--no-proxy-server")
    elif proxy_mode and proxy_mode != "system":
        launch_options["proxy"] = {"server": PROXY}

    context = playwright.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        **launch_options,
    )
    page = context.pages[0] if context.pages else context.new_page()
    page.goto("https://lens.google.com/?hl=zh-CN", wait_until="domcontentloaded")

    print(
        f"Manual Google Lens instance is running on CDP port 9223. GOOGLE_PROXY={PROXY or 'system'}",
        flush=True,
    )
    try:
        while True:
            time.sleep(1)
    finally:
        context.close()
