"""Build step (herdr runs it on `herdr plugin install`): create .venv and install deps.
Stdlib only, no uv needed. Safe to re-run."""
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
venv = root / ".venv"
py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
if sys.version_info < (3, 11):
    sys.exit(f"herdr-navigator needs Python 3.11+ (found {sys.version.split()[0]})")
if not py.exists():
    try:
        subprocess.check_call([sys.executable, "-m", "venv", str(venv)])
    except subprocess.CalledProcessError:
        sys.exit("herdr-navigator: could not create a virtual environment. On Debian/Ubuntu install "
                 "it first (sudo apt install python3-venv), then run `herdr plugin install` again.")
subprocess.check_call([str(py), "-m", "pip", "install", "-q", "--disable-pip-version-check",
                       "-r", str(root / "requirements.txt")])
print(f"herdr-navigator: environment ready ({py})")
