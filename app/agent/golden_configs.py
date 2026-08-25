# Intended/expected configuration for each device, used by the Config Analyzer
# to detect drift against the live running-config. In a real deployment this
# would come from a config management system (e.g. a git repo of intended
# configs), not be hardcoded.

GOLDEN_CONFIGS = {
    "R1": (
        "interface GigabitEthernet0/0\r\n"
        " ip address 10.0.12.1 255.255.255.0\r\n"
        "!\r\n"
        "router ospf 1\r\n"
        " router-id 1.1.1.1\r\n"
        " network 10.0.12.0 0.0.0.255 area 0\r\n"
        "!\r\n"
    ),
    "R2": (
        "interface GigabitEthernet0/0\r\n"
        " ip address 10.0.12.2 255.255.255.0\r\n"
        "!\r\n"
        "router ospf 1\r\n"
        " router-id 2.2.2.2\r\n"
        " network 10.0.12.0 0.0.0.255 area 0\r\n"
        "!\r\n"
    ),
}
