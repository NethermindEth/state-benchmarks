"""Make src/ modules importable from tests without packaging gymnastics."""
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).parent.parent
_SRC = _REPO_ROOT / "src"

for sub in ("metrics", "orchestrator", "load_tests", "mock_cl"):
    p = str(_SRC / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

# src/ itself so the `mock_cl` package (and `python -m mock_cl`) resolves too.
_SRC_STR = str(_SRC)
if _SRC_STR not in sys.path:
    sys.path.insert(0, _SRC_STR)

# Expose REPO_ROOT for integration tests that drive scripts via subprocess.
REPO_ROOT = _REPO_ROOT
