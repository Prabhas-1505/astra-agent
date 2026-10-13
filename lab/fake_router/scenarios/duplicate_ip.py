# Fault: R2's link interface was accidentally reconfigured with R1's IP
# address (10.0.12.1) instead of its own (10.0.12.2) — e.g. a copy-paste error
# during a change window. Both interfaces come up fine at L1/L2, but the IP
# conflict prevents OSPF from forming a stable adjacency.

R1 = {
    "show version": (
        "Cisco IOS Software, IOSv Software\r\n"
        "R1 uptime is 3 days, 4 hours, 12 minutes\r\n"
    ),
    "show ip interface brief": (
        "Interface                  IP-Address      OK? Method Status                Protocol\r\n"
        "GigabitEthernet0/0         10.0.12.1       YES manual up                    up\r\n"
        "Loopback0                  1.1.1.1         YES manual up                    up\r\n"
    ),
    "show ip ospf neighbor": "Total number of neighbors: 0\r\n",
    "show cdp neighbors": (
        "Capability Codes: R - Router, S - Switch\r\n"
        "Device ID        Local Intrfce     Holdtme    Capability  Platform  Port ID\r\n"
        "R2                Gig 0/0           144        R           IOSv      Gig 0/0\r\n"
        "Total cdp entries displayed: 1\r\n"
    ),
    "show logging": (
        "*MAR  3 09:16:10.040: %IP-4-DUPADDR: Duplicate address 10.0.12.1 on "
        "GigabitEthernet0/0, sourced by aabb.cc00.0200\r\n"
    ),
    "show running-config": (
        "interface GigabitEthernet0/0\r\n"
        " ip address 10.0.12.1 255.255.255.0\r\n"
        "!\r\n"
        "router ospf 1\r\n"
        " router-id 1.1.1.1\r\n"
        " network 10.0.12.0 0.0.0.255 area 0\r\n"
        "!\r\n"
    ),
}

R2 = {
    "show version": (
        "Cisco IOS Software, IOSv Software\r\n"
        "R2 uptime is 3 days, 4 hours, 9 minutes\r\n"
    ),
    "show ip interface brief": (
        "Interface                  IP-Address      OK? Method Status                Protocol\r\n"
        "GigabitEthernet0/0         10.0.12.1       YES manual up                    up\r\n"
        "Loopback0                  2.2.2.2         YES manual up                    up\r\n"
    ),
    "show ip ospf neighbor": "Total number of neighbors: 0\r\n",
    "show cdp neighbors": (
        "Capability Codes: R - Router, S - Switch\r\n"
        "Device ID        Local Intrfce     Holdtme    Capability  Platform  Port ID\r\n"
        "R1                Gig 0/0           151        R           IOSv      Gig 0/0\r\n"
        "Total cdp entries displayed: 1\r\n"
    ),
    "show logging": (
        "*MAR  3 09:16:10.882: %IP-4-DUPADDR: Duplicate address 10.0.12.1 on "
        "GigabitEthernet0/0, sourced by aabb.cc00.0100\r\n"
    ),
    "show running-config": (
        "interface GigabitEthernet0/0\r\n"
        " ip address 10.0.12.1 255.255.255.0\r\n"
        "!\r\n"
        "router ospf 1\r\n"
        " router-id 2.2.2.2\r\n"
        " network 10.0.12.0 0.0.0.255 area 0\r\n"
        "!\r\n"
    ),
}
