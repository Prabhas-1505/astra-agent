import os

from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoTimeoutException, ReadTimeout
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

# host/port default to the local lab containers — convenience, not a secret.
# username/password have no source-code default: a client silently falling
# back to a known credential is fine for a fake SSH server simulating a lab
# device, but is exactly the pattern that's dangerous if DEVICES is ever
# pointed at something real. Validated at call time (not here, at import
# time) so importing this module never requires ROUTER_USER/ROUTER_PASS to
# be set — only actually calling run_commands does.
DEVICES = {
    "R1": {
        "device_type": "cisco_ios",
        "host": os.environ.get("R1_HOST", "127.0.0.1"),
        "port": int(os.environ.get("R1_PORT", "2201")),
        "username": os.environ.get("ROUTER_USER"),
        "password": os.environ.get("ROUTER_PASS"),
    },
    "R2": {
        "device_type": "cisco_ios",
        "host": os.environ.get("R2_HOST", "127.0.0.1"),
        "port": int(os.environ.get("R2_PORT", "2202")),
        "username": os.environ.get("ROUTER_USER"),
        "password": os.environ.get("ROUTER_PASS"),
    },
}


@retry(
    retry=retry_if_exception_type((ReadTimeout, NetmikoTimeoutException)),
    stop=stop_after_attempt(3),
    wait=wait_fixed(2),
    reraise=True,
)
def run_commands(device_name: str, commands: list[str]) -> dict[str, str]:
    creds = DEVICES[device_name]
    if not creds["username"] or not creds["password"]:
        raise RuntimeError(
            "ROUTER_USER and ROUTER_PASS must be set in the environment — "
            "no default credential is shipped in source. For the local lab, "
            "set them to the fake routers' fixture credential (see .env.example)."
        )
    conn = ConnectHandler(**creds)
    try:
        return {cmd: conn.send_command(cmd) for cmd in commands}
    finally:
        conn.disconnect()
