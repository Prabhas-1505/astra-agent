"""Fault-injection eval harness.

Cycles ASTRA through several known fault scenarios in the lab, grades each
diagnosis against ground truth (the device and config element actually at
fault), and reports accuracy. This is what turns "confidence: 0.95" from an
asserted number into something measured.

Each scenario is run multiple times (default 5, `--runs`) so the report shows
mean +/- stddev instead of a single anecdotal pass/fail — LLM non-determinism
is real here, not harness flakiness, and worth reporting honestly. `--no-kb`
disables both runbook and learned-case retrieval for the run, so the KB's
actual contribution can be measured by diffing two reports instead of assumed.
"""

import argparse
import os
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
LAB_COMPOSE = ROOT / "lab" / "docker-compose.yml"
EVAL_KB_PATH = ROOT / "data" / "eval_cases.json"

sys.path.insert(0, str(ROOT))

from app.agent.graph import build_graph
from app.logging_config import configure_logging, new_correlation_id

configure_logging()

SCENARIOS = {
    "gi_shutdown": {
        "device": "R1",
        "keywords": ["shutdown"],
        "description": "Gi0/0 administratively down",
    },
    "ospf_network_missing": {
        "device": "R1",
        "keywords": ["network"],
        "description": "OSPF network statement missing on R1",
    },
    "area_mismatch": {
        "device": "R2",
        "keywords": ["area"],
        "description": "OSPF area ID mismatch (R2 area 1 vs R1 area 0)",
    },
    "duplicate_ip": {
        "device": "R2",
        "keywords": ["duplicate", "dupaddr", "ip address", "conflict"],
        "description": "Duplicate IP address on R2's link interface (copied R1's)",
    },
    "mtu_mismatch": {
        "device": "R1",
        "keywords": ["mtu"],
        "description": "MTU mismatch on R1's link interface blocking OSPF adjacency",
    },
    "healthy_network": {
        "no_fault": True,
        "description": "Negative case — full OSPF adjacency, no drift, benign logs",
    },
}

# gpt-4o-mini pricing as of the time this harness was written (USD per 1K
# tokens) — hardcoded rather than fetched live, so cost figures are an
# estimate that can drift from OpenAI's current published pricing, not a
# billed amount.
_PRICE_PER_1K_TOKENS = {"input": 0.00015, "output": 0.0006}


def wait_for_port(port: int, host: str = "127.0.0.1", timeout: float = 15) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def restart_lab(scenario: str) -> None:
    # --build matters: without it, a new/edited scenario file (lab/fake_router/
    # scenarios/*.py) silently keeps running against whatever image was last
    # built, since --force-recreate alone only recreates the *container*, not
    # the image. Docker's layer cache makes the no-op case fast.
    env = os.environ.copy()
    env["FAULT_SCENARIO"] = scenario
    subprocess.run(
        ["docker", "compose", "-f", str(LAB_COMPOSE), "up", "-d", "--build", "--force-recreate"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    if not (wait_for_port(2201) and wait_for_port(2202)):
        raise RuntimeError(f"lab did not come up for scenario '{scenario}'")
    time.sleep(3)


def estimate_cost(usage: dict) -> float:
    input_cost = usage.get("input_tokens", 0) / 1000 * _PRICE_PER_1K_TOKENS["input"]
    output_cost = usage.get("output_tokens", 0) / 1000 * _PRICE_PER_1K_TOKENS["output"]
    return input_cost + output_cost


def run_case(kb_enabled: bool) -> tuple[dict, float]:
    if EVAL_KB_PATH.exists():
        EVAL_KB_PATH.unlink()
    os.environ["ASTRA_KB_PATH"] = str(EVAL_KB_PATH)
    os.environ["ASTRA_KB_ENABLED"] = "1" if kb_enabled else "0"
    new_correlation_id()
    graph = build_graph()
    with patch("builtins.input", return_value="y"):
        start = time.perf_counter()
        final_state = graph.invoke({"max_retries": 3})
        latency = time.perf_counter() - start
    return final_state, latency


def grade(scenario_name: str, final_state: dict) -> tuple[str, float | None]:
    expected = SCENARIOS[scenario_name]
    rc = final_state.get("root_cause")

    if expected.get("no_fault"):
        # Negative case: there is no root cause. Correct iff ASTRA reaches
        # inconclusive (exhausted retries, never confirmed anything) rather
        # than confirming a hallucinated fault with a confidence score — a
        # false positive, which "inconclusive" and "incorrect" alone don't
        # distinguish from the fault-injection scenarios' failure modes, so
        # it's labeled explicitly here.
        return ("correct", None) if not rc else ("false_positive", rc["confidence"])

    if not rc:
        return "inconclusive", None

    hyp = final_state["current_hypothesis"]
    text = " ".join([rc["statement"], rc["recommended_fix"], hyp["statement"]]).lower()

    device_ok = expected["device"] in hyp["target_devices"]
    keyword_ok = any(k.lower() in text for k in expected["keywords"])
    verdict = "correct" if device_ok and keyword_ok else "incorrect"
    return verdict, rc["confidence"]


def _mean_std(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    mean = statistics.mean(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0.0
    return mean, stdev


def run_scenario(name: str, runs: int, kb_enabled: bool) -> dict:
    expected = SCENARIOS[name]
    print(f"\n>>> scenario: {name} ({expected['description']}) — {runs} run(s), kb={'on' if kb_enabled else 'off'}")
    restart_lab(name)

    verdicts, confidences, retries_list, latencies, costs = [], [], [], [], []
    for i in range(runs):
        final_state, latency = run_case(kb_enabled)
        verdict, confidence = grade(name, final_state)
        retries = final_state.get("retry_count", 0)
        cost = estimate_cost(final_state.get("token_usage", {}))

        verdicts.append(verdict)
        if confidence is not None:
            confidences.append(confidence)
        retries_list.append(retries)
        latencies.append(latency)
        costs.append(cost)

        conf_str = f"{confidence:.2f}" if confidence is not None else "n/a"
        print(
            f"    run {i + 1}/{runs}: verdict={verdict}  confidence={conf_str}  "
            f"retries={retries}  latency={latency:.1f}s  cost=${cost:.4f}"
        )

    accuracy = sum(1 for v in verdicts if v == "correct") / len(verdicts)
    conf_mean, conf_std = _mean_std(confidences)
    retries_mean, retries_std = _mean_std([float(r) for r in retries_list])
    latency_mean, latency_std = _mean_std(latencies)
    cost_mean, cost_std = _mean_std(costs)

    return {
        "scenario": name,
        "accuracy": accuracy,
        "confidence": (conf_mean, conf_std),
        "retries": (retries_mean, retries_std),
        "latency": (latency_mean, latency_std),
        "cost": (cost_mean, cost_std),
    }


def print_report(results: list[dict], runs: int, kb_enabled: bool) -> None:
    overall_accuracy = statistics.mean(r["accuracy"] for r in results)
    print("\n=== EVAL REPORT ===")
    print(f"Runs per scenario: {runs}  |  KB: {'on' if kb_enabled else 'off'}")
    print(f"Overall accuracy: {overall_accuracy * 100:.0f}%\n")
    header = f"{'scenario':<24}{'accuracy':<12}{'confidence':<16}{'retries':<14}{'latency (s)':<16}cost ($)"
    print(header)
    for r in results:
        conf_mean, conf_std = r["confidence"]
        retries_mean, retries_std = r["retries"]
        latency_mean, latency_std = r["latency"]
        cost_mean, cost_std = r["cost"]
        print(
            f"{r['scenario']:<24}{r['accuracy'] * 100:<11.0f}%"
            f"{conf_mean:.2f}±{conf_std:.2f}      "
            f"{retries_mean:.1f}±{retries_std:.1f}     "
            f"{latency_mean:.1f}±{latency_std:.1f}         "
            f"{cost_mean:.4f}±{cost_std:.4f}"
        )
    total_cost = sum(r["cost"][0] * runs for r in results)
    print(f"\nTotal estimated cost for this eval run: ${total_cost:.4f}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs", type=int, default=int(os.environ.get("ASTRA_EVAL_RUNS", "5")),
        help="runs per scenario (default 5, or $ASTRA_EVAL_RUNS)",
    )
    parser.add_argument(
        "--no-kb", action="store_true",
        help="disable runbook + learned-case retrieval, to measure the KB's actual contribution",
    )
    parser.add_argument(
        "--scenario", action="append", dest="scenarios", choices=list(SCENARIOS),
        help="limit to specific scenario(s); repeatable. Default: all.",
    )
    args = parser.parse_args()

    kb_enabled = not args.no_kb
    scenario_names = args.scenarios or list(SCENARIOS)

    results = [run_scenario(name, args.runs, kb_enabled) for name in scenario_names]
    print_report(results, args.runs, kb_enabled)

    restart_lab("gi_shutdown")  # leave the lab in its default state


if __name__ == "__main__":
    main()
