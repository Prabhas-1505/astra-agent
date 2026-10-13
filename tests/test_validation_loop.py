from app.agent import nodes
from tests.conftest import _DEFAULT_FAKE_USAGE, FakeLLM, _FakeRawMessage


def _fake_raw():
    return _FakeRawMessage(_DEFAULT_FAKE_USAGE)


def _base_state(max_retries=3, retry_count=0, attempts=None):
    return {
        "current_hypothesis": {
            "statement": "h0",
            "target_devices": ["R1"],
            "verify_commands": ["show version"],
        },
        "current_evidence": {"R1": {"show version": "irrelevant output"}},
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

    state = _base_state(
        max_retries=3,
        retry_count=2,
        attempts=[{"hypothesis": {"statement": "prior"}, "evidence": {}, "reasoning": "nope"}],
    )

    result = nodes.validation(state)

    assert result["validation_result"] == "exhausted"
    assert result["retry_count"] == 3
    assert len(result["attempts"]) == 2  # prior attempt plus this one


def test_diagnostic_tool_gathers_evidence_from_every_target_device(monkeypatch):
    """The core of the cross-device-verification fix: a hypothesis naming two
    devices (e.g. an MTU/area mismatch) must get evidence from *both*, not just
    the first — the eval harness's live mtu_mismatch 0/3 result was exactly this
    gap (see README "Eval harness")."""
    calls = []

    def fake_run_commands(device_name, commands):
        calls.append(device_name)
        return {cmd: f"{device_name} output for {cmd}" for cmd in commands}

    monkeypatch.setattr(nodes, "run_commands", fake_run_commands)

    state = {
        "current_hypothesis": {
            "statement": "MTU mismatch between R1 and R2",
            "target_devices": ["R1", "R2"],
            "verify_commands": ["show running-config"],
        }
    }

    result = nodes.diagnostic_tool(state)

    assert calls == ["R1", "R2"]
    assert result["current_evidence"] == {
        "R1": {"show running-config": "R1 output for show running-config"},
        "R2": {"show running-config": "R2 output for show running-config"},
    }


def test_format_evidence_renders_all_devices():
    evidence = {
        "R1": {"show running-config": "mtu 1400"},
        "R2": {"show running-config": "no mtu line"},
    }

    formatted = nodes._format_evidence(evidence)

    assert "--- R1 ---" in formatted
    assert "mtu 1400" in formatted
    assert "--- R2 ---" in formatted
    assert "no mtu line" in formatted


def test_hypothesis_engine_carries_ruled_out_history_into_the_prompt(monkeypatch):
    captured = {}

    class CapturingStructuredLLM:
        def invoke(self, prompt):
            captured["prompt"] = prompt
            parsed = nodes.Hypothesis(
                statement="a refined guess",
                target_devices=["R1"],
                verify_commands=["show running-config"],
            )
            return {"parsed": parsed, "raw": _fake_raw(), "parsing_error": None}

    class CapturingLLM:
        def with_structured_output(self, _model, include_raw=False):
            return CapturingStructuredLLM()

    monkeypatch.setattr(nodes, "llm", CapturingLLM())

    state = {
        "context": "some incident context",
        "attempts": [
            {
                "hypothesis": {
                    "statement": "wrong first guess",
                    "target_devices": ["R1"],
                    "verify_commands": ["show version"],
                },
                "evidence": {"R1": {"show version": "some output"}},
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


def test_hypothesis_engine_ranks_two_candidates_on_first_pass(monkeypatch):
    monkeypatch.setattr(nodes, "_kb_enabled", lambda: False)

    class RankingStructuredLLM:
        def invoke(self, prompt):
            parsed = nodes.RankedHypotheses(
                primary=nodes.Hypothesis(
                    statement="primary guess", target_devices=["R1"], verify_commands=["show version"]
                ),
                secondary=nodes.Hypothesis(
                    statement="secondary guess",
                    target_devices=["R1", "R2"],
                    verify_commands=["show version"],
                ),
            )
            return {"parsed": parsed, "raw": _fake_raw(), "parsing_error": None}

    class RankingLLM:
        def with_structured_output(self, _model, include_raw=False):
            return RankingStructuredLLM()

    monkeypatch.setattr(nodes, "llm", RankingLLM())

    result = nodes.hypothesis_engine({"context": "some incident context", "retry_count": 0})

    assert result["current_hypothesis"]["statement"] == "primary guess"
    assert result["pending_hypotheses"] == [
        {
            "statement": "secondary guess",
            "target_devices": ["R1", "R2"],
            "verify_commands": ["show version"],
        }
    ]


def test_hypothesis_engine_advances_pending_without_llm_call(monkeypatch):
    def fail_if_called(_model, include_raw=False):
        raise AssertionError("should not call the LLM when a pending hypothesis is available")

    monkeypatch.setattr(
        nodes, "llm", type("Boom", (), {"with_structured_output": staticmethod(fail_if_called)})()
    )

    state = {
        "context": "some incident context",
        "attempts": [
            {
                "hypothesis": {
                    "statement": "primary guess", "target_devices": ["R1"], "verify_commands": []
                },
                "evidence": {},
                "reasoning": "refuted",
            }
        ],
        "pending_hypotheses": [
            {
                "statement": "secondary guess",
                "target_devices": ["R1", "R2"],
                "verify_commands": ["show version"],
            }
        ],
        "retry_count": 1,
    }

    result = nodes.hypothesis_engine(state)

    assert result["current_hypothesis"]["statement"] == "secondary guess"
    assert result["current_hypothesis"]["target_devices"] == ["R1", "R2"]
    assert result["pending_hypotheses"] == []
