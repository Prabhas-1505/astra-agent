from app.agent.nodes import build_symptom_fingerprint


def _state(ip_status="up                    up", cdp_count=1, drift="(no drift from golden config)", logs=""):
    return {
        "topology": {
            "R1": {
                "show ip interface brief": (
                    "Interface                  IP-Address      OK? Method Status                Protocol\r\n"
                    f"GigabitEthernet0/0         10.0.12.1       YES manual {ip_status}\r\n"
                ),
                "show cdp neighbors": (
                    "Capability Codes: R - Router, S - Switch\r\n"
                    f"Total cdp entries displayed: {cdp_count}\r\n"
                ),
            }
        },
        "config_drift": {"R1": drift},
        "logs": {"R1": logs},
    }


def test_fingerprint_parses_interface_status():
    fp = build_symptom_fingerprint(_state(ip_status="administratively down down"))
    assert "GigabitEthernet0/0=administratively down/down" in fp


def test_fingerprint_reports_no_drift():
    fp = build_symptom_fingerprint(_state(drift="(no drift from golden config)"))
    assert "R1 config: no drift" in fp


def test_fingerprint_extracts_changed_drift_lines_only():
    diff = (
        "--- R1-golden\n+++ R1-running\n"
        "@@ -1,3 +1,4 @@\n"
        " interface GigabitEthernet0/0\n"
        "+ shutdown\n"
        " !\n"
    )
    fp = build_symptom_fingerprint(_state(drift=diff))
    assert "+ shutdown" in fp
    assert "@@" not in fp  # hunk headers aren't signal, shouldn't leak into the fingerprint


def test_fingerprint_extracts_syslog_codes():
    logs = (
        "*MAR  3 09:14:02.113: %LINK-3-UPDOWN: Interface Gi0/0 down\r\n"
        "*MAR  3 09:14:05.220: %OSPF-5-ADJCHG: Process 1, Nbr down\r\n"
    )
    fp = build_symptom_fingerprint(_state(logs=logs))
    assert "%LINK-3-UPDOWN" in fp
    assert "%OSPF-5-ADJCHG" in fp


def test_fingerprint_is_order_independent_of_boilerplate():
    # Two states with identical signal but different uptime/banner boilerplate
    # (which the full `context` would include) should produce the *same*
    # fingerprint, since that boilerplate carries no diagnostic signal.
    a = build_symptom_fingerprint(_state())
    b = build_symptom_fingerprint(_state())
    assert a == b
