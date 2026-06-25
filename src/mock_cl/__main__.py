"""CLI entry point: ``python -m mock_cl <pivot|replay|record> ...``.

Builds an :class:`EngineClient` from a jwt secret + engine URL and runs one of
the three mock-CL operations, printing a JSON summary to stdout.
"""
import argparse
import json
import logging
import signal
import sys
import threading

try:
    from .drivers import PivotDriver, ReplayDriver
    from .engine import EngineClient
    from .jwt_auth import load_secret
    from .payloads import load_payloads, record_from_el
except ImportError:  # pragma: no cover - flat sys.path layout
    from drivers import PivotDriver, ReplayDriver
    from engine import EngineClient
    from jwt_auth import load_secret
    from payloads import load_payloads, record_from_el

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mock_cl", description="Mock consensus-layer Engine-API driver for frozen snapshots"
    )
    parser.add_argument("--engine-url", default="http://localhost:8551", help="Engine API URL")
    parser.add_argument("--jwt", required=True, help="Path to jwt.hex or a 0x/hex secret string")

    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_pivot = sub.add_parser("pivot", help="Snap-sync: drive the EL to a frozen pivot")
    p_pivot.add_argument("--pivot-hash", required=True, help="Frozen pivot block hash (0x...)")
    p_pivot.add_argument("--pivot-number", type=int, default=None, help="Frozen pivot block number")
    p_pivot.add_argument("--interval", type=int, default=12, help="Seconds between FCU ticks")
    p_pivot.add_argument("--version", type=int, default=3, help="forkchoiceUpdated version (1..3)")
    p_pivot.add_argument("--status-rpc", default=None, help="EL JSON-RPC URL to poll for sync done")
    p_pivot.add_argument("--max-seconds", type=int, default=None, help="Stop after this many seconds")

    p_replay = sub.add_parser("replay", help="Execution: replay recorded payloads under live head")
    p_replay.add_argument("--payloads", required=True, help="JSONL payloads file")
    p_replay.add_argument("--count", type=int, default=None, help="Replay at most this many blocks")
    p_replay.add_argument("--no-advance-head", action="store_true", help="Skip the FCU after each payload")
    p_replay.add_argument("--latency-csv", default=None, help="Write per-block latency rows here")
    p_replay.add_argument("--newpayload-version", type=int, default=None,
                          help="Force engine_newPayload version for all records (e.g. 4 for Prague); "
                               "default: per-record/auto")

    p_record = sub.add_parser("record", help="Record blocks from a live EL into a JSONL payload file")
    p_record.add_argument("--source-rpc", required=True, help="Source EL JSON-RPC URL")
    p_record.add_argument("--start", type=int, required=True, help="First block number")
    p_record.add_argument("--count", type=int, required=True, help="Number of blocks to record")
    p_record.add_argument("--out", required=True, help="Output JSONL path")

    return parser


def _run_pivot(args, engine: EngineClient) -> dict:
    driver = PivotDriver(
        engine,
        pivot_hash=args.pivot_hash,
        interval=args.interval,
        version=args.version,
        status_rpc_url=args.status_rpc,
        pivot_number=args.pivot_number,
    )
    stop_event = threading.Event()

    def _handle_sigint(_signum, _frame):
        logger.info("Received interrupt; stopping pivot driver...")
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_sigint)
    return driver.run(stop_event=stop_event, max_seconds=args.max_seconds)


def _run_replay(args, engine: EngineClient) -> dict:
    records = load_payloads(args.payloads)
    driver = ReplayDriver(
        engine,
        records,
        advance_head=not args.no_advance_head,
        count=args.count,
        latency_csv=args.latency_csv,
        newpayload_version=args.newpayload_version,
    )
    return driver.run()


def _run_record(args) -> dict:
    written = record_from_el(args.source_rpc, args.start, args.count, args.out)
    return {"recorded": written, "out": args.out}


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    if args.subcommand == "record":
        summary = _run_record(args)
    else:
        secret = load_secret(args.jwt)
        engine = EngineClient(args.engine_url, secret)
        if args.subcommand == "pivot":
            summary = _run_pivot(args, engine)
        else:
            summary = _run_replay(args, engine)

    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
