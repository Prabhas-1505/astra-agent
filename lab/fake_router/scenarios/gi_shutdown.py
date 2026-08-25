# Fault: GigabitEthernet0/0 on R1 (the only link to R2) is administratively
# shut down, dropping the OSPF adjacency.

R1 = {
    "show version": (
        "Cisco IOS Software, IOSv Software\r\n"
        "R1 uptime is 3 days, 4 hours, 12 minutes\r\n"
    ),
    "show ip interface brief": (
        "Interface                  IP-Address      OK? Method Status                Protocol\r\n"
        "GigabitEthernet0/0         10.0.12.1       YES manual administratively down down\r\n"
        "Loopback0                  1.1.1.1         YES manual up                    up\r\n"
    ),
    "show ip ospf neighbor": "Total number of neighbors: 0\r\n",
    "show cdp neighbors": (
        "Capability Codes: R - Router, S - Switch\r\n"
        "Device ID        Local Intrfce     Holdtme    Capability  Platform  Port ID\r\n"
        "Total cdp entries displayed: 0\r\n"
    ),
    "show logging": (
        "*MAR  3 09:14:02.113: %LINK-3-UPDOWN: Interface GigabitEthernet0/0, changed state to administratively down\r\n"
        "*MAR  3 09:14:03.119: %LINEPROTO-5-UPDOWN: Line protocol on Interface GigabitEthernet0/0, changed state to down\r\n"
        "*MAR  3 09:14:05.220: %OSPF-5-ADJCHG: Process 1, Nbr 2.2.2.2 on GigabitEthernet0/0 from FULL to DOWN, Neighbor Down: Interface down or detached\r\n"
    ),
    "show running-config": (
        "interface GigabitEthernet0/0\r\n"
        " ip address 10.0.12.1 255.255.255.0\r\n"
        " shutdown\r\n"
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
        "Total cdp entries displayed: 0\r\n"
    ),
    "show logging": (
        "*MAR  3 09:14:06.331: %OSPF-5-ADJCHG: Process 1, Nbr 1.1.1.1 on GigabitEthernet0/0 from FULL to DOWN, Neighbor Down: Dead timer expired\r\n"
    ),
    "show running-config": (
        "interface GigabitEthernet0/0\r\n"
        " ip address 10.0.12.2 255.255.255.0\r\n"
        "!\r\n"
        "router ospf 1\r\n"
        " router-id 2.2.2.2\r\n"
        " network 10.0.12.0 0.0.0.255 area 0\r\n"
        "!\r\n"
    ),
}
