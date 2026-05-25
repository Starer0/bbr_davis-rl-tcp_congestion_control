#!/bin/bash
# run_rl_experiment.sh — run mm-tcp experiment with RL-controlled c2tcp_target
#
# Usage (NO sudo):
#   ./run_rl_experiment.sh ATT-LTE-driving.down ATT-LTE-driving.up bbr_davis 50001 20 0 droptail 300 mit 750
#
# The script sudos only the RL controller (needs root for sysfs).
# mm-tcp itself runs as your normal user (mm-delay refuses root).

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
GYM_DIR="$REPO_DIR/src/gym"

DATA_FILE="/tmp/iperf3_rl_fifo"
MODEL="$GYM_DIR/bbr_rl_models/bbr_target_model_final.zip"

if [ "$#" -ne 10 ]; then
    echo "Usage: $0 DOWNLINK UPLINK LOGFILE PORT RTT LOSS QUEUE_ALG BUFFER TRACESET DURATION"
    echo "Example: $0 ATT-LTE-driving.down ATT-LTE-driving.up bbr_davis 50001 20 0 droptail 300 mit 750"
    exit 1
fi

if [ ! -f "$MODEL" ]; then
    echo "Model not found: $MODEL"
    exit 1
fi

# Ensure sudo timestamp is fresh (avoids background sudo hanging on password prompt)
sudo -v

# Restore default target on Ctrl+C or error
cleanup_target() {
    echo 100000 | sudo tee /sys/module/bbr_davis/parameters/c2tcp_target_param > /dev/null 2>&1
    echo "[Wrapper] Restored c2tcp_target to default"
}
trap cleanup_target EXIT

# Clean up data fifo from previous run (created by sudo'd RL controller)
sudo rm -f "$DATA_FILE"

echo "=========================================="
echo "RL-Controlled BBR-Davis Experiment"
echo "=========================================="
echo "  Model:   $MODEL"
echo "  Data:    $DATA_FILE"
echo "  Trace:   $1 / $2"
echo "  CC:      $3  Port: $4  RTT: $5 ms  Loss: $6 %"
echo "  Queue:   $7 ($8 pkts)  TraceSet: $9  Duration: ${10}s"
echo "=========================================="

# Kill any leftover RL controller from a previous run
sudo pkill -f rl_controller.py 2>/dev/null || true
sudo rm -f /tmp/rl_pid /tmp/rl_ready

# Log directory
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
TRACE_NAME="$(basename "$1" .down)"

# Start RL controller as root (needs sysfs), in background
echo "[Wrapper] Starting RL controller (sudo, TF takes ~10s to load)..."
sudo -E env PYTHONPATH="$GYM_DIR:$SCRIPT_DIR:$PYTHONPATH" \
    python3 "$SCRIPT_DIR/rl_controller.py" \
    --model="$MODEL" \
    --file="$DATA_FILE" \
    --port="$4" \
    --trace-name="$TRACE_NAME" \
    --log-dir="$LOG_DIR" &
RL_PID=$!

# Wait for controller to signal it's loaded and ready
echo "[Wrapper] Waiting for RL controller to be ready (TF model load takes ~10s)..."
READY=0
for i in $(seq 1 30); do
    sleep 1
    if [ -f /tmp/rl_ready ]; then
        echo "[Wrapper] RL controller ready"
        sudo rm -f /tmp/rl_ready
        READY=1
        break
    fi
done

if [ $READY -eq 0 ]; then
    echo "[ERROR] RL controller did not become ready within 30s"
    echo "Check if the model path is correct: $MODEL"
    sudo rm -f "$DATA_FILE"
    exit 1
fi

echo "[Wrapper] Starting mm-tcp experiment..."
echo "------------------------------------------"

# Run mm-tcp-rl as normal user (mm-delay requires non-root)
"$SCRIPT_DIR/mm-tcp-rl" "$@"

echo "------------------------------------------"
echo "[Wrapper] Experiment finished."
sleep 2

# Stop RL controller
sudo kill $RL_PID 2>/dev/null || true
wait $RL_PID 2>/dev/null || true

# Restore default target so subsequent non-RL experiments are not affected
echo 100000 | sudo tee /sys/module/bbr_davis/parameters/c2tcp_target_param > /dev/null
echo "[Wrapper] Restored c2tcp_target to 100000 us (default)"

sudo rm -f "$DATA_FILE"
echo "[Wrapper] Done."
