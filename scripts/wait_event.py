"""Block until something needs the orchestrator, then print one short line and exit.

Returns when: INBOX.md md5 differs from out/inbox.md5, or docs/LOG.md gains a line containing
"→ ОРКЕСТРАТОР" / "-> ОРКЕСТРАТОР" / "БЛОКЕР" / "КРИТИЧНО", or the timeout passes.
Usage: python scripts/wait_event.py [--timeout 900]
"""
import argparse
import hashlib
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INBOX, MD5, LOG = ROOT / "INBOX.md", ROOT / "out" / "inbox.md5", ROOT / "docs" / "LOG.md"
KEYS = ("→ ОРКЕСТРАТОР", "-> ОРКЕСТРАТОР", "→ оркестратор", "БЛОКЕР", "КРИТИЧНО")


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest() if p.exists() else ""


def main():
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()
    saved = MD5.read_text(encoding="utf-8").strip() if MD5.exists() else ""
    start_size = LOG.stat().st_size if LOG.exists() else 0
    end = time.time() + a.timeout
    while time.time() < end:
        if md5(INBOX) != saved:
            print(f"INBOX CHANGED {time.strftime('%H:%M')}")
            return
        if LOG.exists() and LOG.stat().st_size > start_size:
            with LOG.open("rb") as f:
                f.seek(start_size)
                new = f.read().decode("utf-8", "replace")
            hits = [ln[:300] for ln in new.splitlines() if any(k in ln for k in KEYS)]
            if hits:
                print(f"LOG EVENT {time.strftime('%H:%M')}")
                for ln in hits[-5:]:
                    print(ln)
                return
        time.sleep(20)
    print(f"TIMEOUT {time.strftime('%H:%M')}")


if __name__ == "__main__":
    main()
