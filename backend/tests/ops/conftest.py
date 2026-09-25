import sys
from pathlib import Path

# The ops tools are standard-library-only scripts, not an importable package
# (they run with the server's system Python before any venv exists).
OPS_DIR = Path(__file__).resolve().parents[2] / "ops"
if str(OPS_DIR) not in sys.path:
    sys.path.insert(0, str(OPS_DIR))
