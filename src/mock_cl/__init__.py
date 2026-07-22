"""Mock consensus-layer (CL) Engine-API driver for FROZEN milestone snapshots.

A post-merge execution-layer (EL) client (Geth, Besu, Reth, Erigon, Nethermind)
will only snap-sync or execute blocks when a consensus client drives its Engine
API. Frozen milestone snapshots in this project have no live CL, so this package
provides the two minimal CL functions GitHub issue #21 needs:

  * :class:`PivotDriver` — repeatedly sends ``engine_forkchoiceUpdatedV3`` with a
    fixed frozen pivot hash so the EL snap-syncs to that state (snap-sync mode).
  * :class:`ReplayDriver` — replays recorded ``engine_newPayload`` /
    ``engine_forkchoiceUpdated`` pairs to drive block execution under a live head
    and measure per-block processing latency (replay mode).

For Nethermind targets, ``--Sync.StaticSnapPivot`` (nethermind#11943) is the
native alternative to pivot mode.
"""
try:  # package-relative (python -m mock_cl) and bare (tests with src/mock_cl on path)
    from .drivers import PivotDriver, ReplayDriver
    from .engine import EngineClient
    from .payloads import load_payloads
except ImportError:  # pragma: no cover - fallback for flat sys.path layout
    from drivers import PivotDriver, ReplayDriver
    from engine import EngineClient
    from payloads import load_payloads

__all__ = ["EngineClient", "PivotDriver", "ReplayDriver", "load_payloads"]
