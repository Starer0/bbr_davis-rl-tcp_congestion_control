#!/usr/bin/env python3
"""
RL Controller: reads iperf3 text output from a FIFO, runs the PPO model,
and writes c2tcp_target to sysfs. Gets RTT from kernel via ss -i.

Usage (as root, for sysfs write):
    sudo -E python3 rl_controller.py --model=../src/gym/bbr_rl_models/bbr_target_model_final.zip --port=50001
"""

import sys
import os
import re
import time
import subprocess
import numpy as np

_current = os.path.dirname(os.path.abspath(__file__))
_parent = os.path.dirname(_current)
_src = os.path.join(_parent, "src")
for p in (_current, _parent, _src):
    if p not in sys.path:
        sys.path.insert(0, p)

from stable_baselines3 import PPO

from common.simple_arg_parse import arg_or_default

# ---------------------------------------------------------------------------
# CLI args
# ---------------------------------------------------------------------------
MODEL_PATH    = arg_or_default("--model", default="bbr_rl_models/bbr_target_model_final.zip")
DATA_FILE     = arg_or_default("--file", default="/tmp/iperf3_rl_fifo")
PORT          = arg_or_default("--port", default=50001)
HISTORY_LEN   = arg_or_default("--history-len", default=5)
DETERMINISTIC = arg_or_default("--deterministic", default=1)
TRACE_NAME    = arg_or_default("--trace-name", default="experiment")
LOG_DIR       = arg_or_default("--log-dir", default=".")

# ---------------------------------------------------------------------------
# Scales (must match mahimahi_env.py)
# ---------------------------------------------------------------------------
THROUGHPUT_SCALE = 2e8
RTT_SCALE        = 1000000
TARGET_SCALE     = 100000

TARGET_MIN_US = 30000
TARGET_MAX_US = 150000
TARGET_DEFAULT = 100000
WRITE_THRESHOLD = 0.01  # only write sysfs if target changes by > 1%

SYSFS_TARGET_PATH = "/sys/module/bbr_davis/parameters/c2tcp_target_param"

# ---------------------------------------------------------------------------
# Sysfs
# ---------------------------------------------------------------------------
def read_target():
    try:
        with open(SYSFS_TARGET_PATH, "r") as f:
            return int(f.read().strip())
    except Exception:
        return TARGET_DEFAULT

def write_target(target_us):
    target_us = int(np.clip(target_us, TARGET_MIN_US, TARGET_MAX_US))
    try:
        with open(SYSFS_TARGET_PATH, "w") as f:
            f.write(str(target_us))
    except Exception as e:
        print("[WARN] Cannot write target: {}".format(e), file=sys.stderr)
    return target_us

# ---------------------------------------------------------------------------
# Parse iperf3 text output and kernel RTT
# ---------------------------------------------------------------------------

# iperf3 text line: [  5]   0.00-1.00   sec  1.23 MBytes  10.3 Mbits/sec    0
IPERF_RE = re.compile(
    r'\[\s*\d+\]\s+[\d.]+-[\d.]+\s+sec\s+'
    r'([\d.]+)\s+(\w+)\s+'          # transfer amount + unit
    r'([\d.]+)\s+(\w+)/sec\s+'      # bitrate amount + unit
    r'(\d+)'                         # retransmits
)

UNIT_MULT = {"Bytes": 1, "KBytes": 1000, "MBytes": 1000000, "GBytes": 1000000000,
             "bits": 1, "Kbits": 1000, "Mbits": 1000000, "Gbits": 1000000000}

def parse_iperf_line(line):
    """Parse one iperf3 text output line. Returns (tp_bps, retransmits, bytes_sent) or None."""
    m = IPERF_RE.search(line)
    if not m:
        return None
    xfer_amt = float(m.group(1))
    xfer_unit = m.group(2)
    rate_amt = float(m.group(3))
    rate_unit = m.group(4)
    retransmits = int(m.group(5))
    xfer_mult = UNIT_MULT.get(xfer_unit)
    rate_mult = UNIT_MULT.get(rate_unit)
    if xfer_mult is None or rate_mult is None:
        return None
    throughput = rate_amt * rate_mult  # bits per second
    bytes_sent = xfer_amt * xfer_mult   # bytes
    return throughput, retransmits, bytes_sent

def get_rtt_from_ss(port):
    """Get smoothed RTT in microseconds for the TCP connection on the given port."""
    try:
        out = subprocess.check_output(
            ["ss", "-tipn", "state", "established", "sport", "= :{}".format(port)],
            stderr=subprocess.DEVNULL, timeout=2
        ).decode()
        m = re.search(r'rtt:([\d.]+)/', out)
        if m:
            # ss reports RTT in milliseconds; convert to microseconds for consistency
            return float(m.group(1)) * 1000.0
    except Exception:
        pass
    return 5000.0  # default fallback: 5ms

# ---------------------------------------------------------------------------
# Observation buffer (same as training)
# ---------------------------------------------------------------------------
class ObsBuffer:
    def __init__(self, history_len, n_features=4):
        self.history_len = history_len
        self.n_features = n_features
        self.history = []

    def build(self, throughput, avg_rtt, retransmits, bytes_sent, current_target):
        retrans_rate = retransmits / max(bytes_sent / 1500.0, 1.0)
        retrans_rate = np.clip(retrans_rate, 0.0, 1.0)
        feats = np.array([
            throughput / THROUGHPUT_SCALE,
            avg_rtt / RTT_SCALE,
            retrans_rate,
            current_target / TARGET_SCALE,
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

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("[RL Ctrl] Loading model: {}".format(MODEL_PATH))
    model = PPO.load(MODEL_PATH)

    # Set up logging
    os.makedirs(LOG_DIR, exist_ok=True)
    start_ts = time.time()
    start_time_str = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(start_ts))
    log_path = os.path.join(LOG_DIR, "{}_{}.csv".format(TRACE_NAME, start_time_str))
    log_f = open(log_path, "w")
    log_f.write("step,time_s,tp_bps,tp_mbps,rtt_us,rtt_ms,retransmits,bytes_sent,retrans_rate,old_target_us,delta,new_target_us,obs_tp,obs_rtt,obs_retrans,obs_target\n")
    print("[RL Ctrl] Logging to {}".format(log_path))

    current_target = read_target()
    if current_target == 0:
        current_target = TARGET_DEFAULT
        write_target(current_target)

    obs_buf = ObsBuffer(HISTORY_LEN)
    step = 0

    # Set up fifo: remove stale one, create new
    if os.path.exists(DATA_FILE):
        os.remove(DATA_FILE)
    os.mkfifo(DATA_FILE)
    os.chmod(DATA_FILE, 0o666)

    # Write PID for cleanup
    with open("/tmp/rl_pid", "w") as f:
        f.write(str(os.getpid()))

    # Signal wrapper that we're loaded and ready
    with open("/tmp/rl_ready", "w") as f:
        f.write("ready")

    print("[RL Ctrl] Activated — model loaded, ready to control c2tcp_target")
    print("[RL Ctrl] Waiting for iperf3 to connect...")

    # Open fifo for reading — blocks here until iperf3 opens it for writing
    with open(DATA_FILE, "r") as f:
        print("[RL Ctrl] iperf3 connected, RL control loop running")

        for line in f:
            parsed = parse_iperf_line(line)
            if parsed is None:
                continue

            tp, retr, sent = parsed
            rtt = get_rtt_from_ss(PORT)

            obs = obs_buf.build(tp, rtt, retr, sent, current_target)
            action, _states = model.predict(obs, deterministic=bool(DETERMINISTIC))

            delta = float(action[0])
            old_target = current_target
            if delta >= 0.0:
                new_target = old_target * (1.0 + delta)
            else:
                new_target = old_target / (1.0 - delta)
            new_target = int(np.clip(new_target, TARGET_MIN_US, TARGET_MAX_US))

            # Only write sysfs if target actually changed by > 1%
            if abs(new_target - old_target) / float(old_target) > WRITE_THRESHOLD:
                current_target = write_target(new_target)
            else:
                current_target = old_target

            # Log this step
            retrans_rate = np.clip(retr / max(sent / 1500.0, 1.0), 0.0, 1.0)
            elapsed = time.time() - start_ts
            log_f.write("{},{:.3f},{:.0f},{:.3f},{:.1f},{:.3f},{},{},{:.6f},{:.1f},{:.6f},{:.1f},{:.6f},{:.6f},{:.6f},{:.6f}\n".format(
                step, elapsed,
                tp, tp / 1e6,
                rtt, rtt / 1000.0,
                retr, sent,
                retrans_rate,
                old_target, delta, current_target,
                tp / THROUGHPUT_SCALE,
                rtt / RTT_SCALE,
                retrans_rate,
                old_target / TARGET_SCALE))
            log_f.flush()

            step += 1

    # iperf3 closed its end of the fifo — experiment is done
    os.remove(DATA_FILE)
    log_f.close()
    print("[RL Ctrl] Done — {} steps, log saved to {}".format(step, log_path))

if __name__ == "__main__":
    main()
