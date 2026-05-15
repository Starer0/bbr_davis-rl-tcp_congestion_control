# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a research project combining two components:

1. **BBR/** — A modified Linux kernel TCP congestion control module (`bbr_davis`) based on Google's BBR, with added C2TCP-inspired network condition detection and power optimization (throughput/delay ratio tuning).
2. **src/** — A Python reinforcement learning framework (OpenAI Gym + Stable Baselines + PPO1) for training RL-based congestion control agents in simulation or online against real networks.

## Build & Run

### Kernel module

```bash
cd BBR && make          # build bbr_davis.ko
sudo make install       # install and load module
sudo make uninstall     # unload and remove module
make clean              # clean build artifacts
```

The module registers as `bbr_davis` and once loaded can be selected via `sysctl` or per-socket setsockopt. Requires kernel headers/build at `/lib/modules/$(uname -r)/build`.

### Python RL training

No package manager or requirements.txt. Manual dependencies on:
- `gym`, `numpy`, `tensorflow` (1.x, `tf.saved_model`)
- `stable_baselines` (legacy, the `stable_baselines` package, **not** `stable-baselines3`)

Simulation training:
```bash
cd src/gym && python stable_solve.py --arch=32,16 --gamma=0.99
```

Online training (requires companion `PCC-Uspace.git` repo, `deep-learning` branch):
```bash
cd src/gym/online && python shim_solver.py --arch=32,16 --model-dir=/tmp/pcc_saved_models/model_A/
```

Both accept `--history-len`, `--input-features`, `--gamma`, `--delta-scale` as CLI args (format: `--key=value` or `--key` for boolean true).

## Architecture

### Kernel module (`BBR/bbr_davis.c`, ~1470 lines)

Standard BBR engine (modes: STARTUP → DRAIN → PROBE_BW ↔ PROBE_RTT) with three custom additions layered on top:

1. **Congestion-aware pacing gain adjustment** in `bbr_update_gains()` (line ~1015): In PROBE_BW mode, the pacing gain during cycle phases 0 (probe) and 1 (drain) is dynamically adjusted based on a `congestion_factor` computed as `(inflight - BDP) / (bw * min_rtt)`. Probe phase ranges from 1.0–2.0×, drain phase 0.5–1.0×.

2. **C2TCP condition detector/enforcer** (lines ~1258–1361): Monitors per-ACK RTT against a dynamic `setpoint` (alpha × min_rtt_global). Three states — GOOD (RTT below setpoint: add extra cwnd), NORMAL (above setpoint with grace period), BAD (exceeded grace period: collapse cwnd to 1). The detection interval shrinks as `sqrt(N)` on consecutive BAD events.

3. **Power-optimization tuner** (lines ~1363–1436): Every 500ms, computes `power = throughput / avg_delay` and adjusts `c2tcp_target` (target delay, range 30–150ms) bidirectionally to maximize this ratio. Uses a step-size adaptation that shrinks near the best power and grows when far from it. If power drops >5% below best, resets target to the historically best value. The dynamic `c2tcp_alpha` parameter (1.0–3.0) drives the threshold setpoint.

### Python RL framework (`src/`)

```
src/
├── common/
│   ├── simple_arg_parse.py   # CLI arg parsing (--key=value or --key)
│   ├── config.py             # Single config: DELTA_SCALE (step size for rate changes)
│   └── sender_obs.py         # Observation/feature definitions for RL state space
├── gym/
│   ├── network_sim.py        # SimulatedNetworkEnv (gym env "PccNs-v0")
│   ├── stable_solve.py       # Training script for simulated env
│   ├── BBR/                  # Test harness calling runer.so via ctypes
│   └── online/
│       ├── shim_env.py       # ShimNetworkEnv (gym env "NetShim-v0") — TCP socket bridge
│       └── shim_solver.py    # Training script for online env
```

**Simulation path**: `SimulatedNetworkEnv` creates a network with bottleneck links (random bw/latency/queue/loss), a `Sender` agent, and steps through discrete event simulation. At each step, it calls into a C shared library (`runer.so`, loaded via `ctypes.CDLL`) that performs a BBR calculation and returns network metrics. The action space is a single continuous value (rate delta multiplier), and the reward is `throughput - 2e3 * latency`.

**Online path**: `ShimNetworkEnv` listens on TCP port 9787 for a `pccclient` from the `PCC-Uspace` repo. Each step: send current rate → receive observation data → feed through RL policy. Same action/observation semantics as the simulation env.

**RL**: Uses PPO1 from `stable_baselines` with configurable MLP policy architecture. Both training scripts save models as TensorFlow SavedModel via `tf.saved_model`.

**Observation features** (defined in `sender_obs.py`): send rate, recv rate, recv duration, avg latency, loss ratio, ACK latency inflation, sent latency inflation, conn min latency, latency ratio, send ratio. The `SenderHistory` class stacks `history_len` intervals into the observation vector.

### Key connection: `runer.so`

Both the simulation env (`network_sim.py`) and the BBR test script (`src/gym/BBR/test.py`) load a shared library `./BBR/runer.so` via `ctypes.CDLL` and call its `run()` function with a string-encoded beta parameter and an int array buffer to receive computed metrics. This `.so` is the compiled bridge between the Python RL framework and the BBR logic.

# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
