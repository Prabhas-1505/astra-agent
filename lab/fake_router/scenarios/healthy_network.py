# No fault. Full OSPF adjacency on both sides, config matches golden exactly,
# logs are benign. This is the negative case: ASTRA should conclude there's
# nothing wrong rather than hallucinate a plausible-sounding root cause on
# healthy evidence — see README "Eval harness" for what actually happens when
# this is measured live rather than assumed.

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
    "show ip ospf neighbor": (
        "Neighbor ID     Pri   State           Dead Time   Address         Interface\r\n"
        "2.2.2.2           1   FULL/BDR        00:00:38    10.0.12.2       GigabitEthernet0/0\r\n"
    ),
    "show cdp neighbors": (
        "Capability Codes: R - Router, S - Switch\r\n"
        "Device ID        Local Intrfce     Holdtme    Capability  Platform  Port ID\r\n"
        "R2                Gig 0/0           157        R           IOSv      Gig 0/0\r\n"
        "Total cdp entries displayed: 1\r\n"
    ),
    "show logging": (
        "*MAR  3 09:05:00.001: %SYS-5-CONFIG_I: Configured from console by admin\r\n"
        "*MAR  3 09:05:04.228: %OSPF-5-ADJCHG: Process 1, Nbr 2.2.2.2 on "
        "GigabitEthernet0/0 from LOADING to FULL, Loading Done\r\n"
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
    "show ip ospf neighbor": (
        "Neighbor ID     Pri   State           Dead Time   Address         Interface\r\n"
        "1.1.1.1           1   FULL/DR         00:00:35    10.0.12.1       GigabitEthernet0/0\r\n"
    ),
    "show cdp neighbors": (
        "Capability Codes: R - Router, S - Switch\r\n"
        "Device ID        Local Intrfce     Holdtme    Capability  Platform  Port ID\r\n"
        "R1                Gig 0/0           160        R           IOSv      Gig 0/0\r\n"
        "Total cdp entries displayed: 1\r\n"
    ),
    "show logging": (
        "*MAR  3 09:05:00.004: %SYS-5-CONFIG_I: Configured from console by admin\r\n"
        "*MAR  3 09:05:04.331: %OSPF-5-ADJCHG: Process 1, Nbr 1.1.1.1 on "
        "GigabitEthernet0/0 from LOADING to FULL, Loading Done\r\n"
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
