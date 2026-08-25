from app.agent.graph import build_graph, route_after_validation


def test_route_after_validation_passes_through_result():
    for value in ("confirmed", "retry", "exhausted"):
        assert route_after_validation({"validation_result": value}) == value


def test_graph_compiles_with_expected_nodes():
    graph = build_graph()
    node_names = set(graph.get_graph().nodes.keys())
    expected = {
        "logs_analyzer",
        "topology_analyzer",
        "config_analyzer",
        "context_aggregator",
        "hypothesis_engine",
        "diagnostic_tool",
        "validation",
        "root_cause_synthesis",
        "human_approval",
        "escalate_inconclusive",
    }
    assert expected.issubset(node_names)
