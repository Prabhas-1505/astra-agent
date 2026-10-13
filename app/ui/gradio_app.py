"""Gradio dashboard for ASTRA — replaces the CLI's blocking input() approval
prompt with Approve/Reject buttons, and surfaces the diagnosis trace (KB
precedent, ruled-out hypotheses, final root cause) that the CLI only prints
to a terminal.
"""

import os

import gradio as gr
from dotenv import load_dotenv

load_dotenv()

from app.agent import nodes
from app.agent.graph import build_diagnosis_graph
from app.logging_config import configure_logging, new_correlation_id

configure_logging()


def _topology_svg() -> str:
    scenario = os.environ.get("FAULT_SCENARIO", "gi_shutdown")

    if scenario == "gi_shutdown":
        link_color = "#d33"
        r1_if_status, r1_if_color = "ADMIN DOWN", "#d33"
        r2_if_status, r2_if_color = "UP", "#2a9d4a"
        ospf_label, ospf_color = "NO ADJACENCY — R1's link interface is shut down", "#d33"
        fault_note = "Fault: GigabitEthernet0/0 on R1 was shut down, so the link itself is physically down."
    elif scenario == "area_mismatch":
        link_color = "#2a9d4a"
        r1_if_status, r1_if_color = "UP", "#2a9d4a"
        r2_if_status, r2_if_color = "UP", "#2a9d4a"
        ospf_label, ospf_color = "NO ADJACENCY — area ID mismatch (R1 area 0 vs R2 area 1)", "#d33"
        fault_note = "Fault: the link is physically fine, but R2's OSPF area ID doesn't match R1's, so the neighbor relationship is rejected."
    elif scenario == "duplicate_ip":
        link_color = "#e8a33d"
        r1_if_status, r1_if_color = "UP", "#2a9d4a"
        r2_if_status, r2_if_color = "UP (DUP IP)", "#e8a33d"
        ospf_label, ospf_color = "NO ADJACENCY — duplicate IP address (both 10.0.12.1)", "#d33"
        fault_note = "Fault: R2's interface was misconfigured with R1's IP (10.0.12.1), so both sides see a duplicate-address conflict."
    elif scenario == "mtu_mismatch":
        link_color = "#2a9d4a"
        r1_if_status, r1_if_color = "UP (MTU 1400)", "#e8a33d"
        r2_if_status, r2_if_color = "UP (MTU 1500)", "#2a9d4a"
        ospf_label, ospf_color = "STUCK EXSTART — MTU mismatch (R1 1400 vs R2 1500)", "#d33"
        fault_note = "Fault: R1's interface MTU (1400) doesn't match R2's (1500), so OSPF's DBD exchange never completes."
    else:  # ospf_network_missing
        link_color = "#2a9d4a"
        r1_if_status, r1_if_color = "UP", "#2a9d4a"
        r2_if_status, r2_if_color = "UP", "#2a9d4a"
        ospf_label, ospf_color = "NO ADJACENCY — OSPF never enabled on R1's interface", "#d33"
        fault_note = "Fault: the link is physically fine, but R1 never got a `network` statement for this interface, so OSPF was never turned on there."

    def router_box(x: float, name: str, role: str, router_id: str) -> str:
        return f"""
          <rect x="{x}" y="40" width="160" height="80" rx="10" fill="#2d3436" />
          <text x="{x + 80}" y="62" fill="white" text-anchor="middle" font-size="15" font-weight="bold">Router {name}</text>
          <text x="{x + 80}" y="80" fill="#bbb" text-anchor="middle" font-size="10">{role}</text>
          <text x="{x + 80}" y="98" fill="white" text-anchor="middle" font-size="11">Router ID (Lo0): {router_id}</text>
        """

    def status_pill(cx: float, label: str, color: str) -> str:
        return f"""
          <rect x="{cx - 48}" y="130" width="96" height="20" rx="10" fill="{color}" />
          <text x="{cx}" y="144" fill="white" text-anchor="middle" font-size="10" font-weight="bold">{label}</text>
        """

    return f"""
    <div style="display:flex;justify-content:center;padding:8px 0;">
    <svg width="640" height="340" viewBox="0 0 640 340" xmlns="http://www.w3.org/2000/svg"
         font-family="sans-serif">
      {router_box(20, "R1", "simulated via SSH, port 2201", "1.1.1.1")}
      {router_box(460, "R2", "simulated via SSH, port 2202", "2.2.2.2")}

      <line x1="180" y1="80" x2="460" y2="80" stroke="{link_color}" stroke-width="4" />
      <text x="320" y="68" text-anchor="middle" font-size="11" fill="#555">Link subnet: 10.0.12.0/24</text>

      <text x="180" y="115" text-anchor="middle" font-size="10" fill="#555">Interface: Gi0/0</text>
      <text x="180" y="128" text-anchor="middle" font-size="10" fill="#555">IP: 10.0.12.1</text>
      {status_pill(180, r1_if_status, r1_if_color)}

      <text x="460" y="115" text-anchor="middle" font-size="10" fill="#555">Interface: Gi0/0</text>
      <text x="460" y="128" text-anchor="middle" font-size="10" fill="#555">IP: 10.0.12.2</text>
      {status_pill(460, r2_if_status, r2_if_color)}

      <rect x="60" y="175" width="520" height="44" rx="8" fill="none" stroke="{ospf_color}" stroke-width="1.5" />
      <text x="320" y="192" text-anchor="middle" font-size="11" font-weight="bold" fill="#333">OSPF process 1, area 0</text>
      <text x="320" y="208" text-anchor="middle" font-size="11" font-weight="bold" fill="{ospf_color}">{ospf_label}</text>

      <text x="320" y="245" text-anchor="middle" font-size="11" fill="#444">{fault_note}</text>

      <rect x="150" y="270" width="12" height="12" fill="#2a9d4a" />
      <text x="168" y="280" font-size="10" fill="#555">= healthy / up</text>
      <rect x="330" y="270" width="12" height="12" fill="#d33" />
      <text x="348" y="280" font-size="10" fill="#555">= faulted / down</text>

      <text x="320" y="305" text-anchor="middle" font-size="10" fill="#888">
        Active scenario: {scenario} (set via FAULT_SCENARIO env var on lab/docker-compose.yml)
      </text>
    </svg>
    </div>
    """


def _format_kb_matches(result: dict) -> str:
    precedent = result.get("kb_precedent") or []
    runbooks = result.get("kb_runbooks") or []
    if not precedent and not runbooks:
        return "_No precedent cases or matching runbooks yet — this is a novel incident to the KB._"

    lines = []
    if runbooks:
        lines.append("**Matched runbook(s):**")
        for rb in runbooks:
            lines.append(f"- {rb['title']}")
    if precedent:
        lines.append("\n**Precedent from past incidents:**")
        for case in precedent:
            rc = case.get("root_cause") or {}
            lines.append(
                f"- outcome: `{case['outcome']}` — {rc.get('statement', 'n/a')}"
            )
    return "\n".join(lines)


def _format_attempts(attempts: list[dict]) -> str:
    if not attempts:
        return "_Confirmed on the first hypothesis — no retries needed._"
    lines = [f"**{len(attempts)} hypothesis(es) ruled out before reaching the final one:**\n"]
    for i, attempt in enumerate(attempts):
        h = attempt["hypothesis"]
        lines.append(f"{i + 1}. *{h['statement']}* (checked `{', '.join(h['target_devices'])}`)")
        lines.append(f"   — ruled out: {attempt['reasoning']}")
    return "\n".join(lines)


def run_diagnosis():
    new_correlation_id()
    graph = build_diagnosis_graph()
    result = graph.invoke({"max_retries": 3})

    kb_md = _format_kb_matches(result)
    attempts_md = _format_attempts(result.get("attempts", []))

    rc = result.get("root_cause")
    if rc:
        root_cause_md = (
            f"### Root cause\n{rc['statement']}\n\n"
            f"**Confidence:** {rc['confidence']:.2f}\n\n"
            f"**Recommended fix:**\n```\n{rc['recommended_fix']}\n```"
        )
        buttons_visible = True
    else:
        root_cause_md = (
            "### Inconclusive\n"
            f"Exhausted {result.get('retry_count', 0)} hypotheses without confirmation. "
            "Escalated for manual review — see ruled-out attempts above."
        )
        buttons_visible = False

    session = {
        "context": result.get("context"),
        "symptom_fingerprint": result.get("symptom_fingerprint"),
        "root_cause": rc,
    }

    return (
        kb_md,
        attempts_md,
        root_cause_md,
        session,
        gr.update(visible=buttons_visible),
        gr.update(visible=buttons_visible),
        "",
    )


def decide(session, outcome):
    if not session or not session.get("root_cause"):
        return "Nothing to decide — run a diagnosis first."
    nodes.record_decision(
        session["context"], session.get("symptom_fingerprint", session["context"]),
        session["root_cause"], outcome,
    )
    verb = "Approved" if outcome == "approved" else "Rejected"
    note = (
        "ASTRA does not auto-execute — apply the fix manually."
        if outcome == "approved"
        else "No fix applied."
    )
    return f"**{verb}.** {note} Outcome written back to the KB for future precedent."


def approve(session):
    return decide(session, "approved"), gr.update(visible=False), gr.update(visible=False)


def reject(session):
    return decide(session, "rejected"), gr.update(visible=False), gr.update(visible=False)


with gr.Blocks(title="ASTRA — Network Diagnostics Agent") as demo:
    gr.Markdown(
        "# ASTRA — Autonomous Network Diagnostics Agent\n"
        "Diagnoses the current fault in the lab (`lab/docker-compose.yml`), showing its "
        "reasoning trail, not just a final answer."
    )

    gr.Markdown("#### Lab topology")
    gr.HTML(_topology_svg())

    run_btn = gr.Button("Run Diagnosis", variant="primary")

    with gr.Row():
        with gr.Column():
            gr.Markdown("#### Knowledge base")
            kb_output = gr.Markdown()
            gr.Markdown("#### Hypotheses ruled out")
            attempts_output = gr.Markdown()
        with gr.Column():
            gr.Markdown("#### Result")
            root_cause_output = gr.Markdown()
            with gr.Row():
                approve_btn = gr.Button("Approve fix", variant="primary", visible=False)
                reject_btn = gr.Button("Reject", variant="stop", visible=False)
            decision_output = gr.Markdown()

    session_state = gr.State()

    run_btn.click(
        run_diagnosis,
        outputs=[
            kb_output,
            attempts_output,
            root_cause_output,
            session_state,
            approve_btn,
            reject_btn,
            decision_output,
        ],
    )
    approve_btn.click(
        approve, inputs=[session_state], outputs=[decision_output, approve_btn, reject_btn]
    )
    reject_btn.click(
        reject, inputs=[session_state], outputs=[decision_output, approve_btn, reject_btn]
    )


if __name__ == "__main__":
    # 0.0.0.0 in the containerized flow (docker-compose.yml's `ui` service sets
    # GRADIO_SERVER_NAME) — binding loopback-only inside a container is
    # unreachable through Docker's port mapping even though the mapping itself
    # looks correct. Defaults to loopback for the host-venv flow, where binding
    # every interface would needlessly expose the dashboard to the LAN.
    demo.launch(
        server_name=os.environ.get("GRADIO_SERVER_NAME", "127.0.0.1"),
        server_port=int(os.environ.get("PORT", 7900)),
    )
