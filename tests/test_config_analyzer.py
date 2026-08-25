from app.agent import nodes


def test_detects_drift_from_golden_config(monkeypatch):
    def fake_run_commands(device_name, commands):
        assert commands == ["show running-config"]
        running = {
            "R1": "interface GigabitEthernet0/0\r\n ip address 10.0.12.1 255.255.255.0\r\n shutdown\r\n!\r\n",
            "R2": "interface GigabitEthernet0/0\r\n ip address 10.0.12.2 255.255.255.0\r\n!\r\n",
        }[device_name]
        return {"show running-config": running}

    monkeypatch.setattr(nodes, "run_commands", fake_run_commands)
    monkeypatch.setattr(
        nodes,
        "GOLDEN_CONFIGS",
        {
            "R1": "interface GigabitEthernet0/0\r\n ip address 10.0.12.1 255.255.255.0\r\n!\r\n",
            "R2": "interface GigabitEthernet0/0\r\n ip address 10.0.12.2 255.255.255.0\r\n!\r\n",
        },
    )
    monkeypatch.setattr(nodes, "DEVICES", {"R1": {}, "R2": {}})

    result = nodes.config_analyzer({})

    assert "+ shutdown" in result["config_drift"]["R1"]
    assert result["config_drift"]["R2"] == "(no drift from golden config)"


def test_no_golden_entry_is_treated_as_full_drift(monkeypatch):
    monkeypatch.setattr(
        nodes,
        "run_commands",
        lambda device_name, commands: {"show running-config": "interface Gi0/0\r\n!\r\n"},
    )
    monkeypatch.setattr(nodes, "GOLDEN_CONFIGS", {})
    monkeypatch.setattr(nodes, "DEVICES", {"R3": {}})

    result = nodes.config_analyzer({})

    assert "interface Gi0/0" in result["config_drift"]["R3"]
