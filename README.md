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
- **Gradio dashboard** — `app/ui/gradio_app.py` shows the reasoning trail
  (KB precedent + runbook matches, ruled-out hypotheses, final root cause)
  and replaces the CLI's blocking `input()` prompt with Approve/Reject
  buttons. Backed by `build_diagnosis_graph()`, a graph variant that stops at
  root cause instead of calling `human_approval` directly, so the UI can
  collect the decision itself and call the same `record_decision`/
  `record_inconclusive` KB-writeback helpers the CLI uses.

## What's not implemented

- **pyATS as the primary diagnostic tool** — attempted twice, not shipped.
  pyATS needs `unicon`, whose connection layer hung indefinitely (zero
  output, well past its own timeout) against the lab's fake CLI both with
  `os: ios` (assumes real IOS banner/prompt negotiation) and `os: generic`
  (Unicon's looser, vendor-agnostic prompt matching) — ruling out "wrong OS
  profile" as the cause. The mismatch is more fundamental: our fake CLI is a
  line-based `asyncssh` process handler, not a full pty-driven shell, and
  Unicon's connection handshake apparently expects the latter. Making pyATS
  work would mean either building a proper pty-backed fake CLI or writing a
  custom Unicon connection plugin — real effort disproportionate to what it
  adds over Netmiko here. Netmiko remains the only diagnostic tool; revisit
  pyATS only against a real or more IOS-faithful lab (e.g. real IOSv images
  in GNS3/EVE-NG).

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

## UI

```bash
python -m app.ui.gradio_app   # defaults to http://127.0.0.1:7900, override with PORT=
```

(7900 instead of Gradio's usual 7860 default — deliberately, to avoid colliding
with other Gradio apps you might already have running, e.g. from Jupyter
notebooks using the same default port.)

Click "Run Diagnosis" to trigger the pipeline against whatever fault is
currently in the lab, then Approve or Reject the recommended fix.

Worth documenting since it was caught live rather than in a test: the first
run showed the KB matching the *wrong* runbook — "OSPF area ID mismatch"
surfaced instead of "Interface administratively down," despite the interface
fault being the actual (correctly diagnosed) root cause. Checking the raw
cosine similarities confirmed this wasn't a bug: the area-mismatch runbook's
phrasing ("config-drift", "diff running-config against golden baseline")
happens to overlap more with the aggregated context's *structure* than the
correct runbook does, even though it's the wrong match for this incident. A
known failure mode of single-vector retrieval over long multi-section text —
incidental vocabulary overlap can outrank the true semantic match. The final
diagnosis was unaffected (the LLM reasoned past the misleading hint using
actual evidence), but the KB panel showing one confidently-wrong pick was
misleading on its own, so `retrieve_runbooks` now returns the top 2 instead
of top 1 — cheap, and it surfaces the correct runbook alongside the
incidental match instead of hiding it.

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
run. Early on, a run scored 2/3 (67%): the model gathered the *right*
evidence on its first hypothesis for `ospf_network_missing` (R1's
running-config, correctly showing no `network` statement at all) but phrased
the hypothesis narrowly as "area ID mismatch" — evidence-that-nothing-exists
doesn't confirm evidence-of-a-specific-wrong-value, so Validation correctly
refuted it. At the time, hypotheses were a fixed list of 3 generated upfront,
so once refuted it had no way to re-frame and exhausted into "inconclusive."

That was a real architectural gap, not noise, so it's fixed: the Hypothesis
Engine now regenerates a single new hypothesis on every retry, informed by
every prior attempt's evidence and *why* it was refuted (see
`_format_ruled_out` in `app/agent/nodes.py`), instead of cycling through a
static list decided before any evidence came in. Two eval runs since the fix:
**3/3 (100%) both times**, with `ospf_network_missing` needing exactly one
informed retry each time instead of exhausting.

## Tests

```bash
python -m pytest tests/
```

Unit tests cover the validation loop's branching logic, config drift
detection, and KB retrieval/writeback — all mocked (no live API/Docker
dependency, run in ~2s). Live accuracy is measured separately by the eval
harness above, which is the more meaningful signal for this kind of agent.
