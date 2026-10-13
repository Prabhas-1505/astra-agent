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
  *symptom fingerprint* (see below, not the raw context) and stores it with
  its outcome. The Hypothesis Engine retrieves the most similar past case
  before reasoning. Every approval, rejection, and inconclusive escalation
  writes back.
- **Structured symptom fingerprint for retrieval** — `build_symptom_fingerprint`
  in `app/agent/nodes.py` parses interface status, CDP neighbor counts,
  config-drift lines, and syslog mnemonics into a compact structured string,
  used as the retrieval query instead of the full concatenated context. This
  replaces an earlier mitigation (see "Eval harness" below for the bug it
  actually fixes).
- **Ranked first-guess hypotheses** — the Hypothesis Engine proposes a
  primary *and* secondary candidate on the first pass instead of committing
  to one guess; a refuted primary falls through to the already-ranked
  secondary (no extra LLM call) before a fully informed regeneration.
- **Cross-device hypothesis verification** — a hypothesis can name multiple
  `target_devices`, and `diagnostic_tool` gathers evidence from all of them.
  Needed for faults that are inherently a *comparison* (MTU/area mismatch)
  rather than a single-device symptom — see "Eval harness" for the live run
  that caught this as a real 0/3 gap before the fix, and 100% after.
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
  `record_inconclusive` KB-writeback helpers the CLI uses. Includes a small
  live SVG of the lab topology so the active fault is visible at a glance.
- **Observability** — structured `logging` (not `print`) for everything
  except the CLI's own human-facing prompts, level-configurable via
  `ASTRA_LOG_LEVEL`. Three env vars (`LANGCHAIN_TRACING_V2`,
  `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT`) turn on full LangSmith tracing of
  every graph run with zero code changes — langchain-core reads them
  natively.
- **Retry/backoff on LLM calls** — the same `tenacity` pattern already used
  for Netmiko (`app/tools/netmiko_tool.py`) now also wraps the structured-
  output LLM calls in `app/agent/nodes.py`, for transient network/timeout
  errors.
- **Containerized end-to-end** — a top-level `Dockerfile` + `docker-compose.yml`
  run the fake routers, the Gradio UI, and the syslog listener all in
  containers, so `docker compose up` is enough with nothing needed on the
  host but Docker itself and a filled-in `.env`.

## Security

Boundaries that are actually enforced in code, not just asked for in a prompt:

- **Command allowlisting.** `Hypothesis.verify_commands` is free-text LLM
  output. Before this pass, the only thing keeping it to `ALLOWED_COMMANDS`
  was a prompt instruction — a hallucinating or adversarially-steered model
  could have produced any string, and `diagnostic_tool` sent it straight to
  the device. `diagnostic_tool` (`app/agent/nodes.py`) now filters
  `verify_commands` against `ALLOWED_COMMANDS` and `target_devices` against
  the known `DEVICES` in code; anything off-list is dropped and logged at
  `WARNING`, never reaches `run_commands`. See `tests/test_security_boundaries.py`.
- **No crash on a hallucinated device.** Previously a device name not in
  `DEVICES` reached `netmiko_tool.py`'s `DEVICES[device_name]` lookup directly
  and raised an unhandled `KeyError`, crashing the whole run. Now it's filtered
  out same as an unknown command; if nothing valid survives, evidence is empty
  and Validation correctly refuses to confirm — same `INCONCLUSIVE` path as
  any other insufficient-evidence case, not a crash.
- **No credential fallback in source.** `netmiko_tool.py` no longer ships a
  default username/password — `ROUTER_USER`/`ROUTER_PASS` must be set via
  the environment (`.env.example`/compose files), and `run_commands` raises
  clearly if they're not, rather than silently connecting with a known
  default. The lab's fixture credential (`netops`/`C1sco123!`) now lives in
  `docker-compose.yml`/`lab/docker-compose.yml` as an explicit, overridable
  default — not buried in the Python client that would run against a real
  device too.
- **Dependency scanning** — `pip-audit -r requirements.txt` runs in CI
  alongside `ruff`; currently clean.

**Not addressed, and worth naming rather than ignoring:** there's no explicit
SSH host-key verification policy (relies on Netmiko/Paramiko defaults —
irrelevant against `127.0.0.1`, real for any future real-device deployment),
and no sanitization boundary between untrusted evidence (device logs/config)
and the LLM prompt — log/config text is concatenated into prompts as-is. The
command/device allowlisting above closes the one boundary that's actually
enforceable in code (what ASTRA is *permitted to execute*); no code-level
technique reliably stops a sufficiently adversarial log line from influencing
an LLM's own reasoning — that's a general, open problem for LLM-based systems,
not something this project claims to have solved.

## Known limitations

Collected in one place rather than scattered through the README — what's
genuinely missing or constrained, and why it wasn't worth fixing here.

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
- **Single-vendor scope** — the fake CLI, golden configs, and runbooks are
  all Cisco-IOS-shaped. Supporting a second vendor would mean a second
  fake-CLI command set and vendor-aware runbook matching, not just a config
  change; not attempted here.
- **Learned-case KB embeddings predate the symptom-fingerprint fix** —
  `data/past_cases.json` entries written before this fix were embedded from
  the full raw context, not the structured fingerprint. New cases are
  fingerprint-embedded going forward; old entries aren't migrated (there
  were only 2, both low-value duplicates of the same scenario) rather than
  risk a fragile reconstruction from truncated, already-lossy stored text.
- **Eval harness grading is a device + keyword heuristic**, not an LLM
  judge — appropriate for fault-injection against known ground truth, but it
  can't score *fix quality* beyond that; a wrong-but-plausible-sounding fix
  for the right root cause would still grade "correct."
- **Validation can confirm a wrong hypothesis with high confidence**, not just
  fail to confirm a right one. Seen live on `mtu_mismatch` (see "Eval
  harness"): once multi-device evidence gathering was fixed, one run out of
  three had Validation confirm an OSPF-network-statement-mismatch hypothesis
  at 0.90 confidence when the actual fault was the MTU mismatch — a false
  positive, not the "exhausts into inconclusive" failure mode this project's
  bounded-retry design was built to catch. Not fixed; would need either a
  second independent verification pass or a stricter validation prompt.
- **No hosted demo or recorded walkthrough** — both would need screen/account
  access this environment doesn't have; the "Running ASTRA" section below is
  kept accurate enough to self-serve either whenever needed.

## The lab

Since this doesn't run against real Cisco gear, `lab/` spins up two
containers, each a small `asyncssh`-based server that speaks a Cisco-IOS-like
CLI (`show ip interface brief`, `show running-config`, etc.) over real SSH —
realistic enough that Netmiko's connection handling, prompt detection, and
command parsing are genuinely exercised, not mocked out.

Five fault scenarios are built in, switchable via `FAULT_SCENARIO`:

| Scenario | Fault |
|---|---|
| `gi_shutdown` (default) | R1's link interface is administratively shut down |
| `ospf_network_missing` | R1 never enabled OSPF on the link (missing `network` statement) |
| `area_mismatch` | R2's OSPF area ID doesn't match R1's |
| `duplicate_ip` | R2's interface was misconfigured with R1's IP address (conflict) |
| `mtu_mismatch` | R1's interface MTU doesn't match R2's, so OSPF's DBD exchange never completes |

```bash
docker compose -f lab/docker-compose.yml up -d --build          # default scenario
FAULT_SCENARIO=area_mismatch docker compose -f lab/docker-compose.yml up -d --force-recreate
```

## Running ASTRA

**Option A — host venv, routers in Docker** (original workflow, best for development):

```bash
uv venv --python 3.11 .venv && source .venv/bin/activate
uv pip install -r requirements.txt
cp .env.example .env   # fill in OPENAI_API_KEY (ROUTER_USER/ROUTER_PASS already default to the lab fixture)
docker compose -f lab/docker-compose.yml up -d --build

python -m app.main
```

It'll pull logs/topology/config from both routers, propose hypotheses, verify
them, and print a root cause + confidence + recommended fix, then ask for
approval before writing the outcome back to the KB.

**Option B — everything in containers:**

```bash
cp .env.example .env   # fill in OPENAI_API_KEY (ROUTER_USER/ROUTER_PASS already default to the lab fixture)
docker compose up -d --build        # routers + Gradio UI (localhost:7900) + syslog listener
docker compose run --rm cli         # interactive CLI, on demand
```

## UI

```bash
python -m app.ui.gradio_app   # defaults to http://127.0.0.1:7900, override with PORT=
```

(7900 instead of Gradio's usual 7860 default — deliberately, to avoid colliding
with other Gradio apps you might already have running, e.g. from Jupyter
notebooks using the same default port.)

Click "Run Diagnosis" to trigger the pipeline against whatever fault is
currently in the lab, then Approve or Reject the recommended fix. The KB
panel is also what first surfaced a real retrieval precision bug, caught
live rather than in a test — see "Eval harness" below for the full story and
the fix.

## Eval harness

`eval/run_eval.py` cycles ASTRA through all five fault scenarios
automatically (restarting the lab and clearing a scratch KB between each),
grades the diagnosis against the actual ground truth, and reports accuracy —
this is what backs the confidence numbers instead of just asserting them.
Each scenario now runs multiple times (`--runs`, default 5) and the report
shows mean ± stddev per scenario — accuracy, confidence, retries, latency,
and an estimated gpt-4o-mini token cost — instead of a single anecdotal pass.
`--no-kb` disables runbook + learned-case retrieval for the run, so the KB's
actual contribution can be measured by diffing two reports instead of
assumed.

```bash
python -m eval.run_eval                       # all 5 scenarios, 5 runs each
python -m eval.run_eval --runs 3 --scenario mtu_mismatch --scenario duplicate_ip
python -m eval.run_eval --no-kb               # ablation: KB disabled
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
static list decided before any evidence came in.

A second, separate precision bug showed up later, in KB *retrieval* rather
than hypothesis generation: `retrieve_runbooks` matched the wrong runbook —
"OSPF area ID mismatch" surfaced instead of "Interface administratively
down" — despite the interface fault being the actual (correctly diagnosed)
root cause. The raw cosine similarities confirmed this wasn't a bug in the
ranking code: embedding the *entire* concatenated context (logs + topology +
config diffs, mostly unchanging boilerplate) let the area-mismatch runbook's
phrasing ("config-drift", "diff running-config against golden baseline")
outrank the true semantic match on incidental vocabulary overlap. First
mitigated cheaply (return top-2 runbooks instead of top-1), then fixed at the
actual cause: `build_symptom_fingerprint` (`app/agent/nodes.py`) now parses a
compact, structured summary — interface status, CDP neighbor counts, changed
config-drift lines, syslog mnemonics — and that fingerprint, not the full
context, is what gets embedded for both runbook and learned-case retrieval.

Real measured report (`python -m eval.run_eval --runs 3`, gpt-4o-mini, KB on):

```
=== EVAL REPORT ===
Runs per scenario: 3  |  KB: on
Overall accuracy: 73%

scenario                accuracy    confidence      retries       latency (s)     cost ($)
gi_shutdown             100%        0.95±0.00      0.0±0.0         9.1±1.7         0.0004±0.0000
ospf_network_missing    100%        0.93±0.03      2.0±0.0        16.5±0.7         0.0007±0.0000
area_mismatch            67%        0.95±0.00      2.0±0.0        18.1±3.0         0.0008±0.0000
duplicate_ip            100%        0.95±0.00      2.0±0.0        20.3±2.3         0.0008±0.0000
mtu_mismatch               0%        n/a            3.0±0.0        16.6±2.1         0.0007±0.0000

Total estimated cost for this eval run: $0.0100
```

This was a genuinely useful result, not just a clean number to report: it
surfaced a real architectural gap. `mtu_mismatch` went 0/3, and `area_mismatch`
dropped to 2/3 — both worse than the three single-device faults. The reason is
in the ruled-out reasoning the harness captured: `diagnostic_tool` only ever
verified evidence from the hypothesis's *one* `target_device`, so a hypothesis
that's inherently a *cross-device comparison* — "R1's MTU doesn't match R2's" —
could never be confirmed from one side's evidence alone, no matter how many
retries. Validation correctly refused to confirm on insufficient evidence every
time, so the loop exhausted. `duplicate_ip` still hit 100% because its fault
leaves an unambiguous single-device signature (`%IP-4-DUPADDR` in `show
logging` is sufficient on its own); `mtu_mismatch`'s evidence (one side's `mtu
1400` line) isn't self-evidently wrong without the other side's value to
compare against, and `area_mismatch` was a coin flip depending on whether the
model happened to pick the side whose `area` value visibly diverges from the
implied default.

**Fixed the same session, once confirmed by the harness rather than guessed
at:** `Hypothesis.target_device: str` became `target_devices: list[str]`
(`Field(min_length=1)`), `diagnostic_tool` now gathers evidence from every
device a hypothesis names and merges it into `{device: {command: output}}`,
and the hypothesis-generation prompts explicitly tell the model to name a
second device only when the fault is inherently comparative. Re-running the
same 3-run eval after the fix:

```
=== EVAL REPORT ===
Runs per scenario: 3  |  KB: on
Overall accuracy: 100%

scenario                accuracy    confidence      retries       latency (s)     cost ($)
gi_shutdown             100%        0.95±0.00      0.0±0.0      9.4±2.2         0.0004±0.0000
ospf_network_missing    100%        0.90±0.00      0.0±0.0      9.2±0.5         0.0004±0.0000
area_mismatch           100%        0.95±0.00      0.0±0.0      8.9±0.2         0.0004±0.0000
duplicate_ip            100%        0.95±0.00      0.0±0.0      9.9±0.3         0.0004±0.0000
mtu_mismatch            100%        0.93±0.03      0.0±0.0      9.4±0.4         0.0005±0.0000

Total estimated cost for this eval run: $0.0063
```

73% → 100%, retries dropped to 0 across the board (the model now gets both
sides' evidence in a single pass instead of exhausting retries first), and
latency dropped too (~9s avg vs. 15–20s for the scenarios that used to retry).
Worth reporting honestly rather than stopping at the clean number above: a
second 3-run pass on just `mtu_mismatch`/`area_mismatch` (done to confirm the
fix before the full suite) hit `mtu_mismatch` 2/3 — one run had Validation
*confirm* a plausible-sounding but wrong hypothesis (an OSPF network-statement
mismatch, not the actual MTU fault) with 0.90 confidence. That's a different
failure mode than before (false positive vs. correctly-refused-to-confirm),
and arguably a better one — the agent now reaches *a* conclusion instead of
stalling out — but it means Validation's strictness isn't perfect either, and
multi-run variance on this scenario specifically is worth watching rather than
assuming the 100% above is guaranteed every time.

## Containerization

A top-level `Dockerfile` + `docker-compose.yml` run the fake routers, the
Gradio UI, and the syslog listener as containers on one `astranet` bridge
network — `docker compose up` is enough, with the KB persisted to the host
via a `./data` volume mount so it survives container restarts. The
interactive CLI is a `cli` profile service (`docker compose run --rm cli`)
rather than part of the default `up`, since it needs a TTY for the approval
prompt and would otherwise hang a non-interactive `up`.
`lab/docker-compose.yml` still exists separately for the original
host-venv-plus-dockerized-routers workflow above.

## Tests

```bash
python -m pytest tests/
```

Unit tests cover the validation loop's branching logic, the ranked-hypothesis
fast path, multi-device evidence gathering, the symptom-fingerprint parser,
config drift detection, and KB retrieval/writeback — all mocked (no live
API/Docker dependency, run in ~1.5s). `ruff check .` runs alongside pytest in
CI. Live accuracy is measured separately by the eval harness above, which is
the more meaningful signal for this kind of agent.
