import argparse
import csv
import json
import logging
import os
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import requests
import yaml

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

REGRESSION_THRESHOLD = 0.25  # 25% spec


def load_config(path: str = "config.yml") -> Dict[str, Any]:
    with open(path, 'r') as f:
        return yaml.safe_load(f)


def query_prometheus(prom_url: str, query: str) -> float:
    try:
        response = requests.get(f"{prom_url}/api/v1/query", params={'query': query}, timeout=10)
        data = response.json()
        if data.get('status') == 'success' and data.get('data', {}).get('result'):
            value = data['data']['result'][0]['value'][1]
            # NaN / +Inf from histogram_quantile show up as strings; coerce safely.
            try:
                f = float(value)
                return f if (f == f and f != float('inf') and f != float('-inf')) else 0.0
            except ValueError:
                return 0.0
        return 0.0
    except Exception as e:
        logger.error(f"Prometheus query failed for {query!r}: {e}")
        return 0.0


def render_query(template: str, client: str, window: str) -> str:
    return template.replace("{client}", client).replace("{window}", window)


def gather_prometheus_metrics(
    client: str,
    prom_url: str,
    queries: Dict[str, str],
    client_queries: Dict[str, str],
    window: str,
) -> Dict[str, float]:
    metrics: Dict[str, float] = {}
    for name, query_template in queries.items():
        metrics[name] = query_prometheus(prom_url, render_query(query_template, client, window))
    for name, query_template in client_queries.items():
        metrics[name] = query_prometheus(prom_url, render_query(query_template, client, window))
    return metrics


def parse_locust_stats(csv_path: str) -> Dict[str, Any]:
    """Parse Locust's *_stats.csv into per-method dicts + aggregate."""
    if not os.path.exists(csv_path):
        logger.warning(f"Locust stats CSV not found at {csv_path}")
        return {}

    out: Dict[str, Any] = {}
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get('Name', '')
            if not name:
                continue
            try:
                entry = {
                    "p50_ms": float(row['50%']),
                    "p95_ms": float(row['95%']),
                    "p99_ms": float(row['99%']),
                    "mean_ms": float(row['Average Response Time']),
                    "requests_per_sec": float(row['Requests/s']),
                    "request_count": int(row['Request Count']),
                    "failure_count": int(row['Failure Count']),
                    "avg_content_bytes": float(row.get('Average Content Size', 0) or 0),
                }
            except (KeyError, ValueError) as e:
                logger.warning(f"Could not parse Locust row {name}: {e}")
                continue
            if name == 'Aggregated':
                out['aggregate'] = entry
            else:
                out.setdefault('per_method', {})[name] = entry
    return out


def parse_proof_sizes(csv_path: str) -> Dict[str, float]:
    """Compute p50/p95/p99/mean from the proof-size CSV the locustfile writes."""
    if not os.path.exists(csv_path):
        return {}
    sizes: List[int] = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                sizes.append(int(row['response_bytes']))
            except (KeyError, ValueError):
                continue
    if not sizes:
        return {}
    sizes.sort()
    def pct(p: float) -> float:
        # statistics.quantiles is finicky for very small N; fall back to nearest-rank.
        idx = min(len(sizes) - 1, int(round(p * (len(sizes) - 1))))
        return float(sizes[idx])
    return {
        "p50_bytes": pct(0.50),
        "p95_bytes": pct(0.95),
        "p99_bytes": pct(0.99),
        "mean_bytes": statistics.fmean(sizes),
        "sample_count": len(sizes),
    }


def load_sync_metrics(milestone_dir: str, client: str) -> Dict[str, Any]:
    path = os.path.join(milestone_dir, f"sync_metrics_{client}.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Could not read {path}: {e}")
        return {}


def find_previous_milestone(client: str, current_milestone_dir: str) -> Optional[Dict[str, Any]]:
    """Walk sibling milestone directories under benchmarks/, return the most recent prior run for this client."""
    parent = os.path.dirname(os.path.abspath(current_milestone_dir))
    if not os.path.isdir(parent):
        return None
    current_name = os.path.basename(os.path.abspath(current_milestone_dir))

    candidates: List[Dict[str, Any]] = []
    for entry in os.listdir(parent):
        if entry == current_name:
            continue
        sibling = os.path.join(parent, entry)
        if not os.path.isdir(sibling):
            continue
        metrics_path = os.path.join(sibling, f"metrics_{client}.json")
        if not os.path.exists(metrics_path):
            continue
        try:
            with open(metrics_path) as f:
                payload = json.load(f)
            ts = payload.get("timestamp", "")
            candidates.append({"path": metrics_path, "milestone": entry, "timestamp": ts, "payload": payload})
        except Exception as e:
            logger.warning(f"Could not parse {metrics_path}: {e}")

    if not candidates:
        return None
    # Sort by timestamp (ISO-8601 lex-sorts correctly); fall back to dir name.
    candidates.sort(key=lambda c: (c["timestamp"], c["milestone"]))
    return candidates[-1]


def compare_against_previous(
    current: Dict[str, float],
    prev: Dict[str, Any],
    higher_is_better: Set[str],
):
    prev_milestone = prev.get("milestone", "<unknown>")
    prev_metrics = prev.get("payload", {}).get("metrics", {})
    if not prev_metrics:
        logger.info(f"Previous milestone {prev_milestone} has no metrics block; skipping comparison.")
        return

    logger.info(f"Comparing against previous milestone: {prev_milestone}")
    for key, curr_val in current.items():
        if key not in prev_metrics:
            continue
        prev_val = prev_metrics[key]
        if not isinstance(prev_val, (int, float)) or prev_val == 0:
            continue
        delta = (curr_val - prev_val) / prev_val
        # For higher-is-better metrics, a decrease is a regression.
        regressed = (delta < -REGRESSION_THRESHOLD) if key in higher_is_better else (delta > REGRESSION_THRESHOLD)
        if regressed:
            logger.warning(
                f"REGRESSION DETECTED! {key}: {prev_val} -> {curr_val} ({delta*100:+.2f}%)"
            )
        else:
            logger.info(f"{key}: {prev_val} -> {curr_val} ({delta*100:+.2f}%)")


def write_csv_summary(csv_path: str, client: str, state_size: Optional[int], metrics: Dict[str, Any]):
    """Flat per-metric CSV — the schema the spec asks for."""
    rows = []
    for name, value in metrics.items():
        if isinstance(value, (int, float)):
            rows.append({
                "client": client,
                "state_size": state_size if state_size is not None else "",
                "metric": name,
                "p50": "",
                "p95": "",
                "p99": "",
                "mean": value,
            })
    # Locust per-method rows go in with their actual percentiles.
    locust_per_method = metrics.get("rpc", {}).get("per_method", {}) if isinstance(metrics.get("rpc"), dict) else {}
    for method, stats in locust_per_method.items():
        rows.append({
            "client": client,
            "state_size": state_size if state_size is not None else "",
            "metric": f"rpc:{method}:latency_ms",
            "p50": stats.get("p50_ms", ""),
            "p95": stats.get("p95_ms", ""),
            "p99": stats.get("p99_ms", ""),
            "mean": stats.get("mean_ms", ""),
        })
    # Proof size percentiles if present.
    proof = metrics.get("proof_sizes")
    if isinstance(proof, dict) and proof:
        rows.append({
            "client": client,
            "state_size": state_size if state_size is not None else "",
            "metric": "eth_getProof:response_bytes",
            "p50": proof.get("p50_bytes", ""),
            "p95": proof.get("p95_bytes", ""),
            "p99": proof.get("p99_bytes", ""),
            "mean": proof.get("mean_bytes", ""),
        })

    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=["client", "state_size", "metric", "p50", "p95", "p99", "mean"])
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Metrics Aggregator")
    parser.add_argument("--config", type=str, default="config.yml")
    parser.add_argument("--client", type=str, required=True)
    parser.add_argument("--milestone", type=str, required=True)
    args = parser.parse_args()

    config = load_config(args.config)
    metrics_cfg = config.get("metrics", {})
    prom_url = metrics_cfg.get("prometheus_url", "http://localhost:9090")
    cross_queries = metrics_cfg.get("queries", {})
    client_queries = metrics_cfg.get("client_queries", {}).get(args.client, {})
    load_window = metrics_cfg.get("load_window", "5m")
    higher_is_better = set(metrics_cfg.get("higher_is_better", []))

    milestone_dir = os.path.join("benchmarks", args.milestone)
    os.makedirs(milestone_dir, exist_ok=True)
    client_dir = os.path.join(milestone_dir, args.client)

    # 1. Prometheus metrics over the load window.
    flat_metrics = gather_prometheus_metrics(args.client, prom_url, cross_queries, client_queries, load_window)

    # 2. Runner-supplied sync/db measurements.
    sync = load_sync_metrics(milestone_dir, args.client)
    if sync.get("sync_time_sec") is not None:
        flat_metrics["sync_time_sec"] = float(sync["sync_time_sec"])
    state_size = sync.get("db_size_bytes")

    # 3. Locust stats.
    locust_csv = os.path.join(client_dir, "locust_stats_stats.csv")
    rpc = parse_locust_stats(locust_csv)
    if rpc.get("aggregate"):
        agg = rpc["aggregate"]
        flat_metrics["rpc_p50_latency_ms"] = agg["p50_ms"]
        flat_metrics["rpc_p95_latency_ms"] = agg["p95_ms"]
        flat_metrics["rpc_p99_latency_ms"] = agg["p99_ms"]
        flat_metrics["rpc_mean_latency_ms"] = agg["mean_ms"]
        flat_metrics["rpc_requests_sec"] = agg["requests_per_sec"]

    # 4. Proof sizes.
    proof = parse_proof_sizes(os.path.join(client_dir, "proof_sizes.csv"))
    if proof:
        flat_metrics["proof_p50_bytes"] = proof["p50_bytes"]
        flat_metrics["proof_p95_bytes"] = proof["p95_bytes"]
        flat_metrics["proof_p99_bytes"] = proof["p99_bytes"]

    # 5. Assemble output payload.
    payload = {
        "client": args.client,
        "milestone": args.milestone,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "state_size_bytes": state_size,
        "metrics": flat_metrics,
        "rpc": rpc,
        "proof_sizes": proof,
    }

    json_path = os.path.join(milestone_dir, f"metrics_{args.client}.json")
    with open(json_path, 'w') as f:
        json.dump(payload, f, indent=2)
    logger.info(f"Saved metrics to {json_path}")

    csv_path = os.path.join(milestone_dir, f"metrics_{args.client}.csv")
    write_csv_summary(csv_path, args.client, state_size, {**payload["metrics"], "rpc": rpc, "proof_sizes": proof})
    logger.info(f"Saved CSV summary to {csv_path}")

    # 6. Cross-milestone regression check.
    prev = find_previous_milestone(args.client, milestone_dir)
    if prev is None:
        logger.info("No previous milestones found for comparison.")
    else:
        compare_against_previous(flat_metrics, prev, higher_is_better)


if __name__ == "__main__":
    main()
