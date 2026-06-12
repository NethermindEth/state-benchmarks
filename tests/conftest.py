"""Make src/ modules importable from tests without packaging gymnastics."""
# Importing locust (tests/unit/test_locustfile.py) runs gevent's
# monkey.patch_all(); doing that after another test module has imported
# requests/urllib3 raises RecursionError (gevent#1016). Patch first, before
# any test module import.
from gevent import monkey

monkey.patch_all()

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
