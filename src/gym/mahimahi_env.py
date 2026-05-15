"""
Mahimahi + iperf3 Gym environment for RL-based BBR c2tcp_target tuning.

Each episode sets up a random simulated network via mahimahi (bandwidth, delay,
loss), then the RL agent adjusts c2tcp_target in the kernel module step-by-step.
After each target change, a short iperf3 test runs through the mahimahi link.
The reward is power = throughput / delay — the agent learns to maximize it.

Requires:
  - bbr_davis kernel module loaded (with c2tcp_target module param)
  - mahimahi installed
  - iperf3 installed
  - Root access (for writing sysfs and running mahimahi)
"""

import gym
from gym import spaces
import numpy as np
import subprocess
import json
import os
import tempfile
import random
import time
import signal
import sys

# ---------------------------------------------------------------------------
# Tunable constants
# ---------------------------------------------------------------------------
TARGET_MIN_US  = 30000    # 30 ms  — lower bound for c2tcp_target
TARGET_MAX_US  = 150000   # 150 ms — upper bound for c2tcp_target
TARGET_DEFAULT = 100000   # 100 ms — starting/default value

# Path to the kernel module parameter (adjust if your module name differs)
SYSFS_TARGET_PATH = "/sys/module/bbr_davis/parameters/c2tcp_target_param"

# Observation normalisation scales (rough maxima)
THROUGHPUT_SCALE = 1e8    # 100 Mbps
RTT_SCALE        = 100000 # 100 ms in microseconds
TARGET_SCALE     = 100000 # 100 ms in microseconds

REWARD_SCALE = 0.001

# Mahimahi trace directory
TRACE_DIR = "/tmp/bbr_rl_traces"


def _ensure_trace_dir():
    os.makedirs(TRACE_DIR, exist_ok=True)
    os.chmod(TRACE_DIR, 0o777)


def _generate_trace(bw_mbps, duration_ms=5000):
    """Write a mahimahi trace file with constant bandwidth.

    mm-link trace format: one integer per line = delivery timestamp (ms).
    1 MTU packet = 1500 bytes = 12 Kbit.
    To emulate B Mbps: write B/12 lines per ms of simulated time.
    Timestamp for packet i = floor(i * 12.0 / bw_mbps).

    Returns:
        Path to the trace file.
    """
    _ensure_trace_dir()
    pkts_per_ms = bw_mbps / 12.0
    total_packets = int(duration_ms * pkts_per_ms)
    path = os.path.join(TRACE_DIR, "trace_{}.mah".format(os.getpid()))
    with open(path, "w") as f:
        for i in range(total_packets):
            ts = int(i / pkts_per_ms)
            f.write("{}\n".format(ts))
    os.chmod(path, 0o644)
    return path


def _write_target(target_us):
    """Write c2tcp_target (in microseconds) to the kernel module parameter."""
    try:
        with open(SYSFS_TARGET_PATH, "w") as f:
            f.write(str(int(target_us)))
    except PermissionError:
        print("[WARN] Cannot write to {} — are you root?".format(SYSFS_TARGET_PATH),
              file=sys.stderr)
    except FileNotFoundError:
        print("[WARN] {} not found — is bbr_davis module loaded?".format(
            SYSFS_TARGET_PATH), file=sys.stderr)


def _read_target():
    """Read current c2tcp_target from the kernel module parameter."""
    try:
        with open(SYSFS_TARGET_PATH, "r") as f:
            return int(f.read().strip())
    except Exception:
        return TARGET_DEFAULT


def _iperf_once(uplink_trace, downlink_trace, delay_ms, duration_s, port):
    """Single iperf3 client run inside mahimahi. Returns result tuple or None."""
    nonroot = os.environ.get("SUDO_USER", "nobody")
    cmd = (
        "sudo -u {user} mm-link {ut} {dt} -- "
        "sh -c 'mm-delay {dly} iperf3 -c $MAHIMAHI_BASE -p {port} -t {dur} -J'"
    ).format(
        user=nonroot,
        ut=uplink_trace, dt=downlink_trace,
        dly=int(delay_ms), port=port, dur=int(duration_s),
    )

    timeout_s = int(duration_s) + 15
    try:
        proc = subprocess.run(
            cmd, shell=True,
            capture_output=True, text=True, timeout=timeout_s,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            print("[WARN] iperf3 failed: rc={} stdout={!r} stderr={!r}".format(
                proc.returncode, proc.stdout[:200], proc.stderr[:200]), file=sys.stderr)
            return None
        data = json.loads(proc.stdout)
    except subprocess.TimeoutExpired:
        print("[WARN] iperf3 timed out after {}s".format(timeout_s), file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print("[WARN] iperf3 JSON parse error: {}".format(e), file=sys.stderr)
        return None
    except Exception as e:
        print("[WARN] iperf3 run failed: {}".format(e), file=sys.stderr)
        return None

    try:
        throughput = data["end"]["sum_sent"]["bits_per_second"]
        retransmits = data["end"]["sum_sent"]["retransmits"]
        bytes_sent = data["end"]["sum_sent"]["bytes"]
        stream = data["end"]["streams"][0]["sender"]
        avg_rtt = stream.get("avg_rtt", stream.get("mean_rtt", 0))
        if avg_rtt == 0:
            avg_rtt = stream.get("min_rtt", 5000)
        return throughput, avg_rtt, retransmits, bytes_sent
    except (KeyError, IndexError) as e:
        print("[WARN] iperf3 JSON parse error: {}".format(e), file=sys.stderr)
        return None


def _run_iperf(uplink_trace, downlink_trace, delay_ms, duration_s, port):
    """Run iperf3 with one retry on transient failure (e.g. server busy)."""
    for attempt in range(2):
        result = _iperf_once(uplink_trace, downlink_trace, delay_ms, duration_s, port)
        if result is not None:
            return result
        if attempt == 0:
            time.sleep(0.3)
    return None


class MahimahiEnv(gym.Env):
    """Gym environment that uses mahimahi + iperf3 to evaluate BBR performance.

    Action: delta multiplier on c2tcp_target (1-dim continuous, [-1, 1]).
    Reward: power = throughput / avg_delay  (maximising throughput, minimising delay).
    """

    def __init__(self,
                 history_len=5,
                 iperf_duration=2,
                 iperf_port=5201,
                 steps_per_episode=100):
        super().__init__()

        self.history_len = history_len
        self.iperf_duration = max(1, int(iperf_duration))
        self.iperf_port = int(iperf_port)
        self.max_steps = steps_per_episode

        # Network condition ranges (per episode)
        self.bw_range      = (10, 200)     # Mbit/s
        self.delay_range   = (5, 150)      # ms
        self.loss_range    = (0.0, 2.0)    # percent

        # Current episode state
        self.current_target = TARGET_DEFAULT
        self.steps_taken = 0
        self.episode_reward_sum = 0.0
        self.bw_mbps = 100
        self.delay_ms = 50
        self.loss_pct = 0.0
        self.uplink_trace = None
        self.downlink_trace = None

        # History buffer for observations
        self.history = []
        self.n_features = 4  # throughput, avg_rtt, retransmit_rate, target

        # ------------------------------------------------------------------
        # Gym spaces
        # ------------------------------------------------------------------
        obs_dim = self.n_features * self.history_len
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(obs_dim,), dtype=np.float32
        )
        self.action_space = spaces.Box(
            low=-1.0, high=1.0, shape=(1,), dtype=np.float32
        )

        # Iperf3 server process
        self._server_proc = None

    # ------------------------------------------------------------------
    # Iperf3 server lifecycle
    # ------------------------------------------------------------------
    def _restart_server(self):
        """Start a fresh iperf3 server, killing any previous one first.

        Each iperf3 server instance can only reliably handle one connection
        before entering a "busy" state, so we restart before every test.
        """
        if self._server_proc is not None:
            try:
                self._server_proc.terminate()
                self._server_proc.wait(timeout=3)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                try:
                    self._server_proc.kill()
                except ProcessLookupError:
                    pass
            self._server_proc = None

        # Kill any lingering iperf3 on our port
        subprocess.run(
            ["pkill", "-f", "iperf3.*-p.*{}".format(self.iperf_port)],
            capture_output=True,
        )
        time.sleep(0.1)

        self._server_proc = subprocess.Popen(
            ["iperf3", "-s", "-p", str(self.iperf_port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Poll until the server is actually listening (up to 2 s)
        for _ in range(20):
            time.sleep(0.1)
            result = subprocess.run(
                ["ss", "-tlnp"], capture_output=True, text=True,
            )
            if ":{}".format(self.iperf_port) in result.stdout:
                return
        print("[WARN] iperf3 server did not start listening on port {}".format(
            self.iperf_port), file=sys.stderr)

    def _stop_server(self):
        """Kill the iperf3 server."""
        if self._server_proc is not None:
            try:
                self._server_proc.terminate()
                self._server_proc.wait(timeout=3)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                try:
                    self._server_proc.kill()
                except ProcessLookupError:
                    pass
            self._server_proc = None
        subprocess.run(
            ["pkill", "-f", "iperf3.*-p.*{}".format(self.iperf_port)],
            capture_output=True,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _apply_delta(self, action):
        """Apply delta multiplier action to c2tcp_target and clamp."""
        delta = float(action[0])
        if delta >= 0.0:
            new_target = self.current_target * (1.0 + delta)
        else:
            new_target = self.current_target / (1.0 - delta)
        self.current_target = float(np.clip(new_target, TARGET_MIN_US, TARGET_MAX_US))
        _write_target(self.current_target)
        return self.current_target

    def _build_obs(self, throughput, avg_rtt, retransmits, bytes_sent):
        """Build a normalised observation vector from raw metrics."""
        retrans_rate = retransmits / max(bytes_sent / 1500.0, 1.0)
        retrans_rate = np.clip(retrans_rate, 0.0, 1.0)

        feats = np.array([
            throughput / THROUGHPUT_SCALE,
            avg_rtt / RTT_SCALE,
            retrans_rate,
            self.current_target / TARGET_SCALE,
        ], dtype=np.float32)

        self.history.append(feats)
        while len(self.history) > self.history_len:
            self.history.pop(0)
        if len(self.history) < self.history_len:
            pad = [np.zeros(self.n_features, dtype=np.float32)] * (
                self.history_len - len(self.history))
            padded = pad + list(self.history)
        else:
            padded = list(self.history)
        return np.concatenate(padded)

    # ------------------------------------------------------------------
    # Gym API
    # ------------------------------------------------------------------
    def reset(self):
        self._restart_server()

        # Randomize network conditions for this episode
        self.bw_mbps   = random.uniform(*self.bw_range)
        self.delay_ms  = random.uniform(*self.delay_range)
        self.loss_pct  = random.uniform(*self.loss_range)

        # Generate mahimahi traces (uplink/downlink identical for now)
        _ensure_trace_dir()
        self.uplink_trace   = _generate_trace(self.bw_mbps)
        self.downlink_trace = _generate_trace(self.bw_mbps)

        # Reset target to default
        self.current_target = float(TARGET_DEFAULT)
        _write_target(self.current_target)

        # Clear history
        self.history = []
        self.steps_taken = 0
        self.episode_reward_sum = 0.0

        print("[Episode] bw={:.1f}Mbps delay={:.1f}ms loss={:.2f}%".format(
            self.bw_mbps, self.delay_ms, self.loss_pct))

        # Run one initial iperf test to seed observation
        result = _run_iperf(self.uplink_trace, self.downlink_trace,
                            self.delay_ms, self.iperf_duration, self.iperf_port)
        if result is None:
            # Fallback observation if iperf failed
            obs = self._build_obs(0.0, TARGET_DEFAULT, 0, 1)
        else:
            tp, rtt, retr, sent = result
            obs = self._build_obs(tp, rtt, retr, sent)

        return obs

    def step(self, action):
        # Apply action
        self._apply_delta(action)

        # Restart server before each test (avoids "server busy" errors)
        self._restart_server()

        # Run iperf3 through mahimahi
        result = _run_iperf(self.uplink_trace, self.downlink_trace,
                            self.delay_ms, self.iperf_duration, self.iperf_port)

        if result is None:
            # iperf failed — return negative reward, same observation
            reward = -1.0
            padded = [np.zeros(self.n_features, dtype=np.float32)] * self.history_len
            obs = np.concatenate(padded)
        else:
            throughput, avg_rtt, retransmits, bytes_sent = result
            # POWER = throughput / delay  (the quantity to maximise)
            raw_power = throughput / max(avg_rtt, 1.0)
            reward = raw_power * REWARD_SCALE
            obs = self._build_obs(throughput, avg_rtt, retransmits, bytes_sent)

        self.steps_taken += 1
        self.episode_reward_sum += reward
        done = (self.steps_taken >= self.max_steps)

        info = {
            "target_us": self.current_target,
            "bw_mbps": self.bw_mbps,
            "delay_ms": self.delay_ms,
        }

        if done:
            info["episode_reward_sum"] = self.episode_reward_sum

        return obs, reward, done, info

    def render(self, mode="human"):
        pass

    def close(self):
        self._stop_server()
        # Clean up trace files
        for f in [self.uplink_trace, self.downlink_trace]:
            if f and os.path.exists(f):
                os.remove(f)
