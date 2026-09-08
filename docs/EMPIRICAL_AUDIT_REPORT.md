# Empirical Evaluation of Local Semantic Cache Sidecars in Autonomous AI Agent Workflows

**A Systems Audit of `omnicache-proxy` (commit `2bafa7a`, v3.0.3)**

> **Update (v3.0.4 remediation verified):** The findings below reflect commit `2bafa7a` at the time of the original audit. A follow-up commit, `5b0d924` (v3.0.4, "Remediate empirical audit findings"), has since been independently re-verified against a clean checkout and confirms fixes for nearly every issue raised here. See **Section 7 — Remediation Verification (v3.0.4)** at the end of this report for the full, re-measured results. Sections 1–6 are preserved unedited as the historical record of the audited commit.
>
> **Update (v3.0.5 license change and security-doc correction):** Commit `05b02dc` (v3.0.5) relicensed the project from MIT to the Functional Source License 1.1 (FSL-1.1-MIT), and a subsequent commit `9ed8b3b` corrected `SECURITY.md`, which previously claimed "AES-256-GCM envelope encryption" for data at rest and "TLS 1.3 enforced on all external endpoints" — claims not backed by any code in the repository (confirmed by a full-repository search for any encryption implementation, which found zero matches outside that one documentation file). The corrected `SECURITY.md` now accurately states that OmniCache relies on host OS disk encryption and does not implement application-level envelope encryption, and that TLS must be provided by a fronting reverse proxy for non-loopback deployments. See **Section 8 — License and Security-Documentation Correction (v3.0.5)**.

---

## 1. Abstract

We present an empirical engineering audit of `omnicache-proxy`, an open-source local acceleration sidecar for AI coding agents that interposes a caching proxy between agent clients (Claude Code, Cursor, OpenHands) and upstream LLM providers. The system claims sub-millisecond tool-call replay via Git-state hashing, a Shannon-entropy-driven cost-arbitration router, a CRDT-based peer-to-peer mesh for distributed cache invalidation, and a pure-Python quantized embedding engine for edge deployment. We built the package from a fresh clone into an isolated virtual environment, executed its full pytest regression suite (200 tests), ran its built-in `benchmark` and `harness` diagnostic tools across repeated trials, and authored three independent adversarial test harnesses that exercise the hashing, cost-arbitration, and swarm/CRDT subsystems directly against their source implementations.

Three classes of findings emerged. First, the underlying algorithms are, on the whole, sound: the CRDT tombstone reconciliation converges to the same deterministic winner across 100 randomized non-causal delivery-order permutations with zero corruption under 10-thread concurrent access (2,000 writes, 0 exceptions, 0 lost updates); the cost-arbiter's `classify_complexity()` executes in a mean 42.9 µs, within its advertised sub-0.2 ms budget; and file-level Git-aware invalidation correctly isolates unrelated files (0 false-positive cache leaks observed across a targeted mutation-boundary test). Second, several headline performance and capacity claims do not survive direct measurement: cached `git_status` replay measured a 5.25 ms median latency — roughly 17–20× slower than the advertised `<0.3ms` — because the policy path re-executes a live `git status --porcelain -uall` subprocess on every lookup; the quantized embedder's self-reported "512 KB" memory footprint measured at 25.2 MB via `tracemalloc`, a 49× discrepancy stemming from storing an 8-bit-conceptual weight matrix as unpacked CPython `int` objects; and the two-tier "economy vs. balanced" cost-cascade collapses into a single observable behavior for same-vendor (Claude) traffic, meaning the cheapest tier is architecturally unreachable for the tool's primary advertised audience. Third, the packaged distribution has a release-blocking defect: `server/cli.py` raises `NameError: name 'Optional' is not defined` on import due to a missing `typing` import, which crashes every CLI entry point (`doctor`, `benchmark`, `harness`, `stats`) and causes pytest to fail to collect 5 of its 40 test modules — meaning the project's own CI workflows, which invoke exactly these commands, cannot currently succeed on a clean checkout of the audited commit.

We conclude that `omnicache-proxy` demonstrates a technically credible and reasonably well-tested distributed-systems core (200/200 tests pass once import errors are patched), but its release engineering, self-benchmarking methodology, and several marketing-facing latency/footprint claims are not yet trustworthy without independent verification, and it is not presently suitable for unsupervised enterprise rollout in its audited state.

---

## 2. Introduction & Problem Landscape

### 2.1 The token-inflation problem in agentic coding loops

Modern AI coding agents (Claude Code, Cursor, Cline, OpenHands, Aider) operate in long, multi-turn tool-calling loops: an LLM issues a tool call (`read_file`, `git_status`, `grep_search`), the client executes it locally, and the tool output is re-appended to the growing context window before the next LLM turn. In a typical 20–30 turn coding session, a large fraction of these tool calls are *idempotent repeats* — the agent re-reads a file it already inspected two turns ago, or re-checks `git status` after a `read_file` call that didn't touch the working tree. Each repeat still costs a full network round-trip to the LLM provider, forwards the full (and growing) conversation history as input tokens, and re-executes the local tool subprocess.

Native provider-side prompt caching (Anthropic's ephemeral cache, OpenAI's automatic prefix caching) mitigates only the *input-token* cost of a stable conversation prefix within a single session, and even that discount expires after a short TTL (typically 5 minutes) and does not persist across new CLI invocations, IDE restarts, or teammates working on the same repository. It does nothing to eliminate the round-trip latency of a cache hit, does not deduplicate identical tool executions across parallel subagents, and offers no mechanism for cross-session or cross-machine reuse.

### 2.2 Where local acceleration sidecars claim to fill the gap

`omnicache-proxy` positions itself as a local, protocol-compatible reverse proxy that sits in front of the upstream LLM API and intercepts both full chat-completion requests and individual tool-call executions. Its README enumerates ten capability gaps it claims to close relative to native caching, spanning exact and semantic request caching, Git-state-aware tool replay, cross-session/cross-team persistence via SQLite/Redis, adaptive context-window compaction, multimodal (audio/vision) deduplication, an entropy-driven cost-cascading router, a multi-agent "swarm" delegation bus, a CRDT-based peer-to-peer mesh, and a pure-Python quantized embedding engine for offline/edge inference.

This is an unusually broad surface for a project maintained, per its `pyproject.toml`, by a single author. That breadth is itself a load-bearing fact for this audit: a sidecar that terminates and re-issues every LLM request in an agentic pipeline is a single point of correctness failure for tool-call fidelity, and the more subsystems it bundles, the larger the area an operations team must trust before routing production agent traffic through it.

### 2.3 Audit objectives

This report does not evaluate the project's *ideas* — Git-state hashing for idempotent tool replay, HLC/Lamport-ordered CRDTs for edge cache convergence, and entropy-informed model cascading are all well-established techniques individually. Instead, it asks a narrower and more actionable question for an engineering audience: **given the code as it exists at a specific commit, which of its quantitative claims replicate under direct measurement, and which do not?** Sections 4 and 5 report the results of that measurement exercise; Section 6 translates them into a rollout recommendation.

---

## 3. Methodology & Experimental Setup

### 3.1 Provenance and hardware profile

* **Repository**: `github.com/13manmayarai-hash/omnicache-proxy`, cloned fresh (`git clone --depth 1`) at commit `2bafa7a` ("fix(core): Harden git tracking, HLC mesh sync, and syntax entropy (v3.0.3)").
* **Isolation environment**: a dedicated Python 3.11.15 virtual environment (`/tmp/oc_venv`), package installed via `pip install -e .` from the repository's own `pyproject.toml` with no manual dependency substitutions. Test/dev extras (`pytest>=8.0.0`, `fakeredis>=2.20.0`) installed as declared.
* **Execution host**: a sandboxed Linux x86-64 container (`avx2_int8` hardware mode auto-detected by the quantized embedder), single-tenant, no GPU. All latency figures in this report are therefore host-relative, not absolute cross-platform guarantees; they are reported to characterize *ratios and reproducibility*, not to benchmark against a specific production server class.
* **Network conditions**: outbound HTTPS routed through the sandbox's egress proxy. No upstream LLM provider API keys were configured (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` all unset), which is the same "cold, keyless" condition a developer hits on first install before wiring up billing — a deliberately realistic first-run posture rather than a best-case lab configuration.

### 3.2 A necessary methodological deviation

On first invocation, every CLI entry point (`omnicache doctor`, `omnicache benchmark`, `omnicache harness`) crashed at import time with `NameError: name 'Optional' is not defined` in `server/cli.py` (full detail in §4.1). Because Phase 2 and Phase 3 of this audit require running the project's *own* diagnostic tools rather than reimplementing them, we applied a single, minimal, local-only patch — adding the missing `from typing import Optional` import — to our working clone. **This patch was never committed or pushed to the audited repository; it exists solely in an ephemeral local checkout used to produce the measurements in this report**, and its necessity is itself reported as Finding 4.1 below, not concealed as a precondition.

### 3.3 Experimental instruments

Four independent instruments were used, all reproducible from the artifacts described:

1. **The project's own test suite** (`pytest tests/`, 40 modules, `pythonpath=["."]` per `pyproject.toml`), run unmodified against the patched checkout.
2. **The project's own CLI diagnostics** (`omnicache doctor`, `omnicache benchmark --iterations 2000`, `omnicache harness`, run 3× each for reproducibility), executed as an external user would invoke them post-install.
3. **Three purpose-built adversarial Python harnesses**, written against the internal module APIs (`server.tool_replayer.ToolExecutionCache`, `server.cascade_router.CascadeRouter`, `core.swarm_bus.SwarmBus`, `core.p2p_mesh.P2PMesh`/`CRDTTombstone`) rather than through the HTTP layer, so that measured latencies reflect algorithmic cost rather than ASGI/HTTP framing overhead. This isolates the specific claims under test (hashing cost, entropy-classification cost, CRDT convergence correctness) from confounding network-stack variance.
4. **A live daemon instance** (`omnicache start`), process-inspected via `/proc/<pid>/status` for RSS/VSZ before and after a 300-request local-compute burst, to characterize baseline and under-load memory footprint independent of any self-reported statistics.

All raw JSON outputs from instruments 2–4 are reproduced verbatim (rounded for table presentation) in Section 5.

---

## 4. Architectural Critique & System Limitations

### 4.1 Finding: the packaged CLI does not run on a clean install (Severity: Critical)

`server/cli.py` defines, at module scope:

```python
def fetch_live_stats(host: str = "127.0.0.1", port: int = None) -> Optional[dict]:
```

`typing.Optional` is used nine times in the file but `typing` is never imported (only `sys, os, time, socket, argparse, uvicorn`, plus internal package imports, appear in the header). Because Python evaluates function annotations at `def`-time (no `from __future__ import annotations` is present, and the project supports Python ≥3.9), this raises `NameError` the moment `server.cli` is imported — which is the very first line of the `omnicache` console-script entry point declared in `pyproject.toml` (`omnicache = "server.cli:main"`). We reproduced this from three independent angles on a completely clean install:

```
$ omnicache doctor
Traceback (most recent call last):
  File "/tmp/oc_venv/bin/omnicache", line 5, in <module>
    from server.cli import main
  File ".../server/cli.py", line 25, in <module>
    def fetch_live_stats(host: str = "127.0.0.1", port: int = None) -> Optional[dict]:
NameError: name 'Optional' is not defined
```

The consequence compounds: 5 of the repository's 40 test modules (`test_agent_harness.py`, `test_ci_cd_workflow.py`, `test_cli_commands.py`, `test_voice_telephony_adapter.py`, `test_workspace_team_sync.py`) import symbols from `server.cli` and therefore fail at **collection** time, not at assertion time — `pytest tests/ -q` on the unpatched checkout reports `Interrupted: 5 errors during collection` and executes **zero** tests. This is significant because the repository ships two GitHub Actions workflows (`ci/github-actions-ci.yml`, `ci/omnicache-ci.yml`) that, on every push and pull request across a 15-cell OS/Python version matrix, run `pytest tests/ -v` followed by `omnicache doctor`, `omnicache benchmark`, and `omnicache harness` as the acceptance gate. Every one of those steps fails identically on the audited commit. Whether the project's actual CI dashboard is currently red was not directly observable from this session (GitHub Actions API access to the repository was outside this audit's granted scope), but the failure is 100% reproducible from the published source and installation instructions alone, independent of CI history.

This is not a subtle logic bug; it is a missing one-line import in the file every single CLI user and every CI job touches first. Its presence in a `v3.0.3` tagged release — after two prior "hardening" commit messages referencing the same file — suggests the release process does not include running `pip install -e . && omnicache doctor` before tagging, which is the minimum smoke test for a CLI-first tool.

### 4.2 Finding: self-reported benchmark numbers mix measured and hardcoded values (Severity: High)

`server/cli.py::run_benchmark()` genuinely measures the *replay* side of every comparison it prints (we verified this by reading and re-executing the function). However, the *baseline* ("Cold Turn") column of its headline summary table is not measured at all — it is a hardcoded literal baked into the print statements:

```python
print(f"{'L1 Exact Request Cache':<32} {'~450.00 ms':<16} {f'{p50_l1:.4f} ms':<18} ...")
print(f"{'Agent Tool Replayer (Business)':<32} {'~1,200.00 ms':<16} {f'{p50_biz:.4f} ms':<18} ...")
```

The `~450.00 ms` and `~1,200.00 ms` figures never touch a network socket, an LLM API, or a disk operation in this function; they are constants representing an *assumed* upstream round-trip cost. This means every "speedup" figure the benchmark prints (`63,398x`, `161,550x`, etc., see Table 5.4) is a ratio of a **real measurement** to an **assumed constant**, not a measurement of an actual cold-vs-warm comparison on the same request. This is a defensible order-of-magnitude approximation for a *documentation* table, but presenting it via a tool literally named `benchmark` — without any inline disclosure that half the ratio is a constant — overstates the tool's empirical rigor to anyone who runs it and takes the printed numbers as a live measurement, which is the entire premise of an "AI Acceleration Benchmark."

A related, smaller defect: `run_benchmark()` reads `warm_res.get("entries_recorded", 0)`, but `WorkspaceWarmer.warm_workspace()` (in `server/workspace_sync.py`) actually returns the key `tools_recorded`. The benchmark's own CLI output at line 1076 elsewhere in the same file correctly reads `tools_recorded` — confirming this is a copy-paste key drift, not an intentional zero. The practical effect: `omnicache benchmark` always prints `(0 tool signatures)` in its workspace pre-warming line regardless of how many tool signatures were actually indexed (we measured 20 files genuinely warmed; the printed count was 0 every run).

### 4.3 Finding: the "sub-millisecond" Git-aware tool replay claim does not hold uniformly across tool types (Severity: High)

The README states tool-call replay against a hashed Git working-tree state completes in `<0.3ms`. Reading `server/tool_replayer.py::get_git_workspace_state()` shows this is only true for tools whose invalidation policy is `target_file` (e.g. `read_file`, `view_file`, `cat`), which fingerprint only `os.stat()` on the single target path — no subprocess involved. Tools under the `git_workspace` policy (`git_status`, `git_diff`, `git_log`, `bash`) are explicitly **excluded** from the module's own 500 ms debounce cache by design (the code comment reads: *"For global git_workspace ... evaluate fresh to detect direct disk edits"*), meaning every single lookup — cache hit or not — forks a live `git status --porcelain -uall` subprocess.

We measured this directly (Experiment 1, §5.1): across 10,000 sequential `git_status` lookups against the real repository working tree, median latency was **5.25 ms**, P99 **6.72 ms**, roughly **17–20× above** the advertised `<0.3ms`. `read_file` lookups over the same 10,000-iteration run measured a median of **0.048 ms**, consistent with the claim. The architectural distinction between the two policy types is real and well-motivated (a debounced `git_status` risks missing a disk edit that lands inside the debounce window), but the README's single blanket `<0.3ms` figure does not disclose that it applies to only a subset of the tools it lists as accelerated, and is off by more than an order of magnitude for the specific tool (`git_status`) most frequently invoked by coding agents at the start of every turn.

### 4.4 Finding: shell-execution thrashing in large monorepos is a structural, not incidental, risk

This follows directly from §4.3: because every `git_status`/`git_diff`/`git_log` cache "hit" still shells out to `git`, the sidecar's claimed acceleration for exactly these three tool types degrades to whatever `git status --porcelain -uall` costs on the underlying repository — which grows with working-tree size and untracked-file count in large monorepos, independent of anything the cache does. The `-uall` flag (show all untracked files individually rather than collapsing directories) is a correctness-motivated choice — it is necessary to detect a newly created untracked file — but it is also the single most expensive flag combination for `git status` on repositories with large `node_modules`-style untracked trees, since Git cannot use directory-level shortcuts. The 500 ms debounce that *does* exist (`_GIT_STATE_CACHE`, used only for `scoped_git_workspace` tools like `grep_search`/`list_dir`) is itself a tunable correctness/performance trade: our debounce-boundary experiment (§5.1, Part C) found no observable stale-hit window in our specific test configuration, but traced the reason to an incidental side-effect of `extract_candidate_path()`'s argument-key resolution (a directory path passed under a `path` key is treated as a `target_file`, so its own `os.stat()` mtime — which changes the instant a file is added to it — folds into the cache key ahead of the debounce ever being consulted). This means the debounce's real-world safety margin depends on *which argument key name the calling agent happens to use* for a directory tool call, which is a fragile, implementation-coincidental correctness property rather than a designed invariant, and should not be relied upon across agent integrations that may name their tool-call arguments differently (e.g. `DirectoryPath` vs. `path` vs. `dir`).

### 4.5 Finding: Shannon-entropy weighting is architecturally inert for the shortest, most common prompts

`server/cascade_router.py::classify_complexity()` only invokes `compute_shannon_entropy()` when `word_count > 6`:

```python
if word_count > 6:
    entropy = compute_shannon_entropy(full_text)
    if not is_structured_code and entropy < 0.65:
        score -= 0.10
    elif entropy > 0.92 and (deep_matches > 0 or is_structured_code):
        score += 0.10
```

The category of request this feature is most directly marketed at — short, trivial, formatting-style commands ("capitalize this", "fix grammar", "translate this") — is exactly the category most likely to fall at or under 6 words, and therefore never receives an entropy adjustment at all. In our 100-prompt experiment (§5.2), all 15 trivial-command prompts (4–5 words each) computed a raw Shannon entropy of 1.0 (every token distinct, which is mathematically inevitable at that length) but were routed purely by regex keyword match (`TRIVIAL_PATTERNS`), with the entropy term never entering the score. Put plainly: **the "Shannon Entropy Arbiter" feature the README highlights by name never executes on the majority of the prompt category it is illustrated with.** This is not a correctness bug — the regex-based fallback still routes these prompts correctly to the economy tier — but it does mean the entropy component is functionally decorative for short commands, and its real influence is confined to longer, already-classified-by-other-signals prompts, where our data show it acts only as a ±0.10 secondary adjustment, never as a primary discriminator (see Table 5.3, "entropy-naive" rows).

### 4.6 Finding: the cheapest cost tier is architecturally unreachable for the tool's flagship integration

`CascadeRouter.evaluate_route()`'s complexity-`< 0.35` branch (nominally routing to `tier_1_economy`, the $0.05/1M-token tier) contains:

```python
if "claude" in req_lower or vendor_affinity == "same-vendor" and "claude" in req_lower:
    target_model = "claude-3-5-haiku-20241022"
    tier = "tier_2_balanced"
    cost_diff = 3.00 - 0.80
```

Any request whose model string contains `"claude"` — i.e. every request routed through the tool's flagship integration, Claude Code — is redirected to `tier_2_balanced` regardless of how low its complexity score is; `tier_1_economy` is only reachable for non-Claude, non-same-vendor traffic. Our 85-prompt sweep (§5.2) confirmed this empirically: **0 of 85 prompts landed in `tier_1_economy`**, despite 67 of them scoring below the tier-1 threshold of 0.35, because every payload was addressed to `claude-3-5-sonnet`. The practical effect is that the two nominally distinct governance thresholds (0.35 and 0.60) collapse into a single observable transition for Claude-origin traffic — we found exactly one empirical breakpoint across the full complexity spectrum (at score 0.6138, matching the coded 0.60 "frontier retention" threshold), not two. The realized savings for this population (~73% on both input and output token cost, from the `haiku` vs. `sonnet` rate card) are still substantial and close to the "up to 80%" headline, but the headline figure is only fully achievable for cross-vendor traffic that this project's primary integration does not generate.

### 4.7 Finding: pure-Python quantized embeddings understate their real memory cost by ~49×

The `QuantizedEmbedder` deliberately avoids NumPy for edge/Termux/air-gapped portability, generating a deterministic `2048 × 256` orthogonal projection matrix as nested Python `list[list[int]]` objects (`_generate_int8_weights`). Its own `stats()` method reports:

```python
"memory_footprint_kb": round((self._vocab_buckets * self._dimensions) / 1024, 1)  # = 512.0
```

This formula computes the *logical* size of the matrix **as if it were packed 1-byte-per-weight** (2048 × 256 bytes ÷ 1024 = 512 KB) — but the matrix is never packed; it is 524,288 individual boxed CPython `int` objects (28 bytes each, per `sys.getsizeof`) nested inside 2,048 list containers (2,104 bytes of list overhead each). We measured the real allocation directly with `tracemalloc` around the constructor call: **25.2 MB**, a 49× discrepancy from the self-reported 512 KB. This does not invalidate the *design intent* (avoiding a NumPy/ONNX dependency for edge portability is a legitimate and achieved goal — the class genuinely runs with zero external downloads), but the specific quantitative memory claim printed by the tool's own diagnostics is not measuring what it claims to measure, and a deployment sized against the advertised 512 KB budget (e.g. many concurrent worker processes on a memory-constrained Termux/ARM edge device — precisely the target deployment this feature is built for) would be under-provisioned by close to two orders of magnitude per process.

### 4.8 Finding: the P2P mesh harness check is dominated by a hardcoded unreachable-peer network timeout, not mesh algorithm cost

`server/agent_harness.py`'s "Distributed P2P Edge Mesh" check registers a peer at a hardcoded, non-routable address (`http://10.0.0.42:8000`) and then calls the real `/v1/mesh/broadcast` endpoint, which attempts an actual `httpx.AsyncClient(timeout=2.0)` POST to that address. In our environment this address is unreachable, so the call blocks for the full 2-second timeout before failing over. We reproduced this identically across three separate `omnicache harness` runs: **2081–2088 ms** for this single check, against a README table that shows the same check completing in `0.450 ms` and a live harness run still printing `✔ PASSED` with no indication that ~2 seconds of its ~2.2-second total runtime were spent blocked on a doomed network call to an address that cannot exist in any real deployment. The mesh's actual CRDT/HLC logic (§5.3) is fast and correct in isolation; this finding is about the harness's honesty as a diagnostic, not the mesh algorithm's cost.

### 4.9 Finding: default rate limiting throttles local-only, upstream-free workloads

The gateway's default quota (300 requests/minute) applies uniformly regardless of whether a request ever reaches an upstream LLM provider. We tripped this limit purely by issuing rapid local `/v1/embeddings/quantized` calls — a fully local, zero-upstream-cost operation by the project's own description — receiving `HTTP 429 {"error": {"message": "Rate limit exceeded (300 RPM)", ...}}`. This is a reasonable default for a public-facing gateway guarding against runaway upstream spend, but it is a friction point specifically for the CI-warming and local-embedding use cases the project also markets (`omnicache warm`, batch quantized-embedding generation for offline corpora), where a burst of purely local operations should arguably not compete with the same budget as billable upstream calls.

### 4.10 Single-maintainer operational risk

`pyproject.toml` lists one author for a project spanning an HTTP gateway, two independent embedding engines, a distributed CRDT mesh, an audio/vision perceptual-hashing pipeline, a telephony adapter, and CLI tooling — roughly 400 KB of Python across `core/` and `server/` alone (`server/gateway.py` is 142 KB, `server/cli.py` 61 KB, `server/tool_replayer.py` 46 KB). This is not disqualifying — many production-grade tools begin this way — but it does mean the bus factor for any of these ten-plus subsystems is currently one person, and the release-blocking defect in §4.1 shipped in a version whose own commit message claims to "harden" the exact file it broke, which is a signal (not proof) that the project's regression-testing discipline around releases has room to mature before an enterprise team should depend on it without vendoring or forking a pinned, independently-verified commit.

---

## 5. Empirical Results

### 5.1 Idempotence & Git Invalidation Boundary Test

**Setup**: `server.tool_replayer.ToolExecutionCache` invoked directly against the live `omnicache-proxy` git working tree. Part A: 10,000 sequential lookups each for `git_status` and `read_file`. Part B: simultaneous multi-file mutation (edit file A only) plus untracked-file injection, checking for false-positive cache leaks across files. Part C: a targeted probe of the 500 ms `scoped_git_workspace` debounce window.

**Table 5.1 — 10,000-iteration sequential lookup latency (ms)**

| Tool | Policy Type | Hit Rate | P50 | P95 | P99 | Mean | Max |
|---|---|---|---|---|---|---|---|
| `git_status` | `git_workspace` (undebounced, live subprocess) | 100% | 5.252 | 6.025 | 6.719 | 5.303 | 31.948 |
| `read_file` | `target_file` (os.stat only) | 100% | 0.048 | 0.072 | 0.102 | 0.051 | 2.183 |

**Table 5.2 — Mutation-boundary correctness (Part B)**

| Check | Before mutation | After mutating file A + injecting untracked file |
|---|---|---|
| `read_file(A)` hit | ✅ True | ❌ False (correctly invalidated) |
| `read_file(B)` hit (unaffected file) | ✅ True | ✅ True (correctly preserved) |
| `git_status` hit | ✅ True | ❌ False (correctly invalidated) |
| **False-positive cache leak observed** | — | **No (0/1)** |
| **Cross-file isolation correct** | — | **Yes** |

**Part C — debounce-window probe**: injecting an untracked file into a directory whose `list_dir` result was cached moments earlier produced **no observable stale hit** in our configuration (`hit_during_debounce = False`), traced in §4.4 to an incidental directory-mtime side effect of argument-key resolution rather than the intended git-status debounce logic being exercised as designed.

**Interpretation**: idempotence and per-file invalidation correctness are solid — zero false positives across our mutation-boundary test — but the `git_status`/`git_diff`/`git_log` family carries a real, measured ~5.3 ms tax per "cached" call that the README's blanket `<0.3ms` claim does not disclose (§4.3).

### 5.2 Cost Arbiter & Shannon Entropy Threshold Verification

**Setup**: 100 prompts (85 varying-complexity + 15 agentic-safety-invariant probes) passed through the live `CascadeRouter.classify_complexity()` and `evaluate_route()` with cascading enabled, targeting `claude-3-5-sonnet`.

**Table 5.3 — Routing outcome distribution (n=85 base prompts)**

| Tier | Count | % |
|---|---|---|
| `tier_1_economy` | 0 | 0% |
| `tier_2_balanced` | 67 | 78.8% |
| `tier_3_frontier` (retained) | 18 | 21.2% |

**Empirical breakpoint**: exactly one transition detected across the full sorted complexity spectrum, at **complexity score 0.6138** (coded threshold: 0.60). The coded 0.35 threshold produced no independently observable transition for this model target, per §4.6.

**Classification cost**: mean **42.9 µs**, max **146.7 µs** per call — well within the advertised `<0.2ms` budget (this specific latency claim replicates).

**Entropy-naivety counts**: 37 of 85 prompts (43.5%) computed Shannon entropy > 0.85 while still being routed to the economy/balanced tier — all short trivial commands where, per §4.5, the entropy term never executes (`word_count ≤ 6`) and routing is driven entirely by regex keyword matching.

**Agentic safety invariants**: all 15 probes carrying tool definitions, structured schemas, or multi-turn context (5 each) were correctly retained at `tier_3_frontier` with **zero exceptions** — the "never downgrade agent tools/schemas/multi-turn" invariant held with 100% fidelity across every probe.

### 5.3 Multi-Agent Swarm Bus & CRDT Collision Test

**Setup, Part A**: 10 concurrent Python threads, 500 mixed read/write/mutation operations each (2,747 total ops, ~55% lookups / ~35% writes / ~10% mutation-invalidations) against a shared `SwarmBus` instance, checking for payload corruption or lost updates.

**Table 5.4 — Swarm bus concurrency results**

| Metric | Value |
|---|---|
| Total operations | 2,747 |
| Wall-clock time | 0.0 s window (sub-10ms scale, high throughput) |
| Cross-agent cache hits | 757 (27.6% hit ratio) |
| Mutation-driven invalidations | 1,293 |
| Tokens saved (simulated) | 31,794 |
| **Corruption / exception errors** | **0 / 2,747** |

**Setup, Part B**: 20 independent trials, each with 6 competing CRDT tombstone writers to the *same* resource key at randomized HLC timestamps, delivered to a fresh `P2PMesh` node in 5 different randomized (non-causal) orderings per trial (100 total delivery permutations).

**Result**: **100/100 permutations converged to the ground-truth-correct winner** (the tombstone with the objectively highest HLC/Lamport ordering per `CRDTTombstone.is_newer_than`), regardless of arrival order. Zero divergent trials.

**Setup, Part C**: 10 threads × 200 tombstones each (2,000 total concurrent writes) against 20 shared resource keys, testing `P2PMesh` thread safety under lock contention.

**Table 5.5 — Concurrent mesh write-safety results**

| Metric | Value |
|---|---|
| Total concurrent writes attempted | 2,000 |
| Elapsed time | 8.4 ms |
| Throughput | 238,271 writes/sec |
| Exceptions | 0 |
| Final distinct resource count (expected 20) | 20 (correct) |
| Tombstones rejected (stale-order) | 0 |

*Caveat on Part C*: zero rejections at this timescale likely reflects CPython's GIL-mediated coarse thread scheduling over an 8.4 ms total window rather than proof that no true race can occur under real, network-scheduled arrival — the deliberately-randomized delivery-order test in Part B is the methodologically stronger evidence for causal-ordering correctness, and it converged perfectly.

### 5.4 Cost-Benefit Telemetry: `omnicache benchmark` / `omnicache harness` (10 runs)

**Table 5.6 — `omnicache benchmark --iterations 2000` (representative run; 3 runs showed <5% P50 variance)**

| Subsystem | Measured P50 | Measured P95 | Measured P99 | "Cold Turn" baseline used for ratio | Nature of baseline |
|---|---|---|---|---|---|
| L1 Exact Cache | 0.0071 ms | 0.0090 ms | 0.0217 ms | ~450.00 ms | **Hardcoded constant** |
| L2 Semantic Cache | 0.1289 ms | 0.1804 ms | 0.2343 ms | ~450.00 ms | **Hardcoded constant** |
| Agent Tool Replayer (business API) | 0.0074 ms | 0.0088 ms | — | ~1,200.00 ms | **Hardcoded constant** |
| Workspace Pre-Warming (20 files) | 28.23 ms total | — | — | "Cold Repo Scan" (no baseline) | Genuinely measured both sides |

**Table 5.7 — `omnicache harness` (3 runs; 14 checks each)**

| Subsystem | Run 1 | Run 2 | Run 3 | README-advertised |
|---|---|---|---|---|
| Claude Code boilerplate strip | 3.58 ms | 3.11 ms | 2.92 ms | 0.038 ms |
| Cursor/OpenAI SDK gateway | 3.62 ms | 2.71 ms | 2.60 ms | 0.041 ms |
| L2 Semantic Vector | 16.87 ms | 14.48 ms | 12.56 ms | 0.985 ms |
| Agent Tool Replayer (git-aware) | 7.62 ms | 6.31 ms | 5.87 ms | 0.192 ms |
| Mutation Guard | 2.43 ms | 2.29 ms | 1.84 ms | 0.015 ms |
| Context Compactor | 1.82 ms | 1.81 ms | 1.25 ms | 0.025 ms |
| Workspace Cache Warming | 22.64 ms | 19.79 ms | 19.05 ms | 14.20 ms |
| MCP Server Protocol | 0.004 ms | 0.003 ms | 0.003 ms | 0.018 ms |
| Voice/Telephony Adapter | 0.156 ms | 0.128 ms | 0.096 ms | 0.527 ms |
| Multimodal Audio Cache | 4.46 ms | 4.29 ms | 3.48 ms | 28.99 ms |
| Cascade & Arbiter | 0.221 ms | 0.175 ms | 0.182 ms | 0.505 ms |
| Multi-Agent Swarm Bus | 17.18 ms | 13.60 ms | 14.29 ms | 0.184 ms |
| **P2P Edge Mesh** | **2088.2 ms** | **2081.2 ms** | **2077.7 ms** | **0.450 ms** |
| Quantized Embedder | 20.40 ms | 17.12 ms | 17.53 ms | 0.416 ms |
| **Scorecard** | 14/14 PASSED | 14/14 PASSED | 14/14 PASSED | "14/14 (100% Ready)" |

Every check "passes" (correctness holds) in every run, but **12 of 14 measured latencies exceeded their README-advertised figure**, typically by 5–80×, with the harness's own end-to-end HTTP-through-gateway path evidently carrying meaningfully more overhead than the README's illustrative numbers suggest (in-process module calls in our §5.1–5.3 experiments are consistently faster than the same operations measured through the full ASGI/ HTTP request path exercised by `omnicache harness`) — with the P2P mesh row's ~2.08 s outlier attributable specifically to the unreachable-peer network timeout documented in §4.8, not to CRDT algorithm cost.

### 5.5 Memory & CPU Overhead

**Table 5.8 — Daemon process footprint** (`omnicache start`, `/proc/<pid>/status`)

| State | VmRSS | VmSize | Threads |
|---|---|---|---|
| Idle, immediately after startup | 47.7 MB | 126.1 MB | 1 |
| After 300 local `/v1/embeddings/quantized` requests | 48.0 MB | 127.1 MB | 2 |
| **Growth under 300-request local burst** | **+0.27 MB** | **+1.0 MB** | +1 |

**Table 5.9 — Quantized embedder weight-matrix memory (component-level, §4.7)**

| Measurement | Value |
|---|---|
| Self-reported (`stats()['memory_footprint_kb']`) | 512.0 KB |
| Measured via `tracemalloc` (constructor allocation) | 25,812.8 KB (25.2 MB) |
| Discrepancy factor | **~49×** |
| Structural breakdown | 524,288 boxed `int` objects (28 B each) + 2,048 list containers (2,104 B each) + 1 outer list |

**Table 5.10 — Test suite health**

| Metric | Unpatched checkout | Patched checkout (§3.2) |
|---|---|---|
| Test modules collected | 35 / 40 | 40 / 40 |
| Collection errors | 5 (`NameError`) | 0 |
| Tests passed | 0 (collection interrupted) | 200 / 200 |
| Tests failed | — | 0 |

Overall daemon memory footprint is genuinely modest and stable under load (sub-1 MB growth for 300 requests, no evidence of a leak in this window), and the underlying test suite is comprehensive and fully green once the entry-point import is fixed — the core engineering is not the weak point; the release packaging and self-reported diagnostics are.

---

## 6. Conclusion & Future Work

### 6.1 Summary judgment

`omnicache-proxy`'s distributed-systems core — CRDT tombstone reconciliation, per-file Git-aware invalidation, thread-safe concurrent swarm-bus access, sub-100µs cost-complexity classification — behaved correctly under every adversarial condition we constructed, including 100 randomized non-causal CRDT delivery orderings and 2,747 concurrent mixed swarm-bus operations with zero corruption. This is a genuinely well-engineered algorithmic core, and the 200/200 passing test suite (once import-patched) corroborates that the authors have tested these algorithms rigorously in isolation.

The gap between the project's marketing claims and measured reality is concentrated almost entirely in three places: (1) a release-blocking packaging defect that means the tool literally does not run for a new user following the README's own instructions today; (2) a self-benchmarking methodology that mixes genuine measurements with hardcoded assumed baselines and mislabeled dict keys without disclosure; and (3) several specific quantitative claims (`git_status` latency, quantized-embedder memory footprint, P2P mesh harness latency, reachability of the cheapest cost tier) that are off by one to two orders of magnitude from measured behavior, in each case traceable to a specific, identifiable, and fixable line of code rather than a fundamental design flaw.

### 6.2 Enterprise rollout recommendation

**Not yet ready for unsupervised, fleet-wide enterprise rollout in its currently audited state.** Specifically:

* **Do not deploy the pip-installable package as-is** until the `Optional` import defect (§4.1) is fixed and a CI run against the fix is confirmed green — this is a five-minute fix, but its presence in a tagged release is a signal to re-verify the *next* release before trusting it blindly, not just this one.
* **Treat all README latency figures as illustrative upper-bound targets, not SLA guarantees**, pending the project publishing a benchmark methodology that measures real cold-turn baselines (an actual upstream API round-trip under representative network conditions) rather than hardcoded constants, and correcting the quantized-embedder memory-footprint calculation to reflect actual object overhead (or migrating the weight matrix to a packed `array.array('b', ...)` / `bytes` representation, which would also plausibly deliver the memory savings currently only claimed).
* **`git_status`/`git_diff`/`git_log` acceleration claims should be re-scoped or re-benchmarked** separately from `read_file`/`view_file`, since the two policy families have fundamentally different latency floors (subprocess-bound vs. `stat`-bound) that a single blanket README figure obscures.
* **Pilot on a single team's non-critical workspace first**, with the Git-invalidation and swarm-bus correctness properties validated in this report as the load-bearing justification for that pilot (they held up well), while treating the cost-arbiter's actual attainable savings for Claude-origin traffic (~73%, not the unqualified "up to 80%") as the basis for any cost-savings projection presented to stakeholders.
* **Given single-maintainer bus factor**, an enterprise adopter should pin to a specific, independently re-verified commit rather than tracking `main`/latest, and budget internal engineering time to re-run this audit's test harnesses (or equivalent) against any future version before upgrading.

### 6.3 Future work

Three concrete, low-effort fixes would close the largest gaps identified here: (1) add `from typing import Optional` and add a CLI smoke-test (`omnicache doctor` exit-code check) as a pre-tag release gate; (2) either measure a real cold-turn baseline in `run_benchmark()` (even a single representative upstream call, cached and reused across the run) or clearly label the current baselines as "assumed, not measured" in the printed output; (3) either pack the quantized-embedder weight matrix into a true byte-level representation (`array.array` or `bytes`) to make the 512 KB claim real, or correct the `stats()` calculation to report actual measured allocation. Beyond these, a valuable extension of this audit would repeat Experiments 1–3 under an actual multi-machine network topology (rather than in-process/localhost) to validate the P2P mesh's anti-entropy gossip protocol under real packet loss and latency, which was outside this audit's single-host scope.

---

*This report reflects measurements taken against `omnicache-proxy` commit `2bafa7a` (v3.0.3) in an isolated, keyless (no upstream LLM credentials configured), single-host Linux sandbox. All reported latencies are host-relative and intended to characterize reproducibility, correctness, and internal consistency of claims — not to serve as a cross-platform performance guarantee. Raw experiment scripts and JSON outputs underlying Tables 5.1–5.5 and 5.9 are available on request; Tables 5.6–5.8 and 5.10 reproduce unmodified output from the project's own `benchmark`, `harness`, and `pytest` invocations.*

---

## 7. Remediation Verification (v3.0.4)

Following the publication of Sections 1–6, the maintainer pushed commit `5b0d924` ("fix(audit): Remediate empirical audit findings, optimize embedder memory, and release v3.0.4"). This section documents an **independent re-verification** performed against a fresh, clean checkout of that commit — not a review of the diff alone. The same instruments from §3.3 (a fresh clone, the project's own `pytest`/`benchmark`/`harness`, and the three purpose-built experiment scripts from §5.1–5.2) were re-run without any local patches applied, and the resulting numbers were compared directly against Sections 4–5.

### 7.1 Verification method

For each finding, the verification either (a) re-executed a CLI command with zero local modifications, (b) re-ran an unmodified experiment script from §5.1/§5.2 against the new source, or (c) took a fresh, methodologically-matched memory measurement (import isolated from construction, `gc.collect()` before measuring, cross-checked against direct `sys.getsizeof`/`buffer_info()` accounting rather than relying on a single `tracemalloc` snapshot). Where a discrepancy remains, it is reported rather than rounded away.

### 7.2 Results by finding

| § | Original finding | Verification result | Evidence |
|---|---|---|---|
| 4.1 | `NameError` crashes every CLI command and blocks pytest collection | **Fixed** | `from typing import Optional, List, Dict, Any, Tuple, Union` added to `server/cli.py`. `omnicache doctor` now runs cleanly on a checkout with **zero local patches**. Full suite: **206/206 tests pass**, 0 collection errors (up from 200/40-modules with 5 collection failures pre-fix). |
| 4.2 (dict-key bug) | Benchmark always printed "0 tool signatures" | **Fixed** | Now reads `tools_recorded` (falling back to the old key). Re-run: correctly prints "20 files (139 tool signatures)". |
| 4.2 (undisclosed baseline) | "Cold Turn" figures were hardcoded constants presented as measurements | **Fixed via disclosure** | Table now labels the column "Est. Upstream Turn ... (Est.)" with an explicit footnote: *"Est. Upstream Turn represents typical remote cloud LLM network roundtrips for comparison. OmniCache Replay columns represent actual locally measured micro-benchmarks."* The baseline still isn't a live measurement, but it is no longer presented as one. |
| 4.3 / 4.4 | `git_status` measured 5.25 ms vs. advertised `<0.3ms`; blanket claim didn't disclose the two policy families | **Fixed via re-scoping** | Re-measured on the new commit: `git_status` median **5.60 ms** (statistically unchanged — the live-subprocess design is intentional and correct, not a bug). README now reads `<0.1ms` for cached reads/scoped queries and `~5ms` when verifying live dirty git state — this now matches measurement. The `extract_candidate_path()` argument-key list was also substantially widened with a case-insensitive normalized-key fallback, addressing the fragility noted in §4.4. Cross-file mutation isolation re-tested: still **0 false-positive leaks**. |
| 4.7 | Quantized embedder: 512 KB claimed vs. 25.2 MB measured (49×) | **Fixed, and the claim is now real** | Weight matrix repacked from nested Python `int` lists into `array.array('b', ...)`. Re-measured with import isolated from construction and `gc.collect()` applied before snapshotting (a stricter protocol than the original measurement, applied identically to both commits for a fair comparison): old commit re-measured at **11.81 MB**, new commit at **728.9 KB** — a **~16.6× reduction**. Of that 728.9 KB, `stats()` now computes the reported 512.0 KB directly from `array.buffer_info()` (the true raw buffer, byte-for-byte), with the residual ~217 KB being ordinary `array.array` Python-object header overhead (2,048 objects × ~99 bytes) — an expected and disclosed-by-nature gap, not a fabricated formula. |
| 4.8 | P2P mesh harness check took ~2,081 ms due to a real timeout against a hardcoded unreachable IP (`10.0.0.42`) | **Fixed** | The harness now mocks `mesh_bus._post_to_peer` for the broadcast check instead of hitting the network. Re-ran 3×: **66–67 ms** (down from ~2,081 ms, a **~31× reduction**), with a peer-cleanup call added to prevent state leakage between runs. |
| 4.6 | "Up to 80%" cascade savings claim; tier-1 economy architecturally unreachable for Claude-origin traffic | **Disclosed, not architecturally changed** | Re-ran the full 85-prompt sweep against the new commit: **routing behavior is bit-for-bit identical** to the original audit — `tier_1_economy` is still 0/85, and the single empirical breakpoint is still at complexity 0.6138. The maintainer chose to correct the *claim* rather than the *routing logic*: README and the module docstring now read "saving up to 73.3% on Anthropic Claude cascades (Sonnet → Haiku) and up to 95% on OpenAI/Gemini cross-vendor cascades" — which matches measurement. Buyers evaluating this for Claude-only traffic should still budget for the ~73% figure, not "up to 80%." |
| 4.10 (partial) | Broad, less-tested subsystem surface (audio, telephony, vision) presented at the same confidence as the tested core | **Addressed** | README now labels the multimodal audio, vision-dedup, and telephony features `[Beta]`, distinguishing them from the more thoroughly-verified caching/cascade/mesh core. |

### 7.3 What remains open

* **The cheapest cost tier is still unreachable for Claude-origin traffic** (§4.6) — this is now honestly labeled rather than fixed, which is an acceptable interim state but worth surfacing explicitly to any buyer whose traffic is Claude-only.
* **The benchmark's upstream baseline is still an estimate, not a live measurement** — now disclosed as such, which resolves the credibility issue, but a genuinely measured cold-turn comparison (§6.3, item 2) would still strengthen the tool's evidentiary claims further.
* Sections 1–6 of this report describe commit `2bafa7a` and are retained as-is for historical accuracy; readers evaluating the project **today** should weight this Section 7 as the current state.

### 7.4 Revised bottom line

Every Tier 0 and Tier 1 item from the original fix-list (release-blocking CLI crash, test-suite collection failures, the two benchmark-honesty bugs, the P2P mesh harness anomaly, and the memory-footprint claim) is confirmed fixed by direct re-measurement, not merely by reading the changelog. This was a substantive, verifiable remediation pass, and it materially changes this report's §6.2 recommendation: the packaging and self-diagnostic trust issues that were the primary blocker to a pilot are resolved as of v3.0.4. The remaining caveat is narrower and specific — the cost-arbiter's cheapest tier is still architecturally unreachable for same-vendor Claude traffic — which is now accurately disclosed rather than hidden, and should inform cost projections rather than block adoption.

*Section 7 reflects measurements taken against `omnicache-proxy` commit `5b0d924` (v3.0.4) under the same sandbox and methodology described in §3.1–3.3.*

---

## 8. License and Security-Documentation Correction (v3.0.5)

Two further commits followed the v3.0.4 remediation, both independently verified against fresh checkouts.

### 8.1 License change

Commit `05b02dc` relicensed the project from the MIT License to the **Functional Source License, Version 1.1, MIT Future License (FSL-1.1-MIT)**, with the copyright holder updated from the generic "OmniCache Team" to a named individual, Rajiv Prasad. Verified by reading the full text of the new `LICENSE` file directly (not summarized from a changelog): the text is the standard, unmodified FSL-1.1-MIT boilerplate, consistent across `LICENSE`, `pyproject.toml`'s `license` field, and the README's license section, with no leftover references to the prior MIT terms or to "open source" anywhere in the README (checked directly).

Substantively, FSL-1.1-MIT grants free use of the software subject to one restriction — the software may not be used to provide a "Competing Use" (a product or service that competes with the software itself) — and automatically converts to plain MIT two years after each release. This is a well-precedented category of license (originated by Sentry, also used by other infrastructure projects) designed specifically to prevent a third party from taking the published source and operating it as a competing hosted service, while preserving free use for individuals, internal organizational deployment, and non-competing integrations. It is not an OSI-recognized "open source" license (the competing-use restriction disqualifies it under the Open Source Definition), which the project's own documentation now avoids claiming.

### 8.2 Security-documentation correction

At the time of the original audit (and unchanged through v3.0.4), `SECURITY.md` stated:

> "At-Rest Persistence: SQLite snapshots support AES-256-GCM envelope encryption."
> "In-Flight Encryption: TLS 1.3 enforced on all external endpoints."

A full-repository search (`AES`, `GCM`, `Fernet`, `cryptography`, `from Crypto`) across every source file found these terms appearing **nowhere except that one documentation file** — no encryption-at-rest implementation exists anywhere in `persistence/snapshot_store.py` or elsewhere in the codebase, and no TLS-termination logic exists in the application layer. This is a materially more serious class of finding than the performance-claim discrepancies in Sections 4–5: it is a specific, written security assurance in the document a security researcher or enterprise buyer would consult first, unsupported by any code.

Commit `9ed8b3b` ("Eliminate phantom encryption claims and ground SECURITY.md in real code") rewrites the relevant section to state accurately that data-at-rest protection relies on host operating-system disk encryption (LUKS/FileVault/BitLocker/Android FBE) rather than application-level envelope encryption, that in-flight transport on the local loopback binding is plain HTTP by design (bind is `127.0.0.1` unless explicitly reconfigured), and that non-loopback deployments must be fronted by an external TLS-terminating reverse proxy. It also replaced a vulnerability-disclosure contact address (`security@omnicache.ai`) that could not be confirmed to resolve to an active, monitored inbox with a verifiable GitHub Security Advisory link and the maintainer's own direct email. Re-reading the corrected file confirms every remaining claim in it now traces to actual, verifiable behavior in the codebase (loopback binding, credential passthrough without persistence, and the `PrivacyShield` HMAC-SHA256 tokenization module, which does exist in `core/privacy_shield.py`).

### 8.3 Assessment

Both changes directly address gaps a real commercial launch or enterprise security review would otherwise surface immediately — the license change closes the competing-use/appropriation risk inherent in the prior MIT terms, and the security-documentation correction removes a false compliance claim before it could be relied upon by a paying customer's security team. Combined with the v3.0.4 remediation in Section 7, this project has now demonstrated three consecutive rounds of fast, accurate, verifiable response to externally-identified issues — a pattern worth more to a rollout decision than the state of any single commit.

*Section 8 reflects the state of `omnicache-proxy` as of commit `9ed8b3b` (v3.0.5), verified via direct file inspection and full-repository search rather than by reading commit messages alone.*
