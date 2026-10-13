# Static runbook content — the "R" ASTRA actually retrieves from, distinct
# from the learned past-cases KB. In a real deployment these would be pulled
# from a runbook wiki/repo; here they're seeded directly.

RUNBOOKS = [
    {
        "title": "Interface administratively down",
        "body": (
            "Symptom: an OSPF (or any L3) adjacency drops with no prior "
            "warning, and `show ip interface brief` shows the interface as "
            "'administratively down'. Cause: someone (or a prior change) "
            "issued `shutdown` on the interface. Fix: `no shutdown` under "
            "the interface. Always check `show running-config` for a "
            "`shutdown` line before assuming a hardware or cabling fault."
        ),
    },
    {
        "title": "OSPF neighbor never forms — missing network statement",
        "body": (
            "Symptom: the interface is up/up, IP addressing is correct on "
            "both ends, but `show ip ospf neighbor` shows zero neighbors and "
            "no ADJCHG log ever fired (because the adjacency never started, "
            "it didn't drop). Cause: the interface's subnet was never added "
            "to the OSPF process via a `network <addr> <wildcard> area <n>` "
            "statement. Fix: add the missing `network` statement under "
            "`router ospf`. Distinguish this from a down interface by "
            "checking `show ip interface brief` first — if it's up/up, look "
            "at the OSPF process config next, not the interface."
        ),
    },
    {
        "title": "OSPF neighbor rejected — area ID mismatch",
        "body": (
            "Symptom: interface up/up, network statement present on both "
            "sides, but neighbors still don't form; logs show "
            "%OSPF-4-BADAREAID. Cause: the two ends have different area IDs "
            "configured for the same link (e.g. one side area 0, the other "
            "area 1). Fix: align the `area` value in both sides' `network` "
            "statements under `router ospf`. This is a config-drift issue, "
            "not a link/interface issue — diff running-config against the "
            "golden baseline to catch it quickly."
        ),
    },
    {
        "title": "Duplicate IP address on the link",
        "body": (
            "Symptom: both interfaces show up/up and CDP sees the correct "
            "neighbor, but OSPF neighbor count stays 0; logs show "
            "%IP-4-DUPADDR on one or both sides. Cause: two devices on the "
            "same subnet were configured with the same IP address (often a "
            "copy-paste error during a change window). Fix: correct the "
            "duplicated device's `ip address` back to its intended unique "
            "address — diff running-config against the golden baseline to "
            "see which side actually drifted."
        ),
    },
    {
        "title": "OSPF neighbor stuck pre-FULL — MTU mismatch",
        "body": (
            "Symptom: interface up/up, network statement correct on both "
            "sides, but the neighbor repeatedly cycles EXSTART -> DOWN with "
            "'Too many retransmissions' in %OSPF-5-ADJCHG instead of ever "
            "reaching FULL. Cause: the two ends have mismatched interface "
            "MTU, so OSPF's database-description exchange never completes "
            "(IOS rejects DBD packets on an MTU mismatch unless `ip ospf "
            "mtu-ignore` is set). Fix: align the `mtu` value on both "
            "interfaces — diff running-config against the golden baseline to "
            "spot the drifted `mtu` line."
        ),
    },
]
