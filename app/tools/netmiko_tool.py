import os

from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoTimeoutException, ReadTimeout
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

DEVICES = {
    "R1": {
        "device_type": "cisco_ios",
        "host": os.environ.get("R1_HOST", "127.0.0.1"),
        "port": int(os.environ.get("R1_PORT", "2201")),
        "username": os.environ.get("ROUTER_USER", "netops"),
        "password": os.environ.get("ROUTER_PASS", "C1sco123!"),
    },
    "R2": {
        "device_type": "cisco_ios",
        "host": os.environ.get("R2_HOST", "127.0.0.1"),
        "port": int(os.environ.get("R2_PORT", "2202")),
        "username": os.environ.get("ROUTER_USER", "netops"),
        "password": os.environ.get("ROUTER_PASS", "C1sco123!"),
    },
}


@retry(
    retry=retry_if_exception_type((ReadTimeout, NetmikoTimeoutException)),
    stop=stop_after_attempt(3),
    wait=wait_fixed(2),
    reraise=True,
)
def run_commands(device_name: str, commands: list[str]) -> dict[str, str]:
    conn = ConnectHandler(**DEVICES[device_name])
    try:
        return {cmd: conn.send_command(cmd) for cmd in commands}
    finally:
        conn.disconnect()
