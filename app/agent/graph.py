from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent import nodes


class AstraState(TypedDict, total=False):
    logs: dict[str, str]
    topology: dict[str, dict]
    config_drift: dict[str, str]
    context: str
    current_hypothesis: dict
    current_evidence: dict
    attempts: list[dict]
    kb_precedent: list[dict]
    kb_runbooks: list[dict]
    validation_result: Optional[str]
    validation_reasoning: Optional[str]
    retry_count: int
    max_retries: int
    root_cause: Optional[dict]
    approval: Optional[str]


def route_after_validation(state: AstraState) -> str:
    return state["validation_result"]


def _add_diagnosis_nodes(g: StateGraph) -> None:
    """Everything up through root cause / inconclusive — shared by the CLI
    graph (which adds a human_approval node after this) and the UI's
    diagnosis-only graph (which ends here so the UI can collect the approval
    decision itself, without a blocking input() call).
    """
    g.add_node("logs_analyzer", nodes.logs_analyzer)
    g.add_node("topology_analyzer", nodes.topology_analyzer)
    g.add_node("config_analyzer", nodes.config_analyzer)
    g.add_node("context_aggregator", nodes.context_aggregator)
    g.add_node("hypothesis_engine", nodes.hypothesis_engine)
    g.add_node("diagnostic_tool", nodes.diagnostic_tool)
    g.add_node("validation", nodes.validation)
    g.add_node("root_cause_synthesis", nodes.root_cause_synthesis)
    g.add_node("escalate_inconclusive", nodes.escalate_inconclusive)

    # Parallel analysis fan-out
    g.add_edge(START, "logs_analyzer")
    g.add_edge(START, "topology_analyzer")
    g.add_edge(START, "config_analyzer")

    # Fan-in: context_aggregator waits for all three
    g.add_edge("logs_analyzer", "context_aggregator")
    g.add_edge("topology_analyzer", "context_aggregator")
    g.add_edge("config_analyzer", "context_aggregator")

    g.add_edge("context_aggregator", "hypothesis_engine")
    g.add_edge("hypothesis_engine", "diagnostic_tool")
    g.add_edge("diagnostic_tool", "validation")
    g.add_conditional_edges(
        "validation",
        route_after_validation,
        {
            "confirmed": "root_cause_synthesis",
            # A refutation regenerates a new hypothesis informed by everything
            # ruled out so far, rather than advancing through a static list
            # decided upfront.
            "retry": "hypothesis_engine",
            "exhausted": "escalate_inconclusive",
        },
    )


def build_graph():
    """CLI graph — includes the blocking input()-based approval gate."""
    g = StateGraph(AstraState)
    _add_diagnosis_nodes(g)
    g.add_node("human_approval", nodes.human_approval)
    g.add_edge("root_cause_synthesis", "human_approval")
    g.add_edge("human_approval", END)
    g.add_edge("escalate_inconclusive", END)
    return g.compile()


def build_diagnosis_graph():
    """UI graph — stops at root cause / inconclusive; the caller (e.g. the
    Gradio app) collects the approval decision through its own UI and calls
    nodes.record_decision / nodes.record_inconclusive directly instead of a
    blocking input() call.
    """
    g = StateGraph(AstraState)
    _add_diagnosis_nodes(g)
    g.add_edge("root_cause_synthesis", END)
    g.add_edge("escalate_inconclusive", END)
    return g.compile()
