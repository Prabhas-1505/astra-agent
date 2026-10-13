import difflib
import logging
import os
import re

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from app.agent.golden_configs import GOLDEN_CONFIGS
from app.kb import store as kb
from app.tools.netmiko_tool import DEVICES, run_commands

logger = logging.getLogger(__name__)

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

_RETRYABLE_LLM_ERRORS = (TimeoutError, ConnectionError)


@retry(
    retry=retry_if_exception_type(_RETRYABLE_LLM_ERRORS),
    stop=stop_after_attempt(3),
    wait=wait_fixed(2),
    reraise=True,
)
def _invoke_structured(llm_instance, model_cls, prompt):
    """Transient OpenAI network/timeout errors get retried with backoff, the same
    pattern already used for Netmiko in app/tools/netmiko_tool.py. Malformed output
    or auth errors are not in `_RETRYABLE_LLM_ERRORS` and raise immediately.

    `include_raw=True` is what exposes `usage_metadata` (langchain-core's native,
    non-deprecated token-usage field on the raw AIMessage) even though the call
    site only wants the parsed pydantic model — this is what backs the eval
    harness's per-run token/cost reporting.
    """
    result = llm_instance.with_structured_output(model_cls, include_raw=True).invoke(prompt)
    usage = getattr(result["raw"], "usage_metadata", None) or {}
    return result["parsed"], usage

ALLOWED_COMMANDS = [
    "show ip interface brief",
    "show ip ospf neighbor",
    "show cdp neighbors",
    "show running-config",
    "show version",
]


class Hypothesis(BaseModel):
    statement: str
    # Plural because some faults (MTU/area mismatch) are inherently a comparison
    # between two devices and can never be confirmed from one side's evidence
    # alone — see the README's "Eval harness" section for the live run that
    # caught this (mtu_mismatch 0/3) when this was still a single `target_device`.
    target_devices: list[str] = Field(min_length=1)
    verify_commands: list[str]


class RankedHypotheses(BaseModel):
    primary: Hypothesis
    secondary: Hypothesis


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


_IFACE_STATUS_RE = re.compile(
    r"^(?P<iface>\S+)\s+\S+\s+\S+\s+\S+\s+"
    r"(?P<status>administratively down|up|down)\s+(?P<protocol>up|down)",
    re.MULTILINE,
)
_CDP_COUNT_RE = re.compile(r"Total cdp entries displayed:\s*(\d+)")
_SYSLOG_CODE_RE = re.compile(r"%[A-Z0-9_]+-\d-[A-Z0-9_]+")
_DRIFT_LINE_RE = re.compile(r"^[+-][^+-].*$", re.MULTILINE)


def build_symptom_fingerprint(state) -> str:
    """A compact, structured summary of what's actually abnormal — interface
    status, neighbor counts, config-drift lines, and syslog codes — used as the
    retrieval query for both the runbook KB and the learned-case KB, instead of
    the full concatenated `context`.

    The README documents a real precision bug: embedding the entire multi-section
    context let incidental vocabulary overlap (e.g. shared "config drift" phrasing)
    outrank the true semantic match. This fingerprint strips out the large amount
    of unchanging boilerplate (banners, command echoes) and keeps only the signal
    that actually varies between fault types, so retrieval compares like with like.
    """
    lines = []
    for name, out in state["topology"].items():
        statuses = [
            f"{m['iface']}={m['status']}/{m['protocol']}"
            for m in _IFACE_STATUS_RE.finditer(out["show ip interface brief"])
        ]
        lines.append(f"{name} interfaces: {', '.join(statuses) or 'unparsed'}")
        cdp_match = _CDP_COUNT_RE.search(out["show cdp neighbors"])
        lines.append(f"{name} cdp neighbors: {cdp_match.group(1) if cdp_match else 'unknown'}")

    for name, diff in state["config_drift"].items():
        if diff == "(no drift from golden config)":
            lines.append(f"{name} config: no drift")
        else:
            changed = [m.group(0).strip() for m in _DRIFT_LINE_RE.finditer(diff)]
            lines.append(f"{name} config drift: {'; '.join(changed) or diff}")

    for name, text in state.get("logs", {}).items():
        codes = sorted(set(_SYSLOG_CODE_RE.findall(text)))
        if codes:
            lines.append(f"{name} log codes: {', '.join(codes)}")

    return "\n".join(lines)


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

    return {
        "context": "\n\n".join(parts),
        "symptom_fingerprint": build_symptom_fingerprint(state),
    }


def _format_ruled_out(attempts: list[dict]) -> str:
    if not attempts:
        return ""
    blocks = []
    for i, attempt in enumerate(attempts):
        h = attempt["hypothesis"]
        ev_text = _format_evidence(attempt["evidence"])
        blocks.append(
            f"{i + 1}. Hypothesis: {h['statement']} (checked {', '.join(h['target_devices'])})\n"
            f"   Evidence found:\n  {ev_text}\n"
            f"   Why this was ruled out: {attempt['reasoning']}"
        )
    return (
        "\nAlready ruled out — do not repeat these, and use the reason each was ruled out to "
        "refine your next guess (the evidence gathered may still be relevant to a *different* "
        "hypothesis):\n" + "\n".join(blocks) + "\n"
    )


def _kb_enabled() -> bool:
    return os.environ.get("ASTRA_KB_ENABLED", "1") != "0"


def hypothesis_engine(state):
    context = state["context"]
    fingerprint = state.get("symptom_fingerprint", context)
    attempts = state.get("attempts", [])
    pending = list(state.get("pending_hypotheses", []))
    retry_count = state.get("retry_count", 0)

    # Fast path: a retry with an already-ranked second candidate waiting — use
    # it directly instead of paying for another LLM call. Only the fully
    # informed regeneration path below (both candidates exhausted) re-reasons
    # from scratch using everything ruled out so far.
    if attempts and pending:
        next_hypothesis = pending.pop(0)
        logger.info("hypothesis_engine: advancing to pending secondary hypothesis")
        return {
            "current_hypothesis": next_hypothesis,
            "pending_hypotheses": pending,
            "current_evidence": {},
            "attempts": attempts,
            "retry_count": retry_count,
        }

    precedent_text = ""
    precedent = []
    runbooks = []
    if not attempts and _kb_enabled():  # only worth checking KB/runbooks on the very first guess
        precedent = kb.retrieve_similar(fingerprint, k=2)
        if precedent:
            logger.info("KB: found %d precedent case(s) from past incidents", len(precedent))
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
            logger.info("KB: no precedent cases yet")

        # k=2, not 1: single-vector retrieval over a long multi-section context
        # can rank a runbook higher on incidental vocabulary overlap (e.g.
        # shared "config drift" phrasing) even when a different runbook is the
        # true semantic match for the actual root cause. Surfacing the top-2
        # gives the LLM (and the human reading the KB panel) both candidates
        # instead of one confidently-wrong pick.
        runbooks = kb.retrieve_runbooks(fingerprint, k=2)
        if runbooks:
            logger.info("KB: matched %d runbook(s)", len(runbooks))
            blocks = [f"- {rb['title']}: {rb['body']}" for rb in runbooks]
            runbook_text = "\nRelevant runbook guidance:\n" + "\n".join(blocks) + "\n"
        else:
            runbook_text = ""
    else:
        runbook_text = ""

    ruled_out_text = _format_ruled_out(attempts)

    if not attempts:
        # First pass: rank the top-2 candidate hypotheses up front instead of
        # committing to a single guess. The primary is verified first; if
        # refuted, the secondary is tried next (fast path above) before falling
        # back to a fully informed regeneration.
        prompt = (
            "You are a network diagnostics assistant. Given topology, config-drift, and log "
            "evidence gathered from these devices, propose your two best-ranked hypotheses for "
            "the root cause (primary = most likely, secondary = next most likely, genuinely "
            "distinct from the primary, not a minor rewording of it).\n"
            f"Devices available: {list(DEVICES)}\n"
            f"Allowed verify commands (use exactly these strings): {ALLOWED_COMMANDS}\n"
            "For each, name the device(s) most useful to inspect next and the exact show "
            "commands (from the allowed list) to run against each. Name more than one device "
            "only when the hypothesis is inherently a *comparison* between two devices (e.g. an "
            "MTU or area-ID mismatch can't be confirmed from one side's config alone) — a "
            "single-device symptom (interface shut down, missing config) needs only one.\n"
            f"{runbook_text}"
            f"{precedent_text}\n"
            f"{context}"
        )
        result, usage = _invoke_structured(llm, RankedHypotheses, prompt)
        update = {
            "current_hypothesis": result.primary.model_dump(),
            "pending_hypotheses": [result.secondary.model_dump()],
            "current_evidence": {},
            "attempts": attempts,
            "retry_count": retry_count,
            "kb_precedent": precedent,
            "kb_runbooks": runbooks,
            "token_usage": usage,
        }
        return update

    # Both ranked candidates exhausted — regenerate fresh, informed by everything
    # ruled out so far, rather than cycling through a static list decided upfront.
    prompt = (
        "You are a network diagnostics assistant. Given topology, config-drift, and log "
        "evidence gathered from these devices, propose your single best hypothesis for the "
        "root cause.\n"
        f"Devices available: {list(DEVICES)}\n"
        f"Allowed verify commands (use exactly these strings): {ALLOWED_COMMANDS}\n"
        "Name the device(s) most useful to inspect next and the exact show commands (from the "
        "allowed list) to run against each. Name more than one device only when the hypothesis "
        "is inherently a *comparison* between two devices (e.g. an MTU or area-ID mismatch can't "
        "be confirmed from one side's config alone) — a single-device symptom needs only one.\n"
        f"{ruled_out_text}\n"
        f"{context}"
    )
    result, usage = _invoke_structured(llm, Hypothesis, prompt)
    return {
        "current_hypothesis": result.model_dump(),
        "pending_hypotheses": [],
        "current_evidence": {},
        "attempts": attempts,
        "retry_count": retry_count,
        "token_usage": usage,
    }


def diagnostic_tool(state):
    """Enforces, in code, the two boundaries the prompts only *ask* the LLM to
    respect: `ALLOWED_COMMANDS` and known `DEVICES`. `Hypothesis.verify_commands`
    and `.target_devices` are free-text LLM output — prior to this, a
    hallucinated device name reached `run_commands` directly and crashed the
    whole run with an unhandled KeyError (netmiko_tool.py does `DEVICES[name]`
    with no guard), and an off-allowlist command would have been sent to the
    device with no code-level check at all, only a prompt instruction. Neither
    is reachable anymore: unknown devices/commands are dropped and logged, and
    if nothing valid survives, evidence is empty — which Validation correctly
    refuses to confirm against, same as any other insufficient-evidence case,
    rather than the graph crashing.
    """
    h = state["current_hypothesis"]
    safe_devices = [d for d in h["target_devices"] if d in DEVICES]
    unknown_devices = [d for d in h["target_devices"] if d not in DEVICES]
    safe_commands = [c for c in h["verify_commands"] if c in ALLOWED_COMMANDS]
    rejected_commands = [c for c in h["verify_commands"] if c not in ALLOWED_COMMANDS]

    if unknown_devices:
        logger.warning("diagnostic_tool: ignoring unknown device(s): %s", unknown_devices)
    if rejected_commands:
        logger.warning("diagnostic_tool: ignoring non-allowlisted command(s): %s", rejected_commands)

    if not safe_devices or not safe_commands:
        return {"current_evidence": {}}

    evidence = {device: run_commands(device, safe_commands) for device in safe_devices}
    return {"current_evidence": evidence}


def _format_evidence(evidence: dict[str, dict[str, str]]) -> str:
    """`evidence` is `{device: {command: output}}` — one or more devices,
    since diagnostic_tool now gathers from every device a hypothesis names,
    not just one (see `Hypothesis.target_devices`)."""
    return "\n\n".join(
        f"--- {device} ---\n" + "\n".join(f"$ {cmd}\n{out}" for cmd, out in cmds.items())
        for device, cmds in evidence.items()
    )


def validation(state):
    h = state["current_hypothesis"]
    ev = state["current_evidence"]
    ev_text = _format_evidence(ev)
    prompt = (
        f"Hypothesis: {h['statement']}\n\n"
        f"Evidence collected from {', '.join(h['target_devices'])}:\n{ev_text}\n\n"
        "Does the evidence confirm this hypothesis as the root cause? Be strict — "
        "only confirm if the evidence directly supports it."
    )
    verdict, usage = _invoke_structured(llm, ValidationVerdict, prompt)

    if verdict.confirmed:
        return {
            "validation_result": "confirmed",
            "validation_reasoning": verdict.reasoning,
            "token_usage": usage,
        }

    attempts = list(state.get("attempts", []))
    attempts.append({"hypothesis": h, "evidence": ev, "reasoning": verdict.reasoning})
    retry_count = state["retry_count"] + 1

    if retry_count >= state["max_retries"]:
        return {
            "validation_result": "exhausted",
            "validation_reasoning": verdict.reasoning,
            "retry_count": retry_count,
            "attempts": attempts,
            "token_usage": usage,
        }
    return {
        "validation_result": "retry",
        "validation_reasoning": verdict.reasoning,
        "retry_count": retry_count,
        "attempts": attempts,
        "token_usage": usage,
    }


def root_cause_synthesis(state):
    h = state["current_hypothesis"]
    ev = state["current_evidence"]
    ev_text = _format_evidence(ev)
    prompt = (
        f"Confirmed hypothesis: {h['statement']}\n\nSupporting evidence:\n{ev_text}\n\n"
        "Write a concise root-cause statement, a confidence score between 0 and 1, "
        "and a specific recommended fix (a single CLI command or short sequence)."
    )
    rc, usage = _invoke_structured(llm, RootCause, prompt)
    return {"root_cause": rc.model_dump(), "token_usage": usage}


def record_decision(context: str, fingerprint: str, root_cause: dict, outcome: str) -> None:
    """Shared by the CLI's human_approval node and the Gradio UI's approve/
    reject buttons — the KB writeback shouldn't care which one collected the
    decision. `fingerprint` (see `build_symptom_fingerprint`) is what gets
    embedded for future retrieval; `context` is kept only for the human-readable
    KB panel summary.
    """
    kb.write_case(context, fingerprint, root_cause, outcome)
    logger.info("KB: wrote back case (outcome: %s) for future precedent", outcome)


def record_inconclusive(context: str, fingerprint: str) -> None:
    kb.write_case(context, fingerprint, None, "inconclusive")
    logger.info("KB: wrote back inconclusive case for future precedent")


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
    record_decision(state["context"], state.get("symptom_fingerprint", state["context"]), rc, outcome)
    return {"approval": outcome}


def escalate_inconclusive(state):
    print("\n=== INCONCLUSIVE ===")
    print(f"Exhausted {state['retry_count']} hypotheses without confirmation. Escalating to human review.")
    for i, attempt in enumerate(state.get("attempts", [])):
        h = attempt["hypothesis"]
        print(f"\n[{i}] {h['statement']}")
        print(f"  {_format_evidence(attempt['evidence'])}")
        print(f"  ruled out: {attempt['reasoning']}")
    record_inconclusive(state["context"], state.get("symptom_fingerprint", state["context"]))
    return {}
