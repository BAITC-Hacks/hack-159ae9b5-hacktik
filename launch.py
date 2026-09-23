"""Bootstrap a private environment, then open the local application."""
from pathlib import Path
import hashlib
import os
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11 or newer is required.")
ENV = ROOT / ".venv"
PYTHON = ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
if not PYTHON.exists():
    print("Creating Python environment...", flush=True)
    venv.create(ENV, with_pip=True)
requirements = ROOT / "requirements.txt"
digest = hashlib.sha256((requirements.read_bytes() + (ROOT / "requirements-tested.txt").read_bytes())).hexdigest()
marker = ENV / "ekt-requirements.sha256"
if not marker.exists() or marker.read_text() != digest:
    print("Installing dependencies (internet required on first run)...", flush=True)
    subprocess.run([str(PYTHON), "-m", "pip", "install", "-r", str(requirements)], check=True)
    marker.write_text(digest)
subprocess.run([str(PYTHON), str(ROOT / "run.py")], check=True)
