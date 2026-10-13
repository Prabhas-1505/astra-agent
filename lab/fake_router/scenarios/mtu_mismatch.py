# Fault: R1's link interface MTU was lowered to 1400 (e.g. for an
# unrelated tunnel elsewhere on the box, copied into the wrong interface
# config) while R2 stays at the default 1500. The link and IP addressing are
# fine, but OSPF's DBD exchange detects the MTU mismatch and the neighbor
# gets stuck retransmitting instead of reaching FULL.

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
        "R2                Gig 0/0           148        R           IOSv      Gig 0/0\r\n"
        "Total cdp entries displayed: 1\r\n"
    ),
    "show logging": (
        "*MAR  3 09:18:02.511: %OSPF-5-ADJCHG: Process 1, Nbr 2.2.2.2 on "
        "GigabitEthernet0/0 from EXSTART to DOWN, Neighbor Down: Too many retransmissions\r\n"
        "*MAR  3 09:18:33.740: %OSPF-5-ADJCHG: Process 1, Nbr 2.2.2.2 on "
        "GigabitEthernet0/0 from EXSTART to DOWN, Neighbor Down: Too many retransmissions\r\n"
    ),
    "show running-config": (
        "interface GigabitEthernet0/0\r\n"
        " ip address 10.0.12.1 255.255.255.0\r\n"
        " mtu 1400\r\n"
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
        "R1                Gig 0/0           153        R           IOSv      Gig 0/0\r\n"
        "Total cdp entries displayed: 1\r\n"
    ),
    "show logging": (
        "*MAR  3 09:18:02.998: %OSPF-5-ADJCHG: Process 1, Nbr 1.1.1.1 on "
        "GigabitEthernet0/0 from EXSTART to DOWN, Neighbor Down: Too many retransmissions\r\n"
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
