# ASTRA — Autonomous Network Diagnostics Agent

A LangGraph agent that diagnoses network faults the way an engineer would:
gather evidence from multiple angles, form ranked hypotheses, verify them
against the live device before trusting them, and hand a human a root cause
with a confidence score and a specific fix — never an auto-applied change.

## Architecture

```
                     ┌── Topology Analyzer ──┐
        START ───────┼── Config Analyzer ────┼──▶ Context Aggregator
                     └── Logs Analyzer ──────┘              │
                                                              ▼
                                                    Hypothesis Engine ◀── KB precedent
                                                              │            (past cases)
                                                              ▼
                                                    Diagnostic Tool (Netmiko)
                                                              │
                                                              ▼
                              ┌── refuted (retry ≤3) ── Validation Loop
                              │                              │ confirmed
                              │                              ▼
                              │                    Root Cause Synthesis
                              │                              │
                              ▼                              ▼
                    Escalate (inconclusive)          Human Approval Gate
                              │                              │
                              └──────────┬───────────────────┘
                                         ▼
                              KB writeback (outcome + root cause)
```

Full design rationale and the v1→v2 revision (closing the feedback loop,
bounding the retry, consolidating tooling) are written up separately; this
README covers what's actually built and how to run it.

## What's implemented

- **Parallel analyzers** — Topology (`show ip interface brief`, `show cdp
  neighbors`), Config (diffs live running-config against a golden baseline),
  Logs (`show logging`) — fan out from `START`, fan into a Context Aggregator.
- **KB with a real feedback loop** — `app/kb/store.py` embeds each incident's
  context (OpenAI embeddings) and stores it with its outcome. The Hypothesis
  Engine retrieves the most similar past case before reasoning. Every
  approval, rejection, and inconclusive escalation writes back.
- **Bounded validation loop** — refuted hypotheses retry against the next
  candidate, capped at 3 attempts, then escalate to a human as inconclusive
  instead of looping forever.
- **Netmiko-backed diagnostic tool** — hypotheses aren't trusted until
  verified against the actual device output, with retry-on-timeout for
  transient SSH hiccups.
- **Human approval gate** — nothing gets recommended as a fix without
  explicit sign-off; ASTRA never auto-executes.

## What's implemented (continued)

- **Runbook-backed KB** — alongside the learned past-cases store, a small set
  of static runbooks (`app/kb/runbooks.py`) is embedded once and retrieved by
  similarity at hypothesis time, matched against precedent from real past
  incidents.
- **Automated alert trigger** — `app/triggers/syslog_listener.py` is a UDP
  syslog listener; the lab's fake routers emit a real syslog packet on
  startup (see `emit_startup_syslog` in `lab/fake_router/server.py`), and a
  matching Cisco-style severity pattern (`%FACILITY-SEVERITY-MNEMONIC`)
  auto-launches the full ASTRA graph — no manual `python -m app.main`
  needed. Verified end-to-end: router restart → real UDP packet → listener
  match → ASTRA launch → correct diagnosis → KB writeback, unattended.

## What's not implemented

- **pyATS as the primary diagnostic tool** — attempted, not shipped. pyATS
  needs `unicon`, whose connection state machine is built around real IOS
  banner/prompt negotiation. Against the lab's fake CLI, `dev.connect()`
  hung indefinitely with zero output even with `log_stdout=True` and a 20s
  per-operation timeout — it never got past the initial handshake. Making it
  work would mean writing a custom Unicon OS plugin to match the fake CLI's
  simplified prompt behavior, which is disproportionate effort for a lab
  double of the real diagnostic value pyATS would add. Netmiko remains the
  only diagnostic tool; revisit pyATS only against a real or more
  IOS-faithful lab (e.g. real IOSv images in GNS3/EVE-NG).

## The lab

Since this doesn't run against real Cisco gear, `lab/` spins up two
containers, each a small `asyncssh`-based server that speaks a Cisco-IOS-like
CLI (`show ip interface brief`, `show running-config`, etc.) over real SSH —
realistic enough that Netmiko's connection handling, prompt detection, and
command parsing are genuinely exercised, not mocked out.

Three fault scenarios are built in, switchable via `FAULT_SCENARIO`:

| Scenario | Fault |
|---|---|
| `gi_shutdown` (default) | R1's link interface is administratively shut down |
| `ospf_network_missing` | R1 never enabled OSPF on the link (missing `network` statement) |
| `area_mismatch` | R2's OSPF area ID doesn't match R1's |

```bash
docker compose -f lab/docker-compose.yml up -d --build          # default scenario
FAULT_SCENARIO=area_mismatch docker compose -f lab/docker-compose.yml up -d --force-recreate
```

## Running ASTRA

```bash
uv venv --python 3.11 .venv && source .venv/bin/activate
uv pip install -r requirements.txt
cp .env.example .env   # fill in OPENAI_API_KEY
docker compose -f lab/docker-compose.yml up -d --build

python -m app.main
```

It'll pull logs/topology/config from both routers, propose hypotheses, verify
them, and print a root cause + confidence + recommended fix, then ask for
approval before writing the outcome back to the KB.

## Eval harness

`eval/run_eval.py` cycles ASTRA through all three fault scenarios
automatically (restarting the lab and clearing a scratch KB between each),
grades the diagnosis against the actual ground truth, and reports accuracy —
this is what backs the confidence numbers instead of just asserting them.

```bash
python -m eval.run_eval
```

Results vary run to run — this is real LLM non-determinism, not flakiness in
the harness, and worth reporting honestly rather than cherry-picking the best
run. One run scored 3/3 (100%), including a genuine self-correction on
`ospf_network_missing` (refuted once, retried, landed on the right cause). A
later run scored 2/3 (67%) on the same scenario, and inspecting it surfaced a
real architectural limitation rather than noise: the model gathered the
*right* evidence on its first hypothesis (R1's running-config, correctly
showing no `network` statement at all) but phrased the hypothesis narrowly as
"area ID mismatch" — evidence-that-nothing-exists doesn't confirm
evidence-of-a-specific-wrong-value, so Validation correctly refuted it, and
because hypotheses are a fixed list of 3 generated upfront rather than
regenerated from evidence gathered so far, it never got a chance to re-frame
and exhausted into "inconclusive." A worthwhile next step, if this goes
further: feed evidence back into the Hypothesis Engine on refutation instead
of just advancing a static list.

## Tests

```bash
python -m pytest tests/
```

Unit tests cover the validation loop's branching logic, config drift
detection, and KB retrieval/writeback — all mocked (no live API/Docker
dependency, run in ~2s). Live accuracy is measured separately by the eval
harness above, which is the more meaningful signal for this kind of agent.
