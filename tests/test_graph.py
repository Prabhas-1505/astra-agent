from app.agent.graph import _merge_token_usage, build_graph, route_after_validation


def test_merge_token_usage_sums_totals_and_ignores_nested_detail_dicts():
    a = {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
        "input_token_details": {"audio": 0, "cache_read": 0},
    }
    b = {
        "input_tokens": 3,
        "output_tokens": 2,
        "total_tokens": 5,
        "output_token_details": {"audio": 0, "reasoning": 1},
    }

    assert _merge_token_usage(a, b) == {
        "input_tokens": 13,
        "output_tokens": 7,
        "total_tokens": 20,
    }


def test_merge_token_usage_handles_missing_sides():
    assert _merge_token_usage(None, {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}) == {
        "input_tokens": 1,
        "output_tokens": 1,
        "total_tokens": 2,
    }
    assert _merge_token_usage(None, None) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }


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
