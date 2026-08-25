import difflib

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app.agent.golden_configs import GOLDEN_CONFIGS
from app.kb import store as kb
from app.tools.netmiko_tool import DEVICES, run_commands

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

ALLOWED_COMMANDS = [
    "show ip interface brief",
    "show ip ospf neighbor",
    "show cdp neighbors",
    "show running-config",
    "show version",
]


class Hypothesis(BaseModel):
    statement: str
    target_device: str
    verify_commands: list[str]


class HypothesisList(BaseModel):
    hypotheses: list[Hypothesis] = Field(
        description="Ranked most-likely-first, at most 3 hypotheses"
    )


class ValidationVerdict(BaseModel):
    confirmed: bool
    reasoning: str


class RootCause(BaseModel):
    statement: str
    confidence: float = Field(ge=0.0, le=1.0)
    recommended_fix: str


def logs_analyzer(state):
    logs = {name: run_commands(name, ["show logging"])["show logging"] for name in DEVICES}
    return {"logs": logs}


def topology_analyzer(state):
    topology = {}
    for name in DEVICES:
        out = run_commands(name, ["show ip interface brief", "show cdp neighbors"])
        topology[name] = out
    return {"topology": topology}


def config_analyzer(state):
    drift = {}
    for name in DEVICES:
        running = run_commands(name, ["show running-config"])["show running-config"]
        golden = GOLDEN_CONFIGS.get(name, "")
        diff = "\n".join(
            difflib.unified_diff(
                golden.splitlines(),
                running.splitlines(),
                fromfile=f"{name}-golden",
                tofile=f"{name}-running",
                lineterm="",
            )
        )
        drift[name] = diff if diff else "(no drift from golden config)"
    return {"config_drift": drift}


def context_aggregator(state):
    parts = ["=== Logs ==="]
    for name, text in state["logs"].items():
        parts.append(f"--- {name} ---\n{text}")

    parts.append("=== Topology ===")
    for name, out in state["topology"].items():
        parts.append(
            f"--- {name} ---\n"
            f"$ show ip interface brief\n{out['show ip interface brief']}\n"
            f"$ show cdp neighbors\n{out['show cdp neighbors']}"
        )

    parts.append("=== Config drift vs. golden ===")
    for name, diff in state["config_drift"].items():
        parts.append(f"--- {name} ---\n{diff}")

    return {"context": "\n\n".join(parts)}


def hypothesis_engine(state):
    context = state["context"]
    precedent = kb.retrieve_similar(context, k=2)
    precedent_text = ""
    if precedent:
        print(f"\n[KB] found {len(precedent)} precedent case(s) from past incidents")
        blocks = []
        for case in precedent:
            rc = case.get("root_cause") or {}
            blocks.append(
                f"- outcome: {case['outcome']}; "
                f"root cause: {rc.get('statement', 'n/a')}; "
                f"fix: {rc.get('recommended_fix', 'n/a')}"
            )
        precedent_text = "\nPrecedent from past incidents:\n" + "\n".join(blocks) + "\n"
    else:
        print("\n[KB] no precedent cases yet")

    runbooks = kb.retrieve_runbooks(context, k=1)
    runbook_text = ""
    if runbooks:
        print(f"[KB] matched {len(runbooks)} runbook(s)")
        blocks = [f"- {rb['title']}: {rb['body']}" for rb in runbooks]
        runbook_text = "\nRelevant runbook guidance:\n" + "\n".join(blocks) + "\n"

    prompt = (
        "You are a network diagnostics assistant. Given topology, config-drift, and log "
        "evidence gathered from these devices, propose up to 3 ranked hypotheses for the "
        "root cause.\n"
        f"Devices available: {list(DEVICES)}\n"
        f"Allowed verify commands (use exactly these strings): {ALLOWED_COMMANDS}\n"
        "For each hypothesis, name the single device most useful to inspect next and the exact "
        "show commands (from the allowed list) to run to verify it.\n"
        f"{runbook_text}"
        f"{precedent_text}\n"
        f"{context}"
    )
    result = llm.with_structured_output(HypothesisList).invoke(prompt)
    return {
        "hypotheses": [h.model_dump() for h in result.hypotheses],
        "hypothesis_index": 0,
        "retry_count": 0,
        "evidence": {},
    }


def diagnostic_tool(state):
    idx = state["hypothesis_index"]
    h = state["hypotheses"][idx]
    output = run_commands(h["target_device"], h["verify_commands"])
    evidence = dict(state.get("evidence") or {})
    evidence[idx] = output
    return {"evidence": evidence}


def validation(state):
    idx = state["hypothesis_index"]
    h = state["hypotheses"][idx]
    ev = state["evidence"][idx]
    ev_text = "\n\n".join(f"$ {cmd}\n{out}" for cmd, out in ev.items())
    prompt = (
        f"Hypothesis: {h['statement']}\n\n"
        f"Evidence collected from {h['target_device']}:\n{ev_text}\n\n"
        "Does the evidence confirm this hypothesis as the root cause? Be strict — "
        "only confirm if the evidence directly supports it."
    )
    verdict = llm.with_structured_output(ValidationVerdict).invoke(prompt)

    if verdict.confirmed:
        return {"validation_result": "confirmed", "validation_reasoning": verdict.reasoning}

    retry_count = state["retry_count"] + 1
    next_index = idx + 1
    if retry_count >= state["max_retries"] or next_index >= len(state["hypotheses"]):
        return {
            "validation_result": "exhausted",
            "validation_reasoning": verdict.reasoning,
            "retry_count": retry_count,
        }
    return {
        "validation_result": "retry",
        "validation_reasoning": verdict.reasoning,
        "retry_count": retry_count,
        "hypothesis_index": next_index,
    }


def root_cause_synthesis(state):
    idx = state["hypothesis_index"]
    h = state["hypotheses"][idx]
    ev = state["evidence"][idx]
    ev_text = "\n\n".join(f"$ {cmd}\n{out}" for cmd, out in ev.items())
    prompt = (
        f"Confirmed hypothesis: {h['statement']}\n\nSupporting evidence:\n{ev_text}\n\n"
        "Write a concise root-cause statement, a confidence score between 0 and 1, "
        "and a specific recommended fix (a single CLI command or short sequence)."
    )
    rc = llm.with_structured_output(RootCause).invoke(prompt)
    return {"root_cause": rc.model_dump()}


def human_approval(state):
    rc = state["root_cause"]
    print("\n=== ROOT CAUSE ===")
    print(rc["statement"])
    print(f"Confidence: {rc['confidence']:.2f}")
    print(f"Recommended fix: {rc['recommended_fix']}")
    answer = input("\nApprove this fix? [y/N]: ").strip().lower()
    outcome = "approved" if answer == "y" else "rejected"
    if outcome == "approved":
        print("\nApproved. ASTRA does not auto-execute — apply the fix manually.")
    else:
        print("\nRejected. No fix applied.")
    kb.write_case(state["context"], rc, outcome)
    print(f"[KB] wrote back case (outcome: {outcome}) for future precedent")
    return {"approval": outcome}


def escalate_inconclusive(state):
    print("\n=== INCONCLUSIVE ===")
    print(f"Exhausted {state['retry_count']} hypotheses without confirmation. Escalating to human review.")
    for i, h in enumerate(state["hypotheses"]):
        print(f"\n[{i}] {h['statement']}")
        for cmd, out in state.get("evidence", {}).get(i, {}).items():
            print(f"  $ {cmd}\n  {out}")
    kb.write_case(state["context"], None, "inconclusive")
    print("[KB] wrote back inconclusive case for future precedent")
    return {}
