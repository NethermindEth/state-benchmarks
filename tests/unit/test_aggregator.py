"""Unit tests for src/metrics/aggregator.py — pure functions, no Docker, no network."""
import csv
import io
import json
import logging
import os
from unittest.mock import MagicMock, patch

import pytest

import aggregator


# ---------- render_query ----------

def test_render_query_substitutes_tokens():
    out = aggregator.render_query(
        'rate(x{name="benchmark_{client}"}[{window}])', "nethermind", "5m"
    )
    assert out == 'rate(x{name="benchmark_nethermind"}[5m])'


def test_render_query_handles_no_tokens():
    assert aggregator.render_query("up", "anything", "anything") == "up"


# ---------- query_prometheus ----------

def _prom_response(value):
    """Construct a fake Prometheus instant-query response."""
    resp = MagicMock()
    resp.json.return_value = {
        "status": "success",
        "data": {"result": [{"value": [1700000000, value]}]},
    }
    return resp


@patch("aggregator.requests.get")
def test_query_prometheus_returns_float(mock_get):
    mock_get.return_value = _prom_response("12.5")
    assert aggregator.query_prometheus("http://prom", "up") == 12.5


@patch("aggregator.requests.get")
def test_query_prometheus_handles_nan(mock_get):
    """Unavailable values must be None, never a fabricated 0.0."""
    mock_get.return_value = _prom_response("NaN")
    assert aggregator.query_prometheus("http://prom", "up") is None


@patch("aggregator.requests.get")
def test_query_prometheus_handles_inf(mock_get):
    mock_get.return_value = _prom_response("+Inf")
    assert aggregator.query_prometheus("http://prom", "up") is None


@patch("aggregator.requests.get")
def test_query_prometheus_handles_empty_result(mock_get):
    resp = MagicMock()
    resp.json.return_value = {"status": "success", "data": {"result": []}}
    mock_get.return_value = resp
    assert aggregator.query_prometheus("http://prom", "up") is None


@patch("aggregator.requests.get", side_effect=ConnectionError("boom"))
def test_query_prometheus_handles_connection_error(mock_get):
    assert aggregator.query_prometheus("http://prom", "up") is None


# ---------- gather_prometheus_metrics ----------

@patch("aggregator.query_prometheus")
def test_gather_omits_unavailable_metrics(mock_query):
    """Failed queries are omitted from the result (not recorded as zeros) and
    reported by name in the missing-metrics manifest."""
    mock_query.side_effect = [42.0, None, 7.0]
    out, missing = aggregator.gather_prometheus_metrics(
        "geth", "http://prom",
        queries={"cpu": "q1", "rss": "q2"},
        client_queries={"gas_per_sec": "q3"},
        window="5m",
    )
    assert out == {"cpu": 42.0, "gas_per_sec": 7.0}
    assert "rss" not in out
    assert missing == ["rss"]


# ---------- locust_aggregate_metrics (failure-ratio gate) ----------

def _agg_row(request_count=1000, failure_count=0):
    return {
        "p50_ms": 2.0, "p95_ms": 4.0, "p99_ms": 7.0, "mean_ms": 2.2,
        "requests_per_sec": 33.0,
        "request_count": request_count, "failure_count": failure_count,
    }


def test_locust_aggregate_metrics_healthy_run_passes():
    metrics, valid = aggregator.locust_aggregate_metrics(_agg_row(failure_count=10), 0.1)
    assert valid is True
    assert metrics["rpc_failure_ratio"] == 0.01
    assert metrics["rpc_p50_latency_ms"] == 2.0
    assert metrics["rpc_requests_sec"] == 33.0


def test_locust_aggregate_metrics_gates_mostly_erroring_run(caplog):
    """Locust records response times for failed requests too — an all-errors
    run must not surface its (fast, bogus) latencies as metrics."""
    metrics, valid = aggregator.locust_aggregate_metrics(_agg_row(failure_count=900), 0.1)
    assert valid is False
    assert metrics == {"rpc_failure_ratio": 0.9}
    assert any("max_failure_ratio" in r.message for r in caplog.records if r.levelno >= 40)


def test_locust_aggregate_metrics_zero_requests_no_crash():
    metrics, valid = aggregator.locust_aggregate_metrics(
        _agg_row(request_count=0, failure_count=0), 0.1)
    assert valid is True
    assert metrics["rpc_failure_ratio"] == 0.0


# ---------- parse_locust_stats ----------

LOCUST_CSV_HEADER = (
    "Type,Name,Request Count,Failure Count,Median Response Time,"
    "Average Response Time,Min Response Time,Max Response Time,"
    "Average Content Size,Requests/s,Failures/s,"
    "50%,66%,75%,80%,90%,95%,98%,99%,99.9%,99.99%,100%\n"
)


def _write_locust_csv(path, rows):
    with open(path, "w") as f:
        f.write(LOCUST_CSV_HEADER)
        for r in rows:
            f.write(",".join(str(x) for x in r) + "\n")


def test_parse_locust_stats_aggregate_and_per_method(tmp_path):
    p = tmp_path / "stats.csv"
    _write_locust_csv(
        p,
        [
            ("POST", "eth_getBalance", 252, 0, 2, 2.13, 0, 65, 49.7, 8.68, 0,
             2, 2, 2, 3, 3, 4, 4, 6, 65, 65, 65),
            ("POST", "eth_getProof", 84, 0, 2, 2.31, 0, 21, 1796.0, 2.89, 0,
             2, 2, 3, 3, 3, 4, 12, 21, 21, 21, 21),
            ("", "Aggregated", 968, 0, 2, 2.15, 0, 128, 215.5, 33.35, 0,
             2, 2, 2, 3, 3, 4, 5, 7, 130, 130, 130),
        ],
    )

    out = aggregator.parse_locust_stats(str(p))

    assert out["aggregate"]["p50_ms"] == 2.0
    assert out["aggregate"]["p95_ms"] == 4.0
    assert out["aggregate"]["p99_ms"] == 7.0
    assert out["aggregate"]["requests_per_sec"] == 33.35
    assert out["aggregate"]["request_count"] == 968
    assert out["aggregate"]["failure_count"] == 0

    assert "eth_getBalance" in out["per_method"]
    assert out["per_method"]["eth_getBalance"]["p99_ms"] == 6.0
    assert out["per_method"]["eth_getProof"]["avg_content_bytes"] == 1796.0


def test_parse_locust_stats_missing_file(tmp_path):
    assert aggregator.parse_locust_stats(str(tmp_path / "nope.csv")) == {}


# ---------- parse_proof_sizes ----------

def _write_proof_csv(path, sizes):
    with open(path, "w") as f:
        f.write("timestamp_ms,address,block,response_bytes\n")
        for i, s in enumerate(sizes):
            f.write(f"{i},0xaaa,0x{i:x},{s}\n")


def test_parse_proof_sizes_percentiles(tmp_path):
    p = tmp_path / "proof_sizes.csv"
    _write_proof_csv(p, [100, 200, 300, 400, 1000])

    out = aggregator.parse_proof_sizes(str(p))

    assert out["sample_count"] == 5
    # nearest-rank: idx = round(0.5 * 4) = 2 → sorted[2] = 300
    assert out["p50_bytes"] == 300
    # idx = round(0.95 * 4) = 4 → sorted[4] = 1000
    assert out["p95_bytes"] == 1000
    assert out["mean_bytes"] == pytest.approx(400.0)


def test_parse_proof_sizes_missing_file(tmp_path):
    assert aggregator.parse_proof_sizes(str(tmp_path / "nope.csv")) == {}


def test_parse_proof_sizes_empty_file(tmp_path):
    p = tmp_path / "proof_sizes.csv"
    p.write_text("timestamp_ms,address,block,response_bytes\n")
    assert aggregator.parse_proof_sizes(str(p)) == {}


# ---------- find_previous_milestone ----------

def _write_milestone(root, milestone, client, timestamp, metrics):
    d = root / milestone
    d.mkdir(parents=True)
    payload = {
        "client": client,
        "milestone": milestone,
        "timestamp": timestamp,
        "metrics": metrics,
    }
    (d / f"metrics_{client}.json").write_text(json.dumps(payload))
    return d


def test_find_previous_milestone_picks_by_timestamp(tmp_path):
    _write_milestone(tmp_path, "v0.1.0", "nethermind", "2026-01-01T00:00:00+00:00", {"rpc_requests_sec": 100})
    _write_milestone(tmp_path, "v0.2.0", "nethermind", "2026-03-01T00:00:00+00:00", {"rpc_requests_sec": 200})
    _write_milestone(tmp_path, "v0.3.0", "nethermind", "2026-04-01T00:00:00+00:00", {"rpc_requests_sec": 300})
    current = _write_milestone(tmp_path, "v0.4.0", "nethermind", "2026-05-01T00:00:00+00:00", {"rpc_requests_sec": 400})

    prev = aggregator.find_previous_milestone("nethermind", str(current))

    assert prev is not None
    assert prev["milestone"] == "v0.3.0"  # most recent prior, excluding current


def test_find_previous_milestone_no_siblings(tmp_path):
    current = _write_milestone(tmp_path, "only", "geth", "2026-05-01T00:00:00+00:00", {})
    assert aggregator.find_previous_milestone("geth", str(current)) is None


def test_find_previous_milestone_skips_dirs_without_client_metrics(tmp_path):
    _write_milestone(tmp_path, "other-client-run", "besu", "2026-04-01T00:00:00+00:00", {"x": 1})
    current = _write_milestone(tmp_path, "current", "geth", "2026-05-01T00:00:00+00:00", {})
    assert aggregator.find_previous_milestone("geth", str(current)) is None


def test_find_previous_milestone_excludes_current_dir(tmp_path):
    """Even if the current dir has metrics_<client>.json, it must not be selected."""
    _write_milestone(tmp_path, "prev", "geth", "2026-01-01T00:00:00+00:00", {"x": 1})
    current = _write_milestone(tmp_path, "curr", "geth", "2026-05-01T00:00:00+00:00", {"x": 2})

    prev = aggregator.find_previous_milestone("geth", str(current))
    assert prev["milestone"] == "prev"


# ---------- compare_against_previous ----------

def _prev_payload(metrics):
    return {"milestone": "prev", "payload": {"metrics": metrics}}


def test_compare_lower_is_better_flags_regression(caplog):
    """sync_time_sec: 1000 -> 1400 is +40% on a lower-is-better metric."""
    with caplog.at_level(logging.INFO, logger="aggregator"):
        regressions = aggregator.compare_against_previous(
            current={"sync_time_sec": 1400.0},
            prev=_prev_payload({"sync_time_sec": 1000.0}),
            higher_is_better=set(),
        )

    assert any("REGRESSION DETECTED" in r.message and "sync_time_sec" in r.message
               for r in caplog.records if r.levelno >= logging.WARNING)
    assert len(regressions) == 1
    assert regressions[0]["metric"] == "sync_time_sec"
    assert regressions[0]["previous"] == 1000.0
    assert regressions[0]["current"] == 1400.0
    assert regressions[0]["delta_pct"] == 40.0


def test_compare_higher_is_better_flags_decrease(caplog):
    """rpc_requests_sec: 1000 -> 100 is -90% on a higher-is-better metric."""
    with caplog.at_level(logging.INFO, logger="aggregator"):
        regressions = aggregator.compare_against_previous(
            current={"rpc_requests_sec": 100.0},
            prev=_prev_payload({"rpc_requests_sec": 1000.0}),
            higher_is_better={"rpc_requests_sec"},
        )

    assert any("REGRESSION DETECTED" in r.message and "rpc_requests_sec" in r.message
               for r in caplog.records if r.levelno >= logging.WARNING)
    assert [r["metric"] for r in regressions] == ["rpc_requests_sec"]


def test_compare_improvement_does_not_warn(caplog):
    """sync_time_sec: 1000 -> 500 is -50% on lower-is-better — improvement, no warning."""
    with caplog.at_level(logging.INFO, logger="aggregator"):
        regressions = aggregator.compare_against_previous(
            current={"sync_time_sec": 500.0},
            prev=_prev_payload({"sync_time_sec": 1000.0}),
            higher_is_better=set(),
        )

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert not warnings
    assert regressions == []


def test_compare_within_threshold_does_not_warn(caplog):
    """+15% on lower-is-better is under the 25% threshold."""
    with caplog.at_level(logging.INFO, logger="aggregator"):
        regressions = aggregator.compare_against_previous(
            current={"sync_time_sec": 1150.0},
            prev=_prev_payload({"sync_time_sec": 1000.0}),
            higher_is_better=set(),
        )

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert not warnings
    assert regressions == []


def test_compare_skips_zero_prev():
    """Prev value of 0 must not divide-by-zero."""
    assert aggregator.compare_against_previous(
        current={"x": 100.0},
        prev=_prev_payload({"x": 0.0}),
        higher_is_better=set(),
    ) == []


def test_compare_skips_non_numeric_values():
    """Nulls (unavailable metrics) on either side must not crash or count."""
    assert aggregator.compare_against_previous(
        current={"x": None, "y": 100.0},
        prev=_prev_payload({"x": 50.0, "y": None}),
        higher_is_better=set(),
    ) == []


# ---------- write_csv_summary ----------

def test_write_csv_summary_emits_spec_schema(tmp_path):
    csv_path = tmp_path / "out.csv"
    aggregator.write_csv_summary(
        str(csv_path),
        client="test",
        state_size=500_000_000,
        metrics={
            "cpu_percent_peak": 42.0,
            "rpc_p95_latency_ms": 5.0,
            "proof_p50_bytes": 1781.0,
            "proof_p95_bytes": 1932.0,
            "proof_p99_bytes": 1932.0,
            "rpc": {
                "per_method": {
                    "eth_getProof": {"p50_ms": 2.0, "p95_ms": 4.0, "p99_ms": 12.0, "mean_ms": 2.3}
                }
            },
            "proof_sizes": {"p50_bytes": 1781, "p95_bytes": 1932, "p99_bytes": 1932, "mean_bytes": 1796},
        },
    )

    with open(csv_path) as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == ["client", "state_size", "metric", "p50", "p95", "p99", "mean"]
        rows = list(reader)

    # Flat proof_p* metrics are folded into the dedicated percentile row, not duplicated.
    assert not [r for r in rows if r["metric"].startswith("proof_p")]

    system_row = next(r for r in rows if r["metric"] == "cpu_percent_peak")
    assert system_row["client"] == "test"
    assert system_row["state_size"] == "500000000"
    assert system_row["mean"] == "42.0"
    assert system_row["p50"] == ""

    rpc_row = next(r for r in rows if r["metric"] == "rpc:eth_getProof:latency_ms")
    assert rpc_row["p50"] == "2.0"
    assert rpc_row["p95"] == "4.0"
    assert rpc_row["p99"] == "12.0"
    assert rpc_row["mean"] == "2.3"

    proof_row = next(r for r in rows if r["metric"] == "eth_getProof:response_bytes")
    assert proof_row["p95"] == "1932"


def test_write_csv_summary_omits_proof_row_when_empty(tmp_path):
    csv_path = tmp_path / "out.csv"
    aggregator.write_csv_summary(
        str(csv_path), client="test", state_size=None,
        metrics={"cpu_percent_peak": 0.0, "proof_sizes": {}},
    )
    text = csv_path.read_text()
    assert "eth_getProof:response_bytes" not in text
