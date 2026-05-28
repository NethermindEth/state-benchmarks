import argparse
import requests
import json
import csv
import os
import logging
import yaml
from typing import Dict, Any

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def load_config(path: str = "config.yml") -> Dict[str, Any]:
    with open(path, 'r') as f:
        return yaml.safe_load(f)

CONFIG: Dict[str, Any] = {}
PROMETHEUS_URL = "http://localhost:9090"
METRICS_QUERIES: Dict[str, str] = {}

def query_prometheus(query: str) -> float:
    try:
        response = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={'query': query})
        data = response.json()
        if data.get('status') == 'success' and data.get('data', {}).get('result'):
            return float(data['data']['result'][0]['value'][1])
        return 0.0
    except Exception as e:
        logger.error(f"Prometheus query failed for {query}: {e}")
        return 0.0

def gather_metrics(client: str) -> Dict[str, float]:
    logger.info(f"Gathering metrics for {client} from Prometheus...")
    
    metrics = {}
    for name, query_template in METRICS_QUERIES.items():
        query = query_template.replace("{client}", client)
        metrics[name] = query_prometheus(query)
        
    return metrics

def compare_previous_milestone(current_metrics: Dict[str, Any], milestone: str, output_dir: str):
    logger.info("Checking for regressions against previous milestones...")
    files = [f for f in os.listdir(output_dir) if f.endswith(".json") and f != f"metrics_{milestone}.json"]
    if not files:
        logger.info("No previous milestones found for comparison.")
        return
        
    prev_file = sorted(files)[-1]
    with open(os.path.join(output_dir, prev_file), 'r') as f:
        prev_metrics = json.load(f)
        
    for key, curr_val in current_metrics.items():
        if key in prev_metrics:
            prev_val = prev_metrics[key]
            if prev_val > 0:
                degradation = (curr_val - prev_val) / prev_val
                if degradation > 0.25:
                    logger.warning(f"REGRESSION DETECTED! {key} degraded by {degradation*100:.2f}% (was {prev_val}, now {curr_val})")
                else:
                    logger.info(f"{key}: {curr_val} (vs {prev_val}, diff: {degradation*100:.2f}%)")

def main():
    parser = argparse.ArgumentParser(description="Metrics Aggregator")
    parser.add_argument("--config", type=str, default="config.yml")
    parser.add_argument("--client", type=str, required=True)
    parser.add_argument("--milestone", type=str, required=True)
    args = parser.parse_args()

    global CONFIG, PROMETHEUS_URL, METRICS_QUERIES
    CONFIG = load_config(args.config)
    PROMETHEUS_URL = CONFIG.get("metrics", {}).get("prometheus_url", "http://localhost:9090")
    METRICS_QUERIES = CONFIG.get("metrics", {}).get("queries", {})
    
    output_dir = f"benchmarks/{args.milestone}"
    os.makedirs(output_dir, exist_ok=True)
    
    metrics = gather_metrics(args.client)
    
    locust_stats_file = "benchmarks/locust_stats_stats.csv"
    if os.path.exists(locust_stats_file):
        with open(locust_stats_file, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['Name'] == 'Aggregated':
                    metrics['rpc_p50_latency'] = float(row['50%'])
                    metrics['rpc_p95_latency'] = float(row['95%'])
                    metrics['rpc_p99_latency'] = float(row['99%'])
                    metrics['rpc_requests_sec'] = float(row['Requests/s'])
    
    json_path = os.path.join(output_dir, f"metrics_{args.client}.json")
    with open(json_path, 'w') as f:
        json.dump(metrics, f, indent=4)
    logger.info(f"Saved metrics to {json_path}")
    
    compare_previous_milestone(metrics, args.milestone, output_dir)

if __name__ == "__main__":
    main()
