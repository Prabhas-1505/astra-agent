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


def _format_ruled_out(attempts: list[dict]) -> str:
    if not attempts:
        return ""
    blocks = []
    for i, attempt in enumerate(attempts):
        h = attempt["hypothesis"]
        ev_text = "\n  ".join(f"$ {cmd}: {out}" for cmd, out in attempt["evidence"].items())
        blocks.append(
            f"{i + 1}. Hypothesis: {h['statement']} (checked {h['target_device']})\n"
            f"   Evidence found:\n  {ev_text}\n"
            f"   Why this was ruled out: {attempt['reasoning']}"
        )
    return (
        "\nAlready ruled out — do not repeat these, and use the reason each was ruled out to "
        "refine your next guess (the evidence gathered may still be relevant to a *different* "
        "hypothesis):\n" + "\n".join(blocks) + "\n"
    )


def hypothesis_engine(state):
    context = state["context"]
    attempts = state.get("attempts", [])

    precedent_text = ""
    precedent = []
    runbooks = []
    if not attempts:  # only worth checking KB/runbooks on the very first guess
        precedent = kb.retrieve_similar(context, k=2)
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

        # k=2, not 1: single-vector retrieval over a long multi-section context
        # can rank a runbook higher on incidental vocabulary overlap (e.g.
        # shared "config drift" phrasing) even when a different runbook is the
        # true semantic match for the actual root cause. Surfacing the top-2
        # gives the LLM (and the human reading the KB panel) both candidates
        # instead of one confidently-wrong pick.
        runbooks = kb.retrieve_runbooks(context, k=2)
        if runbooks:
            print(f"[KB] matched {len(runbooks)} runbook(s)")
            blocks = [f"- {rb['title']}: {rb['body']}" for rb in runbooks]
            runbook_text = "\nRelevant runbook guidance:\n" + "\n".join(blocks) + "\n"
        else:
            runbook_text = ""
    else:
        runbook_text = ""

    ruled_out_text = _format_ruled_out(attempts)

    prompt = (
        "You are a network diagnostics assistant. Given topology, config-drift, and log "
        "evidence gathered from these devices, propose your single best hypothesis for the "
        "root cause.\n"
        f"Devices available: {list(DEVICES)}\n"
        f"Allowed verify commands (use exactly these strings): {ALLOWED_COMMANDS}\n"
        "Name the single device most useful to inspect next and the exact show commands "
        "(from the allowed list) to run to verify it.\n"
        f"{runbook_text}"
        f"{precedent_text}"
        f"{ruled_out_text}\n"
        f"{context}"
    )
    result = llm.with_structured_output(Hypothesis).invoke(prompt)
    update = {
        "current_hypothesis": result.model_dump(),
        "current_evidence": {},
        "attempts": attempts,
        "retry_count": state.get("retry_count", 0),
    }
    if not attempts:  # only set on the first pass, for the UI to display
        update["kb_precedent"] = precedent
        update["kb_runbooks"] = runbooks
    return update


def diagnostic_tool(state):
    h = state["current_hypothesis"]
    output = run_commands(h["target_device"], h["verify_commands"])
    return {"current_evidence": output}


def validation(state):
    h = state["current_hypothesis"]
    ev = state["current_evidence"]
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

    attempts = list(state.get("attempts", []))
    attempts.append({"hypothesis": h, "evidence": ev, "reasoning": verdict.reasoning})
    retry_count = state["retry_count"] + 1

    if retry_count >= state["max_retries"]:
        return {
            "validation_result": "exhausted",
            "validation_reasoning": verdict.reasoning,
            "retry_count": retry_count,
            "attempts": attempts,
        }
    return {
        "validation_result": "retry",
        "validation_reasoning": verdict.reasoning,
        "retry_count": retry_count,
        "attempts": attempts,
    }


def root_cause_synthesis(state):
    h = state["current_hypothesis"]
    ev = state["current_evidence"]
    ev_text = "\n\n".join(f"$ {cmd}\n{out}" for cmd, out in ev.items())
    prompt = (
        f"Confirmed hypothesis: {h['statement']}\n\nSupporting evidence:\n{ev_text}\n\n"
        "Write a concise root-cause statement, a confidence score between 0 and 1, "
        "and a specific recommended fix (a single CLI command or short sequence)."
    )
    rc = llm.with_structured_output(RootCause).invoke(prompt)
    return {"root_cause": rc.model_dump()}


def record_decision(context: str, root_cause: dict, outcome: str) -> None:
    """Shared by the CLI's human_approval node and the Gradio UI's approve/
    reject buttons — the KB writeback shouldn't care which one collected the
    decision.
    """
    kb.write_case(context, root_cause, outcome)
    print(f"[KB] wrote back case (outcome: {outcome}) for future precedent")


def record_inconclusive(context: str) -> None:
    kb.write_case(context, None, "inconclusive")
    print("[KB] wrote back inconclusive case for future precedent")


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
    record_decision(state["context"], rc, outcome)
    return {"approval": outcome}


def escalate_inconclusive(state):
    print("\n=== INCONCLUSIVE ===")
    print(f"Exhausted {state['retry_count']} hypotheses without confirmation. Escalating to human review.")
    for i, attempt in enumerate(state.get("attempts", [])):
        h = attempt["hypothesis"]
        print(f"\n[{i}] {h['statement']}")
        for cmd, out in attempt["evidence"].items():
            print(f"  $ {cmd}\n  {out}")
        print(f"  ruled out: {attempt['reasoning']}")
    record_inconclusive(state["context"])
    return {}
