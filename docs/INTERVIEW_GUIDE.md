# Interview guide

20 questions, answered strictly from what's implemented in this repo — file
references point at real code. If pressed for more detail than fits here,
the honest move is "let me check the code," not improvising; everything
below is checkable in under a minute.

---

**1. Why LangGraph instead of one big prompt, or a hand-rolled state machine?**

A single prompt that both proposes *and* judges its own answer can't catch
itself being wrong — it can only sound confident either way. The graph
(`app/agent/graph.py`) separates evidence-gathering, hypothesis-forming, and
verification into nodes that don't share context by default: `validation`
only sees the hypothesis and the evidence gathered specifically to test it,
not the KB precedent or runbook hints that influenced the guess. A hand-rolled
state machine would work too — LangGraph's actual value here is the fan-out
(3 analyzers run concurrently in one superstep for free) and conditional
edges (`route_after_validation`) that map 1:1 onto `confirmed`/`retry`/
`exhausted`, so the state diagram and the code can't drift apart silently.

**2. Walk me through what stops the retry loop from looping forever.**

`max_retries=3` is checked in `validation` (`app/agent/nodes.py`):
`retry_count >= state["max_retries"]` routes to `exhausted` →
`escalate_inconclusive`, a terminal node that writes an inconclusive case to
the KB and ends the run. On the first pass, the Hypothesis Engine ranks a
primary *and* secondary candidate; a refuted primary advances to the
already-ranked secondary with zero extra LLM calls before falling back to a
fully informed regeneration (`_format_ruled_out` feeds every prior attempt's
evidence and refutation reason into the next prompt) — so by the time it
exhausts, it's exhausted 3 genuinely different, evidence-informed guesses,
not the same guess three times.

**3. Tell me about a real bug your evaluation process found.**

Three, same session, each measured not guessed at:
(a) KB/runbook retrieval embedded the *entire* raw context, and a wrong
runbook ("area ID mismatch") outranked the correct one ("interface shutdown")
on incidental vocabulary overlap — caught live via the Gradio KB panel,
confirmed by checking raw cosine similarities, fixed by embedding a
structured symptom fingerprint instead of raw text.
(b) `diagnostic_tool` only verified evidence from a hypothesis's *single*
device, so comparison-type faults (MTU mismatch between two routers) could
never be confirmed — measured at 0/3 accuracy by the eval harness, fixed by
making `target_devices` a list and gathering evidence from all of them,
re-measured at 100%.
(c) The `token_usage` langgraph reducer crashed on the very first *live* run
after I added it — it tried to sum real `usage_metadata`'s nested detail
dicts (`input_token_details`) as if they were plain ints. Caught immediately
because I ran it live instead of trusting the mocked unit tests, which had a
fake usage dict that didn't match the real shape. Fixed, and the test
fixture updated to match reality so this class of bug is caught by `pytest`
going forward, not just by luck of running it live.

**4. How do you stop the LLM from executing an unauthorized command?**

Before this pass: you didn't, really — `ALLOWED_COMMANDS` was prompt text
("use exactly these strings"), and `Hypothesis.verify_commands` (free-text
LLM output, Pydantic only validates it's a `list[str]`) went straight to
`run_commands` → `conn.send_command(cmd)`. Now `diagnostic_tool`
(`app/agent/nodes.py`) filters `verify_commands` against `ALLOWED_COMMANDS`
and `target_devices` against known `DEVICES` *before* anything is
dispatched; anything off-list is dropped and logged, never reaches the
device. This is the one boundary that's actually enforceable in code —
*what ASTRA is permitted to execute*. What the LLM is *persuaded to believe*
from log/config text it reads is a different, harder, and candidly unsolved
problem — see Q17.

**5. What happens if a device just doesn't respond?**

Before this pass: the whole graph run crashed with an unhandled exception,
before a diagnosis was even attempted — `logs_analyzer`/`topology_analyzer`/
`config_analyzer` called `run_commands` directly inside dict/list
comprehensions with no error handling. `_safe_run_commands` now catches
connection failures and returns them *as evidence*
(`DEVICE_UNREACHABLE: <reason>`) instead of raising. Live-verified: with
`R2_PORT` pointed at a closed port, ASTRA logged R2 unreachable and still
correctly diagnosed R1's actual fault using only R1's evidence.

**6. The exception types you catch for that — how did you pick them?**

I checked, I didn't guess. `netmiko.exceptions.NetmikoTimeoutException` and
`NetmikoAuthenticationException` — the two most likely to fire on a real
unreachable device — turn out to inherit from `paramiko.ssh_exception.
SSHException`, not from Netmiko's own `NetmikoBaseException`, confirmed via
`__mro__`. My first implementation only caught `NetmikoBaseException` and
the unit test caught the mistake immediately (the mocked exception
propagated right through). The fix catches
`(NetmikoBaseException, SSHException, OSError)`.

**7. Why does a hypothesis get a *list* of target devices now, not one?**

Because some faults are inherently a comparison, not a single-device
symptom — MTU mismatch, area-ID mismatch. A single `target_device` meant
`diagnostic_tool` could only ever gather one side's evidence, so Validation
correctly (and permanently) refused to confirm a cross-device hypothesis no
matter how many retries — measured at 0/3 for `mtu_mismatch` before the fix.
`Hypothesis.target_devices: list[str]` plus `diagnostic_tool` gathering
evidence from every named device fixed it, re-measured at 100%. The
prompt also explicitly tells the model to name a second device "only when
the hypothesis is inherently a comparison" — without that framing it
over-used two devices even for single-device faults.

**8. Your eval numbers look clean (100% in places). How do you know that's
not luck or a rigged test?**

I don't claim it's not luck without saying so. The harness runs each
scenario multiple times and reports mean±stddev, not a single pass
(`eval/run_eval.py --runs`). The README documents re-checking `mtu_mismatch`
specifically and getting 2/3, not 3/3, on a second pass — Validation
confirmed a plausible-but-wrong hypothesis once. That's reported as a known
limitation, not smoothed over, because the point of the harness is to be
more honest than a single demo run, not to produce a better-looking number.

**9. What's the single biggest unresolved reliability gap right now?**

Validation's confirm/refute threshold isn't well-calibrated in *either*
direction, and I have live evidence of both failure modes, not just one.
Too lenient once: on an `mtu_mismatch` re-check, Validation confirmed a
plausible-but-wrong hypothesis at 0.90 confidence — a false positive, not
the "exhausts into inconclusive" failure mode the bounded-retry design was
built to catch. Too strict elsewhere: on `ospf_network_missing`, measured
at 2/5 across two sessions, Validation refused to confirm the *literally
correct* root cause on its third, most-precisely-worded attempt, because the
evidence was an absence (no `network` statement present) rather than a
specific wrong value — the same underlying bias recurring, not resolved by
the earlier hypothesis-regeneration fix. Having evidence of both directions
is why I wouldn't reach for "just make the prompt stricter" or "just make it
more lenient" — either one fixes one failure mode and likely worsens the
other. The honest next step is a second, independent verification pass
(roughly double the LLM cost per diagnosis) rather than a prompt tweak to
the same single judgment call.

**10. How would this scale to 100+ real devices?**

Honestly: not as-is, and I won't claim otherwise. Today it's 2 devices,
queried in a loop, with a fresh `ConnectHandler` per call and no pool, no
worker-count limit, no bounded queue — because nothing needs bounding yet.
LangGraph's sync executor already gives free concurrency across the 3
analyzer nodes (one thread per node per superstep); past that, you'd need a
connection pool, an explicit concurrency limit (bounded `ThreadPoolExecutor`
or an `asyncio.Semaphore`), and per-device circuit breaking so one flapping
device doesn't starve the pool — `_safe_run_commands`'s isolation is the
prerequisite for that, not the whole of it. I'd rather say this than
fabricate a "tested at 500 devices" number from a simulator that doesn't
exist.

**11. Why Netmiko instead of pyATS, which is the more "enterprise" choice?**

Tried pyATS twice, specifically; it needs `unicon`, whose connection
handshake hung indefinitely against the lab's fake CLI under both `os: ios`
and `os: generic` profiles — ruling out "wrong OS profile" as the cause. The
real mismatch: the fake CLI is a line-based `asyncssh` process handler, not
a full pty-driven shell, and Unicon's handshake expects the latter. Making
pyATS work would mean building a pty-backed fake CLI or a custom Unicon
connection plugin — real effort with no payoff over Netmiko at this lab's
fidelity. Worth revisiting against a more IOS-faithful lab (real IOSv images
in GNS3/EVE-NG), not against this one.

**12. LLMs are non-deterministic. How do you test something whose output
changes run to run?**

Two different answers for two different things. Unit tests
(`tests/*.py`) never call a real LLM — they mock `llm.with_structured_output`
to return canned responses, because the point of those tests is the graph's
*branching logic* (does `retry_count` increment correctly, does the fast
path skip the LLM call), which is deterministic regardless of what any model
says. Actual diagnostic accuracy is measured separately, live, by the eval
harness, specifically *because* it's non-deterministic — multiple runs,
mean±stddev, not a single pass-or-fail assertion.

**13. What's actually trusted in this system, and what isn't?**

Trusted: the fake CLI's own output (it's a controlled simulator), the
`ALLOWED_COMMANDS`/`DEVICES` allowlists (enforced in code), environment-
supplied credentials. Not trusted: anything the LLM outputs as
`verify_commands`/`target_devices` (filtered before dispatch, Q4), the LLM's
own confidence score as proof of correctness (Validation re-checks against
evidence rather than accepting the Hypothesis Engine's say-so), and —
candidly — the LLM's own narrative reasoning when it reads untrusted
evidence text, which no code-level technique in this system claims to fully
secure (see Q17).

**14. Explain the token-usage bug end to end.**

I added cost/latency tracking to the eval harness, which needed real
per-call token counts. I used `with_structured_output(Model, include_raw=True)`
to get the raw `AIMessage.usage_metadata` (the modern, non-deprecated way —
not the sunset `langchain_community` callback). I wrote a LangGraph reducer
(`_merge_token_usage` in `app/agent/graph.py`) to fold each node's usage into
a running total across the whole run. First real CLI run after writing it:
`TypeError: unsupported operand type(s) for +: 'int' and 'dict'` — real
`usage_metadata` includes nested `input_token_details`/`output_token_details`
dicts alongside the three top-level int fields, and my reducer tried to sum
*every* key, including the dicts. Fixed by summing only the three named int
fields (`_TOKEN_USAGE_KEYS`), and updated the test fixture
(`tests/conftest.py`) to include the nested dicts too, so a mocked test would
have caught this the first time if I'd written the fixture correctly
up front.

**15. Why structured logging instead of OpenTelemetry?**

Scale. OpenTelemetry's value is coordinating traces across multiple
services/processes with a collector; this is one process talking to 2
devices. A `contextvars`-based correlation ID
(`app/logging_config.py::new_correlation_id`) plus optional JSON lines gets
the practical value — grep one run's logs out of a shared stream — for a
fraction of the operational surface area. Honest limitation I measured
rather than assumed: `contextvars` are thread-local, so Paramiko's own
background transport thread's log lines don't carry the correlation ID;
every log line from ASTRA's own code and its LLM calls does.

**16. Why does a human have to approve the fix instead of ASTRA just
applying it?**

Because Validation confirming a hypothesis isn't the same as the fix being
*safe* to run unattended, and Q9's false-positive finding is direct proof —
once, Validation confirmed the wrong root cause at 0.90 confidence. An
agent that's right most of the time and auto-applies fixes is an agent that
occasionally reconfigures a production router based on a wrong guess.
`human_approval` (CLI) / the Gradio UI's Approve/Reject buttons are the
actual safety boundary, not a UX nicety — `record_decision`/
`record_inconclusive` write the outcome back to the KB either way, so a
rejection is still useful future precedent, not a wasted run.

**17. Give me a case where ASTRA is overconfident, and what you'd do about
it.**

Q9's `mtu_mismatch` false positive: 0.90 confidence on a wrong root cause.
What I'd actually do, in priority order: (a) measure the rate properly —
one occurrence in a handful of runs isn't enough to act on, it's enough to
flag; (b) if the rate holds up at scale, add a second independent
verification pass before `root_cause_synthesis` runs, structured the same
way the primary Validation step is — evidence-grounded, not "are you sure?"
to the same context; (c) separately, there's no code-level fix for an LLM
being persuaded by misleading *evidence itself* (not a hallucinated
command/device, which is enforced, but a plausible-but-wrong interpretation
of real evidence) — that's a model-capability problem, not an engineering
one, and I'd say that plainly rather than oversell a mitigation.

**18. How do you know the LLM can't just invent a device name or command
that happens to work?**

`diagnostic_tool` filters both before dispatch (Q4) — an invented device
name isn't in `DEVICES`, an invented command isn't in `ALLOWED_COMMANDS`, so
neither reaches `run_commands` regardless of how plausible either looks.
`tests/test_security_boundaries.py` tests this directly: a hypothesis with
`verify_commands=["show ip interface brief", "reload", "write erase"]`
results in exactly one call to `run_commands`, with only the allowlisted
command.

**19. Walk me through what happens when you point ASTRA at a perfectly
healthy network — doesn't it have to invent a problem?**

Not by design, and I measured it rather than assumed it: the
`healthy_network` scenario has full OSPF adjacency, config matching golden
exactly, benign logs. The Hypothesis Engine still proposes plausible-sounding
candidates (nothing stops it from guessing) — but Validation, checking each
guess against the actual healthy evidence, correctly refutes all of them,
and the run exhausts into `INCONCLUSIVE` rather than confirming anything.
3/3 runs, 0% false positives on this sample. Small sample, explicitly not
claimed to generalize past what was measured — but it's the one scenario in
this suite that specifically tests whether the system can say "nothing's
wrong" instead of always producing an answer.

**20. If you started this project over, what would you do differently?**

Make `netmiko_tool.DEVICES` lazily resolved instead of built once at module
import time. It's a small thing that blocked a real improvement: the
`unreachable_device` scenario can't be wired into the automated multi-run
eval loop because changing `R2_PORT` mid-process doesn't affect the already-
built `DEVICES` dict — I had to document it as a manual repro instead
(README "Reliability"). Not a hard fix, just one I'd have made a design
default from the start rather than retrofit.
