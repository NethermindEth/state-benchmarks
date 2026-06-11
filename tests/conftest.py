"""Make src/ modules importable from tests without packaging gymnastics."""
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).parent.parent
_SRC = _REPO_ROOT / "src"

for sub in ("metrics", "orchestrator", "load_tests"):
    p = str(_SRC / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

# Expose REPO_ROOT for integration tests that drive scripts via subprocess.
REPO_ROOT = _REPO_ROOT
