import asyncio
import importlib
import os
import socket
import sys

import asyncssh

ROUTER_NAME = os.environ.get("ROUTER_NAME", "R1")
USERNAME = os.environ.get("ROUTER_USER", "netops")
PASSWORD = os.environ.get("ROUTER_PASS", "C1sco123!")
FAULT_SCENARIO = os.environ.get("FAULT_SCENARIO", "gi_shutdown")
ALERT_HOST = os.environ.get("ALERT_HOST", "host.docker.internal")
ALERT_PORT = int(os.environ.get("ALERT_PORT", "5514"))

scenario_module = importlib.import_module(f"scenarios.{FAULT_SCENARIO}")
COMMANDS = getattr(scenario_module, ROUTER_NAME)
PROMPT = f"{ROUTER_NAME}#"


class RouterSSHServer(asyncssh.SSHServer):
    def connection_made(self, conn):
        peer = conn.get_extra_info("peername")
        print(f"[{ROUTER_NAME}] connection from {peer}", file=sys.stderr)

    def begin_auth(self, username):
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        return username == USERNAME and password == PASSWORD


async def handle_client(process):
    process.stdout.write(f"\r\n{ROUTER_NAME} uptime is 3 days, 4 hours\r\n")
    process.stdout.write(f"{PROMPT} ")
    try:
        async for line in process.stdin:
            cmd = line.strip()
            if cmd in ("exit", "quit", "logout"):
                break
            if not cmd:
                process.stdout.write(f"{PROMPT} ")
                continue
            output = COMMANDS.get(cmd)
            if output is None:
                process.stdout.write(f"% Invalid input detected at '^' marker.\r\n")
            else:
                process.stdout.write(output)
            process.stdout.write(f"{PROMPT} ")
    except asyncssh.BreakReceived:
        pass
    process.exit(0)


def emit_startup_syslog():
    """Best-effort UDP syslog emission of this scenario's fault log lines,
    simulating a real device pushing an alert to a syslog collector. Never
    raises — a missing/unreachable collector must not affect the fake CLI.
    """
    log_text = COMMANDS.get("show logging", "")
    lines = [line for line in log_text.splitlines() if line.strip()]
    if not lines:
        return
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for line in lines:
                sock.sendto(f"{ROUTER_NAME}: {line}".encode(), (ALERT_HOST, ALERT_PORT))
        print(f"[{ROUTER_NAME}] emitted {len(lines)} startup syslog message(s) to {ALERT_HOST}:{ALERT_PORT}", file=sys.stderr)
    except OSError as exc:
        print(f"[{ROUTER_NAME}] syslog emission skipped ({exc})", file=sys.stderr)


async def start_server():
    host_key = asyncssh.generate_private_key("ssh-rsa")
    await asyncssh.create_server(
        RouterSSHServer,
        "",
        8022,
        server_host_keys=[host_key],
        process_factory=handle_client,
        encoding="utf-8",
    )
    print(f"[{ROUTER_NAME}] fake CLI listening on :8022 (scenario: {FAULT_SCENARIO})", file=sys.stderr)
    emit_startup_syslog()


async def main():
    await start_server()
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
