"""A device being unreachable used to crash the whole graph run: all three
analyzers (and diagnostic_tool) called `run_commands` directly, and a
connection failure raised straight out of the dict/list comprehension with no
handling. `_safe_run_commands` isolates that failure into evidence instead —
a down device is signal, not a crash. See README "Reliability" for the live
repro (R2_PORT pointed at a closed port) this can't be wired into the
automated multi-scenario eval loop for (DEVICES is resolved once at import).
"""

from netmiko.exceptions import NetmikoTimeoutException

from app.agent import nodes


def test_safe_run_commands_catches_netmiko_exception(monkeypatch):
    def boom(device, commands):
        raise NetmikoTimeoutException("connection timed out")

    monkeypatch.setattr(nodes, "run_commands", boom)

    result = nodes._safe_run_commands("R2", ["show version", "show logging"])

    assert set(result) == {"show version", "show logging"}
    for output in result.values():
        assert nodes.DEVICE_UNREACHABLE_MARKER in output
        assert "connection timed out" in output


def test_safe_run_commands_catches_os_error(monkeypatch):
    def boom(device, commands):
        raise ConnectionRefusedError("[Errno 61] Connection refused")

    monkeypatch.setattr(nodes, "run_commands", boom)

    result = nodes._safe_run_commands("R2", ["show version"])

    assert nodes.DEVICE_UNREACHABLE_MARKER in result["show version"]


def test_safe_run_commands_passes_through_on_success(monkeypatch):
    monkeypatch.setattr(nodes, "run_commands", lambda device, commands: {"show version": "ok"})

    assert nodes._safe_run_commands("R1", ["show version"]) == {"show version": "ok"}


def test_logs_analyzer_survives_one_device_being_unreachable(monkeypatch):
    def flaky(device, commands):
        if device == "R2":
            raise NetmikoTimeoutException("connection timed out")
        return {cmd: f"{device} output" for cmd in commands}

    monkeypatch.setattr(nodes, "run_commands", flaky)
    monkeypatch.setattr(nodes, "DEVICES", {"R1": {}, "R2": {}})

    result = nodes.logs_analyzer({})  # must not raise

    assert result["logs"]["R1"] == "R1 output"
    assert nodes.DEVICE_UNREACHABLE_MARKER in result["logs"]["R2"]


def test_diagnostic_tool_survives_target_device_being_unreachable(monkeypatch):
    def boom(device, commands):
        raise NetmikoTimeoutException("connection timed out")

    monkeypatch.setattr(nodes, "run_commands", boom)

    state = {
        "current_hypothesis": {
            "statement": "R2 is unreachable",
            "target_devices": ["R2"],
            "verify_commands": ["show version"],
        }
    }

    result = nodes.diagnostic_tool(state)  # must not raise

    assert nodes.DEVICE_UNREACHABLE_MARKER in result["current_evidence"]["R2"]["show version"]
