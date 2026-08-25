from app.agent import nodes
from tests.conftest import FakeLLM


def _base_state(max_retries=3):
    return {
        "hypotheses": [
            {"statement": "h0", "target_device": "R1", "verify_commands": ["show version"]},
            {"statement": "h1", "target_device": "R1", "verify_commands": ["show version"]},
            {"statement": "h2", "target_device": "R1", "verify_commands": ["show version"]},
        ],
        "hypothesis_index": 0,
        "retry_count": 0,
        "max_retries": max_retries,
        "evidence": {0: {"show version": "irrelevant output"}},
    }


def test_confirmed_hypothesis_stops_the_loop(monkeypatch):
    verdict = nodes.ValidationVerdict(confirmed=True, reasoning="matches evidence")
    monkeypatch.setattr(nodes, "llm", FakeLLM([verdict]))

    result = nodes.validation(_base_state())

    assert result["validation_result"] == "confirmed"
    assert "hypothesis_index" not in result  # stays on the confirmed hypothesis


def test_refuted_hypothesis_retries_with_next_index(monkeypatch):
    verdict = nodes.ValidationVerdict(confirmed=False, reasoning="does not match")
    monkeypatch.setattr(nodes, "llm", FakeLLM([verdict]))

    result = nodes.validation(_base_state())

    assert result["validation_result"] == "retry"
    assert result["hypothesis_index"] == 1
    assert result["retry_count"] == 1


def test_exhausts_after_max_retries(monkeypatch):
    verdict = nodes.ValidationVerdict(confirmed=False, reasoning="does not match")
    monkeypatch.setattr(nodes, "llm", FakeLLM([verdict]))

    state = _base_state(max_retries=3)
    state["hypothesis_index"] = 2  # already on the last hypothesis
    state["retry_count"] = 2  # one refutation away from the cap
    state["evidence"] = {2: {"show version": "irrelevant output"}}

    result = nodes.validation(state)

    assert result["validation_result"] == "exhausted"
    assert result["retry_count"] == 3


def test_exhausts_when_out_of_hypotheses_even_under_retry_cap(monkeypatch):
    verdict = nodes.ValidationVerdict(confirmed=False, reasoning="does not match")
    monkeypatch.setattr(nodes, "llm", FakeLLM([verdict]))

    state = _base_state(max_retries=10)  # cap far away, but hypotheses run out first
    state["hypothesis_index"] = 2
    state["evidence"] = {2: {"show version": "irrelevant output"}}

    result = nodes.validation(state)

    assert result["validation_result"] == "exhausted"
