import json
import time
import urllib.request
import urllib.error

URLS_TO_CHECK = [
    "https://example.com",
    "https://api.github.com",
]
# Paste a Discord or Slack incoming webhook URL here (leave blank to print to console only)
WEBHOOK_URL = ""
TIMEOUT_SECONDS = 5

def send_alert(message: str):
    print(f"[ALERT] {message}")
    if not WEBHOOK_URL:
        return
    payload = json.dumps({"content": message, "text": message}).encode("utf-8")
    req = urllib.request.Request(
        WEBHOOK_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"Failed to send webhook: {e}")

def check_endpoints():
    for url in URLS_TO_CHECK:
        start = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": "UptimeBot/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
                elapsed_ms = (time.time() - start) * 1000
                status = response.getcode()
                if status >= 400:
                    send_alert(f"{url} returned HTTP {status}")
                else:
                    print(f"[OK] {url} - {status} ({elapsed_ms:.0f} ms)")
        except urllib.error.URLError as e:
            send_alert(f"{url} is unreachable! Reason: {e.reason}")
        except Exception as e:
            send_alert(f"{url} check failed: {e}")

if __name__ == "__main__":
    check_endpoints()
    