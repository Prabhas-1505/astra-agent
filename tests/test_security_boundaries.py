"""Negative tests for the boundaries closed in this pass: ALLOWED_COMMANDS and
DEVICES are now enforced in code (app/agent/nodes.py::diagnostic_tool), not just
asked for in prompt text, and the Netmiko client has no source-code credential
fallback. These test that a hallucinating or adversarially-steered LLM can't
reach an unauthorized command/device, and that missing credentials fail loudly
rather than silently defaulting.
"""

import pytest

from app.agent import nodes
from app.tools import netmiko_tool


def test_diagnostic_tool_drops_non_allowlisted_commands(monkeypatch):
    calls = []
    monkeypatch.setattr(
        nodes, "run_commands", lambda device, commands: calls.append((device, commands)) or {}
    )

    state = {
        "current_hypothesis": {
            "statement": "whatever",
            "target_devices": ["R1"],
            # "reload" and "write erase" are not in ALLOWED_COMMANDS — exactly
            # the kind of thing a hallucinating or injected LLM output could
            # propose, and the old code sent it to the device unchecked.
            "verify_commands": ["show ip interface brief", "reload", "write erase"],
        }
    }

    result = nodes.diagnostic_tool(state)

    assert len(calls) == 1
    assert calls[0] == ("R1", ["show ip interface brief"])
    assert result["current_evidence"] == {"R1": {}}  # run_commands was mocked to return {}


def test_diagnostic_tool_drops_unknown_devices(monkeypatch):
    calls = []
    monkeypatch.setattr(
        nodes, "run_commands", lambda device, commands: calls.append((device, commands)) or {}
    )

    state = {
        "current_hypothesis": {
            "statement": "whatever",
            # R9 doesn't exist — a hallucinated device name used to reach
            # netmiko_tool.py's `DEVICES[device_name]` and crash with KeyError.
            "target_devices": ["R1", "R9"],
            "verify_commands": ["show ip interface brief"],
        }
    }

    result = nodes.diagnostic_tool(state)

    assert calls == [("R1", ["show ip interface brief"])]
    assert "R9" not in result["current_evidence"]


def test_diagnostic_tool_returns_empty_evidence_without_crashing_when_nothing_is_valid(monkeypatch):
    def fail_if_called(device, commands):
        raise AssertionError("run_commands should never be called with no valid input")

    monkeypatch.setattr(nodes, "run_commands", fail_if_called)

    state = {
        "current_hypothesis": {
            "statement": "whatever",
            "target_devices": ["R9"],
            "verify_commands": ["reload"],
        }
    }

    result = nodes.diagnostic_tool(state)

    assert result == {"current_evidence": {}}


def test_run_commands_fails_loudly_without_credentials(monkeypatch):
    monkeypatch.setattr(
        netmiko_tool,
        "DEVICES",
        {"R1": {"device_type": "cisco_ios", "host": "127.0.0.1", "port": 2201,
                "username": None, "password": None}},
    )

    with pytest.raises(RuntimeError, match="ROUTER_USER and ROUTER_PASS"):
        netmiko_tool.run_commands("R1", ["show version"])
