from app.agent import nodes
from tests.conftest import FakeLLM


def _base_state(max_retries=3, retry_count=0, attempts=None):
    return {
        "current_hypothesis": {
            "statement": "h0",
            "target_device": "R1",
            "verify_commands": ["show version"],
        },
        "current_evidence": {"show version": "irrelevant output"},
        "attempts": attempts or [],
        "retry_count": retry_count,
        "max_retries": max_retries,
    }


def test_confirmed_hypothesis_stops_the_loop(monkeypatch):
    verdict = nodes.ValidationVerdict(confirmed=True, reasoning="matches evidence")
    monkeypatch.setattr(nodes, "llm", FakeLLM([verdict]))

    result = nodes.validation(_base_state())

    assert result["validation_result"] == "confirmed"
    assert "attempts" not in result  # nothing to record, hypothesis was correct


def test_refuted_hypothesis_retries_and_records_the_attempt(monkeypatch):
    verdict = nodes.ValidationVerdict(confirmed=False, reasoning="does not match")
    monkeypatch.setattr(nodes, "llm", FakeLLM([verdict]))

    result = nodes.validation(_base_state())

    assert result["validation_result"] == "retry"
    assert result["retry_count"] == 1
    assert len(result["attempts"]) == 1
    assert result["attempts"][0]["hypothesis"]["statement"] == "h0"
    assert result["attempts"][0]["reasoning"] == "does not match"


def test_exhausts_after_max_retries(monkeypatch):
    verdict = nodes.ValidationVerdict(confirmed=False, reasoning="does not match")
    monkeypatch.setattr(nodes, "llm", FakeLLM([verdict]))

    state = _base_state(max_retries=3, retry_count=2, attempts=[{"hypothesis": {"statement": "prior"}, "evidence": {}, "reasoning": "nope"}])

    result = nodes.validation(state)

    assert result["validation_result"] == "exhausted"
    assert result["retry_count"] == 3
    assert len(result["attempts"]) == 2  # prior attempt plus this one


def test_hypothesis_engine_carries_ruled_out_history_into_the_prompt(monkeypatch):
    captured = {}

    class CapturingStructuredLLM:
        def invoke(self, prompt):
            captured["prompt"] = prompt
            return nodes.Hypothesis(
                statement="a refined guess",
                target_device="R1",
                verify_commands=["show running-config"],
            )

    class CapturingLLM:
        def with_structured_output(self, _model):
            return CapturingStructuredLLM()

    monkeypatch.setattr(nodes, "llm", CapturingLLM())

    state = {
        "context": "some incident context",
        "attempts": [
            {
                "hypothesis": {
                    "statement": "wrong first guess",
                    "target_device": "R1",
                    "verify_commands": ["show version"],
                },
                "evidence": {"show version": "some output"},
                "reasoning": "evidence did not support it",
            }
        ],
        "retry_count": 1,
    }

    result = nodes.hypothesis_engine(state)

    assert result["current_hypothesis"]["statement"] == "a refined guess"
    assert "wrong first guess" in captured["prompt"]
    assert "evidence did not support it" in captured["prompt"]
    assert result["attempts"] == state["attempts"]  # history passed through unchanged
