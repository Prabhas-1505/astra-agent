# Engineering decisions

Grounded in what's actually implemented in this repo, not an aspirational
design. File/function references point at the real code; if they drift,
trust the code.

## Why a multi-node graph instead of one LLM call

A single prompt that's handed logs/config/topology and asked for "the root
cause" has no way to *verify* its own answer — it can only sound confident.
The graph structure (`app/agent/graph.py`) exists to separate three things
that a single call conflates: **gathering evidence** (the three analyzers),
**forming a hypothesis** (Hypothesis Engine), and **checking the hypothesis
against fresh, targeted evidence** (diagnostic_tool → Validation). Validation
only sees the hypothesis and the evidence gathered *specifically to test it*
— not the original context, not the KB precedent, not the runbook hints that
influenced the guess. That separation is what makes "Validation correctly
refused to confirm" a meaningful sentence instead of the same model agreeing
with itself. The `healthy_network` eval scenario (README, "Eval harness")
exists specifically to prove this isn't theater: on genuinely healthy
evidence, three plausible-sounding hypotheses were proposed and all three
were correctly refuted, live, not asserted.

## Why LangGraph specifically

Three properties mattered more than the framework name:

1. **Fan-out/fan-in for free.** `START` has three edges (logs/topology/config
   analyzers) that merge into `context_aggregator`. LangGraph's sync executor
   runs independent nodes in the same superstep concurrently; writing that
   orchestration by hand (thread pool + join + merge) for 3 nodes isn't hard,
   but it isn't free either, and it's exactly the kind of infrastructure code
   that's easy to get subtly wrong (partial failure handling, in particular
   — see "How SSH failures are handled" below).
2. **Conditional edges map directly onto the retry loop's actual states.**
   `route_after_validation` returns `confirmed`/`retry`/`exhausted`, and the
   graph's `add_conditional_edges` call *is* the state machine — there's no
   separate hand-rolled dispatch table that could drift out of sync with the
   graph diagram.
3. **State is a typed, mergeable dict**, not a free-form object either side
   has to remember the shape of. `AstraState` (TypedDict) plus a custom
   reducer for `token_usage` (`_merge_token_usage` in `graph.py`) is what
   lets nodes report partial updates (just their own token usage) and have
   the framework fold them into a running total, instead of every node
   needing to read-and-re-add the whole state's prior value.

The honest alternative considered: a plain Python state machine (a `while`
loop with an enum). That would have worked and would have been *simpler* to
read for someone who's never seen LangGraph. The graph structure earns its
keep specifically at the fan-out step and at the `pending_hypotheses`
fast-path (advance to the ranked secondary without a new LLM call, but still
go through the same `validation` node) — both are naturally expressed as
graph edges and would be more state-tracking code as a bare loop.

## How contradictory and insufficient evidence is handled

Two different mechanisms, deliberately not merged into one:

- **Insufficient evidence** (nothing supports the hypothesis either way) —
  `validation`'s prompt is explicit: "Be strict — only confirm if the
  evidence directly supports it." An LLM that's merely *not told* to be
  strict will tend to find a weak excuse to confirm; measured proof this
  mattered is in the README's "Eval harness" section (the original
  `ospf_network_missing` 2/3 bug was exactly a case of evidence-that-nothing-
  exists not being treated as proof of a specific wrong value).
- **Actively contradictory evidence** (the diagnostic step shows the opposite
  of what's expected) — same mechanism, `confirmed=False` with a reasoning
  string, which becomes part of `_format_ruled_out`'s output on the next
  attempt. The system doesn't have a separate "contradiction detector"; a
  contradiction *is* a refutation, and refutations already drive the retry
  loop. Keeping these as one mechanism rather than two is a deliberate
  simplification — a second, parallel "is this evidence contradictory"
  classifier would add a failure mode (what if the two disagree?) without a
  concrete case in this codebase where the distinction changes behavior.

What's **not** implemented: a second, independent LLM call that re-checks
Validation's own verdict. That would catch Validation confirming a wrong
hypothesis (the real failure mode found live on `mtu_mismatch` — see
README) at roughly double the LLM cost per diagnosis. Not done here because
the failure rate observed (1 run in 6 across two re-checks) doesn't yet
justify it, but it's the concrete next step if that rate turns out to be
higher at larger scale — see `docs/INTERVIEW_GUIDE.md` Q&A for the deeper
discussion.

## Why deterministic, code-level verification (not just a prompt)

`ALLOWED_COMMANDS` existed before this pass, but only as text inside the
Hypothesis Engine's prompt ("use exactly these strings"). That's an
*instruction*, not a *constraint* — nothing stopped `Hypothesis.verify_commands`
(free-text LLM output validated only by Pydantic's `list[str]`, which accepts
any strings) from containing something off-list, and `diagnostic_tool` sent
whatever it received straight to the device. Prompts are not a security
boundary for exactly the reason this pattern is called "prompt injection" —
there's no code-level guarantee an LLM follows an instruction, only a
statistical tendency to. `diagnostic_tool` now filters both `verify_commands`
against `ALLOWED_COMMANDS` and `target_devices` against known `DEVICES`
*before* anything reaches `run_commands` (see `app/agent/nodes.py` and
`tests/test_security_boundaries.py`). This is the one boundary in this
system that's actually enforceable in code — what ASTRA is *permitted to
execute*. What an LLM is *persuaded to believe* from untrusted evidence text
is a different, open problem (see "Security boundaries" below).

## How SSH/device failures are handled

Before this pass, a connection failure to either router — timeout,
authentication failure, connection refused — propagated as an unhandled
exception straight out of whichever analyzer's dict/list comprehension was
calling `run_commands`, crashing the entire graph run before hypothesis
generation ever started. `_safe_run_commands` (`app/agent/nodes.py`) is the
isolation boundary: it catches the failure, logs it, and returns it *as
evidence* (`DEVICE_UNREACHABLE: <reason>` for every requested command on that
device) rather than letting it propagate. This was live-verified, not just
unit-tested: pointing `R2_PORT` at a closed port, ASTRA correctly logged
R2 as unreachable and *still reached the correct diagnosis* using R1's
evidence (see README "Reliability").

One subtlety worth naming: discovering the right exception types to catch
required checking `netmiko.exceptions`' actual class hierarchy rather than
assuming — `NetmikoTimeoutException` and `NetmikoAuthenticationException`
inherit from `paramiko.ssh_exception.SSHException`, *not* from Netmiko's own
`NetmikoBaseException`. A test written against the wrong base class would
have passed against a mock that raised the "wrong" exception type and
missed the most common real failure entirely. The fix catches
`(NetmikoBaseException, SSHException, OSError)` — verified via `__mro__`
inspection, not guessed.

## Scalability tradeoffs (stated honestly)

This system talks to exactly 2 devices, in a loop, inside each analyzer.
There is no connection pool, no worker-count limit, no bounded queue —
because there's nothing to bound yet. Claiming a specific throughput number
for "N simulated devices" would require building a device-pool simulator
that doesn't exist and reporting numbers about *that*, not about ASTRA. The
honest version of this tradeoff: LangGraph's sync executor already runs the
three analyzers concurrently (a thread per node within a superstep) for N=3
nodes querying 2 devices each — that's the free concurrency this
architecture gets today. Scaling past a handful of devices would need:
(1) a connection pool instead of a fresh `ConnectHandler` per call,
(2) an explicit worker-count limit (`asyncio.Semaphore` or a bounded
`ThreadPoolExecutor`) so N devices don't mean N simultaneous SSH sessions,
and (3) per-device circuit breaking so one slow/flapping device doesn't
starve the pool — the `_safe_run_commands` isolation added in this pass is
the prerequisite for that, not the whole of it.

## Security boundaries — what's enforced vs. what's acknowledged open

**Enforced in code:** command allowlisting, device allowlisting, no
source-code credential fallback (see README "Security" for the full list and
`tests/test_security_boundaries.py` for the negative tests).

**Explicitly not solved, and not claimed to be:** there is no code-level
technique in this system (or, candidly, in any LLM-based system today) that
guarantees a sufficiently adversarial log line or config snippet can't
influence the LLM's own narrative reasoning about root cause. The boundary
that *is* enforceable — what commands/devices get touched — is enforced.
The boundary that *isn't* reliably enforceable — what the model concludes
from text it reads — is named as an open limitation rather than papered
over with a "sanitization" step that would create a false sense of safety
without actually bounding the risk.

## Alternatives considered and rejected

- **pyATS instead of Netmiko** — attempted twice, abandoned; see README
  "Known limitations" for the specific `unicon`/pty incompatibility found.
- **Embedding the full context for KB retrieval** (original implementation)
  vs. **a structured symptom fingerprint** (current) — the full-context
  approach shipped first and a real precision bug (wrong runbook matched on
  incidental vocabulary overlap) was caught live before the fingerprint
  replaced it. See README "Eval harness" for the measured before/after.
- **A single `target_device`** vs. **`target_devices: list[str]`** — shipped
  single-device first; the eval harness caught the real gap (0/3 on
  `mtu_mismatch`) before the list-based version replaced it. Kept here as a
  decision record, not just a bug fix, because the *reason* a single device
  was wrong (some faults are inherently comparisons) is a design insight,
  not just an implementation detail.
- **OpenTelemetry vs. structured JSON logging + a correlation ID contextvar**
  — OpenTelemetry's collector/exporter infrastructure isn't justified for a
  single-process, 2-device system; a `contextvars`-based correlation ID
  (`app/logging_config.py`) gets most of the practical value (grep one run's
  logs out of a shared stream) for a fraction of the operational surface
  area. Honest limitation: contextvars are thread-local, so Paramiko's own
  background transport thread's log lines don't carry the correlation ID —
  measured and documented, not silently accepted as "working."
