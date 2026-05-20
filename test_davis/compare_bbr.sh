#!/bin/bash
#set -x # Enable logging of executed commands.

########################### Configuration ###########################
# Test duration in seconds
DURATION=30
# Server port
PORT=5201
# Network conditions
RTT_VALUES=(10) # ms - 先测试一个值
LOSS_RATES=(0) # packet loss rate - 先测试一个值
# Trace set (using MIT traces as default)
TRACESET="mit"
# Downlink and uplink trace files (using first MIT trace as default)
DOWNLINK="ATT-LTE-driving.down"
UPLINK="ATT-LTE-driving.up"
# Current user (for running mahimahi as non-root)
CURRENT_USER=$(whoami)

########################### Setup Directories ###########################
echo "Setting up directories..."
if [ -d "results" ]; then
    rm -rf results
    mkdir results
else
    mkdir results
fi

# Ensure results directory is writable by non-root user
chmod 777 results

########################### Functions ###########################
function run_as_user() {
    # Run command as non-root user
    local cmd="$@"
    if [ "$CURRENT_USER" = "root" ]; then
        # If we're root, find the actual user
        REAL_USER=$(logname 2>/dev/null || echo $SUDO_USER)
        if [ -z "$REAL_USER" ]; then
            REAL_USER="ubuntu" # Fallback
        fi
        echo "Running as user: $REAL_USER"
        su -c "$cmd" "$REAL_USER"
    else
        # Already non-root, run directly
        echo "Running as user: $CURRENT_USER"
        eval "$cmd"
    fi
}

function check_dependencies() {
    echo "Checking dependencies..."
    
    # Check for mahimahi
    if ! command -v mm-delay &> /dev/null; then
        echo "ERROR: mahimahi is not installed or not in PATH"
        return 1
    fi
    
    # Check for iperf3
    if ! command -v iperf3 &> /dev/null; then
        echo "ERROR: iperf3 is not installed or not in PATH"
        return 1
    fi
    
    # Check for trace files
    if [ ! -f "traces/${TRACESET}/${DOWNLINK}" ]; then
        echo "ERROR: Downlink trace file not found: traces/${TRACESET}/${DOWNLINK}"
        return 1
    fi
    
    if [ ! -f "traces/${TRACESET}/${UPLINK}" ]; then
        echo "ERROR: Uplink trace file not found: traces/${TRACESET}/${UPLINK}"
        return 1
    fi
    
    echo "All dependencies checked successfully!"
    return 0
}

function run_test() {
    local protocol=$1
    local rtt=$2
    local loss_rate=$3
    local test_id="${protocol}-rtt${rtt}-loss${loss_rate}"
    
    echo "===================================="
    echo "Testing ${protocol} with RTT=${rtt}ms, Loss=${loss_rate}"
    echo "===================================="
    
    # Set congestion control algorithm (requires root)
    echo "Setting congestion control to ${protocol}..."
    sudo sysctl -w net.ipv4.tcp_congestion_control=${protocol}
    
    # Verify the change
    current_cc=$(sysctl -n net.ipv4.tcp_congestion_control)
    echo "Current congestion control: ${current_cc}"
    
    # Calculate link delay (half of RTT)
    link_delay=$((rtt / 2))
    echo "Calculated link delay: ${link_delay}ms"
    
    # Check if port is available
    if lsof -Pi :${PORT} -sTCP:LISTEN -t >/dev/null ; then
        echo "WARNING: Port ${PORT} is already in use, killing existing process..."
        sudo fuser -k ${PORT}/tcp 2>/dev/null
        sleep 2
    fi
    
    # Start iperf3 server in background (can run as root)
    echo "Starting iperf3 server on port ${PORT}..."
    iperf3 -s -i 0 -p ${PORT} > /dev/null 2>&1 &
    SERVER_PID=$!
    sleep 3 # Give server more time to start
    
    # Verify server is running
    if ! ps -p ${SERVER_PID} > /dev/null; then
        echo "ERROR: iperf3 server failed to start"
        return 1
    fi
    echo "iperf3 server started with PID: ${SERVER_PID}"
    
    # Run the test with Mahimahi - as non-root user
    LOG_FILE="results/${test_id}"
    echo "Test results will be saved to: ${LOG_FILE}*"
    
    # 方案: 使用非root用户运行Mahimahi
    echo "Running Mahimahi test as non-root user..."
    
    # 构建测试命令
    TEST_COMMAND="
        echo '=== Running Mahimahi test for ${protocol} ===' && \
        echo 'Current user inside test: \$(whoami)' && \
        # Test 1: Basic connectivity
        echo '--- Test 1: Basic mm-link ---' && \
        mm-link traces/${TRACESET}/${DOWNLINK} traces/${TRACESET}/${UPLINK} -- sh -c '
            echo \"Inside Mahimahi network\" && \
            echo \"MAHIMAHI_BASE: \$MAHIMAHI_BASE\" && \
            iperf3 -c \$MAHIMAHI_BASE -p ${PORT} -t ${DURATION} -i 5
        ' && \
        # Test 2: With delay and loss
        echo '--- Test 2: With delay and loss ---' && \
        mm-delay ${link_delay} mm-loss uplink ${loss_rate} mm-link traces/${TRACESET}/${DOWNLINK} traces/${TRACESET}/${UPLINK} --uplink-log=${LOG_FILE}-up --downlink-log=${LOG_FILE}-down -- sh -c '
            iperf3 -c \$MAHIMAHI_BASE -p ${PORT} -t ${DURATION} -i 5 -J
        '
    "
    
    # 执行测试命令
    run_as_user "$TEST_COMMAND" > "${LOG_FILE}-output.txt" 2>"${LOG_FILE}-error.log"
    
    if [ $? -eq 0 ]; then
        echo "Mahimahi test succeeded!"
    else
        echo "Mahimahi test failed, checking logs..."
        cat "${LOG_FILE}-error.log"
    fi
    
    # Kill the server
    echo "Stopping iperf3 server..."
    kill -9 ${SERVER_PID} 2>/dev/null
    wait ${SERVER_PID} 2>/dev/null
    
    # Analyze the results
    echo "Analyzing results..."
    
    # 检查是否生成了日志文件
    if [ -f "${LOG_FILE}-up" ]; then
        echo "Found uplink log file: ${LOG_FILE}-up"
        # 检查 mm-metric 是否存在并可执行
        if [ -x "mm-metric" ]; then
            echo "Running mm-metric analysis..."
            # 以非root用户运行 mm-metric
            run_as_user "./mm-metric 500 ${LOG_FILE}-up" > "${LOG_FILE}-analysis.txt" 2>&1
            if [ $? -eq 0 ]; then
                echo "Analysis completed successfully"
                # 显示分析结果
                cat "${LOG_FILE}-analysis.txt"
            else
                echo "WARNING: mm-metric analysis failed"
            fi
        else
            echo "WARNING: mm-metric not found or not executable"
        fi
    else
        echo "ERROR: Uplink log file not found: ${LOG_FILE}-up"
        # 检查是否有其他输出
        if [ -f "${LOG_FILE}-output.txt" ]; then
            echo "Showing test output:"
            cat "${LOG_FILE}-output.txt"
        fi
    fi
    
    echo "Test completed for ${protocol} with RTT=${rtt}ms, Loss=${loss_rate}"
    echo "Results saved to ${LOG_FILE}*"
    echo ""
}

function compare_results() {
    echo "===================================="
    echo "Comparing BBR and BBR-Davis Results"
    echo "===================================="
    
    # Create comparison summary
    echo "Protocol,RTT (ms),Loss Rate,Test Status" > results/comparison.csv
    
    for rtt in "${RTT_VALUES[@]}"; do
        for loss_rate in "${LOSS_RATES[@]}"; do
            for protocol in "bbr" "bbr_davis"; do
                log_file="results/${protocol}-rtt${rtt}-loss${loss_rate}-output.txt"
                if [ -f "${log_file}" ]; then
                    status="Completed"
                else
                    status="Failed"
                fi
                echo "${protocol},${rtt},${loss_rate},${status}" >> results/comparison.csv
            done
        done
    done
    
    echo "Comparison completed. Results saved to results/comparison.csv"
    echo ""
    
    # Show comparison summary
    for rtt in "${RTT_VALUES[@]}"; do
        for loss_rate in "${LOSS_RATES[@]}"; do
            echo "RTT: ${rtt}ms, Loss: ${loss_rate}"
            
            # Check BBR results
            bbr_file="results/bbr-rtt${rtt}-loss${loss_rate}-output.txt"
            if [ -f "${bbr_file}" ]; then
                echo "  BBR: Test completed (check ${bbr_file} for details)"
            else
                echo "  BBR: Test failed"
            fi
            
            # Check BBR-Davis results
            bbr_davis_file="results/bbr_davis-rtt${rtt}-loss${loss_rate}-output.txt"
            if [ -f "${bbr_davis_file}" ]; then
                echo "  BBR-Davis: Test completed (check ${bbr_davis_file} for details)"
            else
                echo "  BBR-Davis: Test failed"
            fi
            
            echo ""
        done
    done
}

########################### Main Script ###########################
echo "Starting BBR vs BBR-Davis comparison test"
echo "=================================================="
echo "Current user: $CURRENT_USER"

# Check dependencies first
echo "Checking dependencies..."
if ! command -v mm-link &> /dev/null; then
    echo "ERROR: mahimahi is not installed or not in PATH"
    exit 1
fi

if ! command -v iperf3 &> /dev/null; then
    echo "ERROR: iperf3 is not installed or not in PATH"
    exit 1
fi

if [ ! -f "traces/${TRACESET}/${DOWNLINK}" ]; then
    echo "ERROR: Downlink trace file not found: traces/${TRACESET}/${DOWNLINK}"
    exit 1
fi

if [ ! -f "traces/${TRACESET}/${UPLINK}" ]; then
    echo "ERROR: Uplink trace file not found: traces/${TRACESET}/${UPLINK}"
    exit 1
fi

echo "All dependencies checked successfully!"

# Test both protocols under different network conditions
for rtt in "${RTT_VALUES[@]}"; do
    for loss_rate in "${LOSS_RATES[@]}"; do
        # Test default BBR
        run_test "bbr" "${rtt}" "${loss_rate}"
        
        # Test BBR-Davis
        run_test "bbr_davis" "${rtt}" "${loss_rate}"
    done
done

# Compare results
compare_results

echo "=================================================="
echo "Test completed!"
echo "Detailed results are available in the 'results' directory."
echo "Check the log files for detailed information about test execution."
