"""Gradio dashboard for ASTRA — replaces the CLI's blocking input() approval
prompt with Approve/Reject buttons, and surfaces the diagnosis trace (KB
precedent, ruled-out hypotheses, final root cause) that the CLI only prints
to a terminal.
"""

import os

import gradio as gr
from dotenv import load_dotenv

load_dotenv()

from app.agent import nodes  # noqa: E402
from app.agent.graph import build_diagnosis_graph  # noqa: E402


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
        lines.append(f"{i + 1}. *{h['statement']}* (checked `{h['target_device']}`)")
        lines.append(f"   — ruled out: {attempt['reasoning']}")
    return "\n".join(lines)


def run_diagnosis():
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

    session = {"context": result.get("context"), "root_cause": rc}

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
    nodes.record_decision(session["context"], session["root_cause"], outcome)
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
    demo.launch(server_name="127.0.0.1", server_port=int(os.environ.get("PORT", 7900)))
