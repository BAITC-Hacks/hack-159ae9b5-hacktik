from pathlib import Path
import os
import socket
import threading
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
# Minimal .env reader: values are literal, never executed or expanded.
if (ROOT / ".env").exists():
    for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() in {"OPENAI_API_KEY", "OPENAI_MODEL", "PUBLIC_ORIGIN", "EKT_DB_PATH"}:
            value = value.strip().strip('"').strip("'")
            if value:
                os.environ.setdefault(name.strip(), value)

def open_when_ready(port):
    url = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except OSError:
            time.sleep(.25)

if __name__ == "__main__":
    import uvicorn
    port = 8000
    while port < 8010:
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                break
            except OSError:
                port += 1
    if port == 8010:
        raise SystemExit("Ports 8000-8009 are occupied. Close an earlier EKT server.")
    threading.Thread(target=open_when_ready, args=(port,), daemon=True).start()
    print(f"EKT: http://127.0.0.1:{port} — keep this window open. Ctrl+C to stop.", flush=True)
    uvicorn.run("app.main:app", host="127.0.0.1", port=port)
