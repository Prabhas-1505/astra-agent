"""Automated alert trigger — the 'Automated Alert' entry point from the
architecture, as opposed to the manual `python -m app.main` invocation.

Listens for UDP syslog messages (the lab's fake routers emit real ones on
startup — see lab/fake_router/server.py) and launches the full ASTRA graph
the moment a message matches a standard Cisco severity pattern, instead of
requiring someone to notice the incident and run ASTRA by hand.
"""

import os
import re
import socket
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from app.agent.graph import build_graph  # noqa: E402

HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
PORT = int(os.environ.get("LISTEN_PORT", "5514"))
COOLDOWN_SECONDS = int(os.environ.get("ALERT_COOLDOWN_SECONDS", "15"))

# Matches standard Cisco IOS syslog format: %FACILITY-SEVERITY-MNEMONIC,
# e.g. %LINK-3-UPDOWN, %OSPF-5-ADJCHG, %OSPF-4-BADAREAID.
ALERT_PATTERN = re.compile(r"%[A-Z0-9_]+-\d-[A-Z0-9_]+")


def run_astra():
    print("\n[ALERT] launching ASTRA...\n")
    graph = build_graph()
    graph.invoke({"max_retries": 3})


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOST, PORT))
    print(f"[listener] watching for syslog alerts on udp:{HOST}:{PORT}", file=sys.stderr)

    last_trigger = 0.0
    while True:
        data, addr = sock.recvfrom(4096)
        message = data.decode(errors="replace").strip()
        print(f"[listener] {addr[0]}: {message}", file=sys.stderr)

        if not ALERT_PATTERN.search(message):
            continue

        now = time.time()
        if now - last_trigger < COOLDOWN_SECONDS:
            continue  # same incident likely still emitting more log lines
        last_trigger = now
        run_astra()


if __name__ == "__main__":
    main()
