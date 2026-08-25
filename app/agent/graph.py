from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent import nodes


class AstraState(TypedDict, total=False):
    logs: dict[str, str]
    topology: dict[str, dict]
    config_drift: dict[str, str]
    context: str
    hypotheses: list[dict]
    hypothesis_index: int
    evidence: dict[int, dict]
    validation_result: Optional[str]
    validation_reasoning: Optional[str]
    retry_count: int
    max_retries: int
    root_cause: Optional[dict]
    approval: Optional[str]


def route_after_validation(state: AstraState) -> str:
    return state["validation_result"]


def build_graph():
    g = StateGraph(AstraState)
    g.add_node("logs_analyzer", nodes.logs_analyzer)
    g.add_node("topology_analyzer", nodes.topology_analyzer)
    g.add_node("config_analyzer", nodes.config_analyzer)
    g.add_node("context_aggregator", nodes.context_aggregator)
    g.add_node("hypothesis_engine", nodes.hypothesis_engine)
    g.add_node("diagnostic_tool", nodes.diagnostic_tool)
    g.add_node("validation", nodes.validation)
    g.add_node("root_cause_synthesis", nodes.root_cause_synthesis)
    g.add_node("human_approval", nodes.human_approval)
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
            "retry": "diagnostic_tool",
            "exhausted": "escalate_inconclusive",
        },
    )
    g.add_edge("root_cause_synthesis", "human_approval")
    g.add_edge("human_approval", END)
    g.add_edge("escalate_inconclusive", END)
    return g.compile()
