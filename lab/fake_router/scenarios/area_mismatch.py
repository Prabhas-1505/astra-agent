# Fault: R2 was configured with OSPF area 1 for the shared link instead of
# area 0 (matching R1's golden config), so the adjacency is rejected on an
# area-ID mismatch even though the interface and network statement are fine.

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
        "R2                Gig 0/0           147        R           IOSv      Gig 0/0\r\n"
        "Total cdp entries displayed: 1\r\n"
    ),
    "show logging": (
        "*MAR  3 09:12:41.220: %OSPF-4-BADAREAID: Received invalid packet: mismatch area ID, "
        "from backbone area must be virtual-link but not found from 10.0.12.2, GigabitEthernet0/0\r\n"
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
        "GigabitEthernet0/0         10.0.12.2       YES manual up                    up\r\n"
        "Loopback0                  2.2.2.2         YES manual up                    up\r\n"
    ),
    "show ip ospf neighbor": "Total number of neighbors: 0\r\n",
    "show cdp neighbors": (
        "Capability Codes: R - Router, S - Switch\r\n"
        "Device ID        Local Intrfce     Holdtme    Capability  Platform  Port ID\r\n"
        "R1                Gig 0/0           152        R           IOSv      Gig 0/0\r\n"
        "Total cdp entries displayed: 1\r\n"
    ),
    "show logging": (
        "*MAR  3 09:12:41.998: %OSPF-4-BADAREAID: Received invalid packet: mismatch area ID, "
        "from backbone area must be virtual-link but not found from 10.0.12.1, GigabitEthernet0/0\r\n"
    ),
    "show running-config": (
        "interface GigabitEthernet0/0\r\n"
        " ip address 10.0.12.2 255.255.255.0\r\n"
        "!\r\n"
        "router ospf 1\r\n"
        " router-id 2.2.2.2\r\n"
        " network 10.0.12.0 0.0.0.255 area 1\r\n"
        "!\r\n"
    ),
}
