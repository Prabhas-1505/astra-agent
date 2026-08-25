"""Fault-injection eval harness.

Cycles ASTRA through several known fault scenarios in the lab, grades each
diagnosis against ground truth (the device and config element actually at
fault), and reports accuracy. This is what turns "confidence: 0.95" from an
asserted number into something measured.
"""

import os
import socket
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

from app.agent.graph import build_graph  # noqa: E402

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
}


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
    env = os.environ.copy()
    env["FAULT_SCENARIO"] = scenario
    subprocess.run(
        ["docker", "compose", "-f", str(LAB_COMPOSE), "up", "-d", "--force-recreate"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    if not (wait_for_port(2201) and wait_for_port(2202)):
        raise RuntimeError(f"lab did not come up for scenario '{scenario}'")
    time.sleep(3)


def run_case() -> dict:
    if EVAL_KB_PATH.exists():
        EVAL_KB_PATH.unlink()
    os.environ["ASTRA_KB_PATH"] = str(EVAL_KB_PATH)
    graph = build_graph()
    with patch("builtins.input", return_value="y"):
        return graph.invoke({"max_retries": 3})


def grade(scenario_name: str, final_state: dict) -> tuple[str, float | None]:
    expected = SCENARIOS[scenario_name]
    rc = final_state.get("root_cause")
    if not rc:
        return "inconclusive", None

    hyp = final_state["current_hypothesis"]
    text = " ".join([rc["statement"], rc["recommended_fix"], hyp["statement"]]).lower()

    device_ok = hyp["target_device"] == expected["device"]
    keyword_ok = any(k.lower() in text for k in expected["keywords"])
    verdict = "correct" if device_ok and keyword_ok else "incorrect"
    return verdict, rc["confidence"]


def main():
    results = []
    for name, expected in SCENARIOS.items():
        print(f"\n>>> scenario: {name} ({expected['description']})")
        restart_lab(name)
        final_state = run_case()
        verdict, confidence = grade(name, final_state)
        retries = final_state.get("retry_count", 0)
        results.append(
            {"scenario": name, "verdict": verdict, "confidence": confidence, "retries": retries}
        )
        conf_str = f"{confidence:.2f}" if confidence is not None else "n/a"
        print(f"    verdict={verdict}  confidence={conf_str}  retries={retries}")

    correct = sum(1 for r in results if r["verdict"] == "correct")
    print("\n=== EVAL REPORT ===")
    print(f"Accuracy: {correct}/{len(results)} ({100 * correct / len(results):.0f}%)\n")
    print(f"{'scenario':<24}{'verdict':<14}{'confidence':<12}retries")
    for r in results:
        conf_str = f"{r['confidence']:.2f}" if r["confidence"] is not None else "n/a"
        print(f"{r['scenario']:<24}{r['verdict']:<14}{conf_str:<12}{r['retries']}")

    restart_lab("gi_shutdown")  # leave the lab in its default state


if __name__ == "__main__":
    main()
