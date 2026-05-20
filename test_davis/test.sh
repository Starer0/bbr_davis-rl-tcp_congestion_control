#!/bin/bash
# BBR-Davis vs 原生BBR 一键对比测试脚本（修复版+JSON输出）
# 核心：对比bbr_davis和原生bbr，完整采集RTT/延迟/吞吐/重传

# ===================== 前置检查 =====================
#if [ $EUID -ne 0 ]; then
#    echo "⚠️  请用sudo执行：sudo ./bbr_davis_compare.sh"
#    exit 1
#fi

if ! command -v iperf3 &> /dev/null; then
    echo "❌ 安装iperf3..."
    apt update && apt install iperf3 -y
fi

# 新增：检查jq（JSON格式化工具），没有则安装
if ! command -v jq &> /dev/null; then
    echo "❌ 安装jq（JSON处理工具）..."
    apt update && apt install jq -y
fi

# ===================== 配置项 =====================
SERVER_IP="192.168.31.134"  # 你的iperf3服务端IP
SERVER_PORT="5201"          
TEST_DURATION="240"         
RTT_LOG="/tmp/rtt_temp.log" 
RESULT_LOG="/tmp/result.log"
DEBUG_LOG="/tmp/debug.log"
JSON_RESULT="/tmp/bbr_compare_result.json"  # 新增：JSON结果文件路径

# 清空日志文件
> $DEBUG_LOG
> $JSON_RESULT  # 清空JSON文件

# ===================== 核心函数 =====================
# 切换拥塞算法
switch_cc() {
    local cc_alg=$1
    if ! sysctl net.ipv4.tcp_available_congestion_control | grep -q "$cc_alg"; then
        echo "❌ 内核未加载$cc_alg算法！请先加载该算法模块"
        exit 1
    fi
    sysctl -w net.ipv4.tcp_congestion_control=$cc_alg > /dev/null 2>&1
    echo "✅ 已切换拥塞算法为：$cc_alg"
    sleep 3
}

# 获取RTT数据（改进版）
get_rtt_data() {
    local ip=$1
    local port=$2
    local timeout=$3
    
    # 方法1: 使用ss命令（主方法）
    local ss_output=$(timeout $timeout ss -tin dst ${ip}:${port} 2>/dev/null)
    
    if [ -n "$ss_output" ]; then
        echo "$ss_output" | grep -A1 "ESTAB" | grep "rtt:" | head -1 | \
        awk -F'[ =/:,]+' '{
            rtt_avg=""; rtt_min=""; rtt_max=""; rtt_var=""
            for(i=1;i<=NF;i++){
                if($i=="rtt:"){rtt_min=$(i+1); rtt_avg=$(i+3); rtt_max=$(i+5)}
                if($i=="rttvar:"){rtt_var=$(i+1)}
            }
            if(rtt_avg!="") printf "%s,%s,%s,%s", rtt_avg, rtt_min, rtt_max, rtt_var
        }'
        return 0
    fi
    
    # 方法2: 使用/proc/net/tcp（备选方法）
    local hex_ip=$(printf "%02x%02x%02x%02x" $(echo $ip | tr '.' ' '))
    local hex_port=$(printf "%04x" $port)
    
    if grep -q "$hex_ip:$hex_port" /proc/net/tcp 2>/dev/null; then
        local socket_info=$(grep "$hex_ip:$hex_port" /proc/net/tcp | head -1)
        # 这里可以添加更复杂的解析，但通常ss方法更可靠
        echo ""
        return 1
    fi
    
    echo ""
    return 1
}

# 运行测试（修复版）
run_test() {
    local cc_alg=$1
    local test_result="/tmp/${cc_alg}_result.txt"
    local rtt_file="/tmp/${cc_alg}_rtt.csv"
    
    > $test_result
    > $rtt_file
    
    echo "🚀 开始${cc_alg}测试（${TEST_DURATION}秒）..."
    echo "  测试服务器: ${SERVER_IP}:${SERVER_PORT}"
    
    # 启动iperf3测试（后台运行）
    iperf3 -c ${SERVER_IP} -p ${SERVER_PORT} -i 1 -t ${TEST_DURATION} --forceflush > $test_result &
    IPERF_PID=$!
    
    # 等待连接建立（增加等待时间）
    echo "  等待TCP连接建立..."
    sleep 8
    
    # 检查连接是否建立
    if ! ss -t state established dst ${SERVER_IP}:${SERVER_PORT} 2>/dev/null | grep -q ":${SERVER_PORT}"; then
        echo "⚠️  警告：TCP连接可能未建立，继续测试..."
        echo "调试信息：" >> $DEBUG_LOG
        ss -t state established 2>&1 >> $DEBUG_LOG
    fi
    
    # 后台采集RTT数据
    echo "📝 开始采集RTT/延迟数据..."
    (
        local start_time=$(date +%s)
        local end_time=$((start_time + TEST_DURATION + 5))
        
        while [ $(date +%s) -lt $end_time ] && kill -0 $IPERF_PID 2>/dev/null; do
            local rtt_line=$(get_rtt_data ${SERVER_IP} ${SERVER_PORT} 1)
            if [ -n "$rtt_line" ]; then
                echo "$rtt_line" >> $rtt_file
                # 同时输出到RTT_LOG用于实时查看
                echo "$rtt_line" | awk -F',' '{printf "RTT: avg=%sms min=%sms max=%sms var=%sms\n", $1, $2, $3, $4}' >> $DEBUG_LOG
            else
                # 如果获取失败，记录时间戳和状态
                echo "$(date +%s),N/A,N/A,N/A,N/A" >> $rtt_file
            fi
            sleep 0.5
        done
    ) &
    RTT_PID=$!
    
    # 等待iperf3测试完成
    wait $IPERF_PID
    IPERF_EXIT=$?
    
    # 等待RTT采集完成
    sleep 2
    kill $RTT_PID 2>/dev/null || true
    wait $RTT_PID 2>/dev/null
    
    # 解析iperf3结果
    local sender_line=$(grep "sender" $test_result | tail -1)
    local receiver_line=$(grep "receiver" $test_result | tail -1)
    
    # 提取吞吐量（优先使用sender，如果没有则使用receiver）
    if [ -n "$sender_line" ]; then
        local sender_bitrate=$(echo $sender_line | awk '{print $7}')
        local sender_unit=$(echo $sender_line | awk '{print $8}')
        local retr_count=$(echo $sender_line | awk '{print $6}')
        local throughput="${sender_bitrate}${sender_unit}"
    elif [ -n "$receiver_line" ]; then
        local receiver_bitrate=$(echo $receiver_line | awk '{print $7}')
        local receiver_unit=$(echo $receiver_line | awk '{print $8}')
        local throughput="${receiver_bitrate}${receiver_unit}"
        local retr_count="0"
    else
        local throughput="N/A"
        local retr_count="N/A"
    fi
    
    # 解析RTT数据
    if [ -s $rtt_file ]; then
        # 统计有效RTT数据行
        local valid_lines=$(grep -v "N/A" $rtt_file | wc -l)
        
        if [ $valid_lines -gt 0 ]; then
            # 平均RTT
            local rtt_avg=$(awk -F',' '$1!="N/A" && $1!="" {sum+=$1; count++} END {if(count>0) printf "%.2f", sum/count; else print "N/A"}' $rtt_file)
            # 最小RTT
            local rtt_min=$(awk -F',' '$2!="N/A" && $2!="" {if(min=="" || $2<min) min=$2} END {if(min!="") printf "%.2f", min; else print "N/A"}' $rtt_file)
            # 最大RTT
            local rtt_max=$(awk -F',' '$3!="N/A" && $3!="" {if(max=="" || $3>max) max=$3} END {if(max!="") printf "%.2f", max; else print "N/A"}' $rtt_file)
            # RTT抖动（标准差）
            local rtt_var=$(awk -F',' '$4!="N/A" && $4!="" {sum+=$4; count++} END {if(count>0) printf "%.2f", sum/count; else print "N/A"}' $rtt_file)
            
            echo "✅ ${cc_alg}采集到${valid_lines}条RTT数据"
        else
            rtt_avg="N/A"
            rtt_min="N/A"
            rtt_max="N/A"
            rtt_var="N/A"
            echo "⚠️  ${cc_alg}未采集到有效RTT数据"
        fi
    else
        rtt_avg="N/A"
        rtt_min="N/A"
        rtt_max="N/A"
        rtt_var="N/A"
        echo "❌ ${cc_alg}的RTT日志文件为空"
    fi
    
    # 保存结果到总日志
    echo "${cc_alg}|${throughput}|${retr_count}|${rtt_avg}|${rtt_min}|${rtt_max}|${rtt_var}" >> $RESULT_LOG
    
    # 输出本次测试摘要
    echo "✅ ${cc_alg}测试完成！"
    echo "   吞吐: $throughput"
    echo "   重传: $retr_count"
    echo "   平均RTT: ${rtt_avg}ms"
    echo "----------------------------------------"
    
    # 保存详细RTT数据供后续分析
    cp $rtt_file "/tmp/${cc_alg}_detailed_rtt.csv"
}

# ===================== 连接测试 =====================
test_connection() {
    echo "🔍 测试网络连接..."
    
    # 测试ping
    if ping -c 2 -W 1 $SERVER_IP > /dev/null 2>&1; then
        echo "✅ Ping测试通过"
    else
        echo "⚠️  Ping测试失败，但继续测试..."
    fi
    
    # 测试端口连接
    if timeout 3 nc -z $SERVER_IP $SERVER_PORT; then
        echo "✅ 端口测试通过"
        return 0
    else
        echo "❌ 无法连接到iperf3服务器 ${SERVER_IP}:${SERVER_PORT}"
        echo "请确保:"
        echo "1. iperf3服务器正在运行: iperf3 -s"
        echo "2. 防火墙已放行端口: ufw allow 5201/tcp"
        echo "3. IP地址正确"
        return 1
    fi
}

# ===================== 主流程 =====================
echo "========================================"
echo "BBR-Davis vs 原生BBR 对比测试"
echo "========================================"

# 测试网络连接
if ! test_connection; then
    exit 1
fi

# 清空结果文件
> $RESULT_LOG

# 显示当前拥塞算法
current_cc=$(sysctl net.ipv4.tcp_congestion_control | awk '{print $3}')
echo "当前拥塞算法: $current_cc"

# 1. 测试原生BBR
echo ""
echo "阶段1: 测试原生BBR"
switch_cc "bbr"
run_test "bbr"

# 2. 测试BBR-Davis
echo ""
echo "阶段2: 测试BBR-Davis"
switch_cc "bbr_davis"
run_test "bbr_davis"

# 3. 恢复系统默认算法
switch_cc "cubic"
echo "✅ 已恢复拥塞算法为: cubic"

# ===================== 输出对比表格 =====================
echo -e "\n========================================"
echo -e "📊 BBR-Davis vs 原生BBR 对比结果"
echo -e "========================================"
echo -e "算法\t\t吞吐\t\t重传\t平均RTT\t最小RTT\t最大RTT\tRTT抖动"
echo -e "----------------------------------------"

# 读取并格式化结果
while IFS="|" read -r cc_alg throughput retr rtt_avg rtt_min rtt_max rtt_var; do
    # 格式化输出
    if [ "$cc_alg" = "bbr" ]; then
        printf "原生BBR\t\t%-12s\t%-4s\t%-6s\t%-6s\t%-6s\t%-6s\n" \
            "$throughput" "$retr" "${rtt_avg}ms" "${rtt_min}ms" "${rtt_max}ms" "${rtt_var}ms"
        # 暂存原生BBR数据用于JSON
        bbr_throughput=$throughput
        bbr_retr=$retr
        bbr_rtt_avg=$rtt_avg
        bbr_rtt_min=$rtt_min
        bbr_rtt_max=$rtt_max
        bbr_rtt_var=$rtt_var
    elif [ "$cc_alg" = "bbr_davis" ]; then
        printf "BBR-Davis\t%-12s\t%-4s\t%-6s\t%-6s\t%-6s\t%-6s\n" \
            "$throughput" "$retr" "${rtt_avg}ms" "${rtt_min}ms" "${rtt_max}ms" "${rtt_var}ms"
        # 暂存BBR-Davis数据用于JSON
        davis_throughput=$throughput
        davis_retr=$retr
        davis_rtt_avg=$rtt_avg
        davis_rtt_min=$rtt_min
        davis_rtt_max=$rtt_max
        davis_rtt_var=$rtt_var
    fi
done < $RESULT_LOG

echo -e "========================================"
echo -e "💡 核心结论维度："
echo -e "1. 吞吐越高 → 传输效率越好；"
echo -e "2. RTT越小+抖动越低 → 延迟越稳定；"
echo -e "3. 重传次数越少 → 网络适配性越好。"
echo -e "========================================"

# ===================== 生成JSON结果文件（新增核心逻辑） =====================
echo -e "\n📝 生成JSON格式结果文件..."
cat > $JSON_RESULT << EOF
{
    "test_config": {
        "server_ip": "$SERVER_IP",
        "server_port": "$SERVER_PORT",
        "test_duration_seconds": "$TEST_DURATION",
        "test_time": "$(date +'%Y-%m-%d %H:%M:%S')"
    },
    "bbr": {
        "throughput": "$bbr_throughput",
        "retransmission_count": "$bbr_retr",
        "rtt": {
            "average_ms": "$bbr_rtt_avg",
            "min_ms": "$bbr_rtt_min",
            "max_ms": "$bbr_rtt_max",
            "jitter_ms": "$bbr_rtt_var"
        }
    },
    "bbr_davis": {
        "throughput": "$davis_throughput",
        "retransmission_count": "$davis_retr",
        "rtt": {
            "average_ms": "$davis_rtt_avg",
            "min_ms": "$davis_rtt_min",
            "max_ms": "$davis_rtt_max",
            "jitter_ms": "$davis_rtt_var"
        }
    },
    "analysis": {
        "throughput_comparison_tips": "吞吐越高 → 传输效率越好",
        "rtt_comparison_tips": "RTT越小+抖动越低 → 延迟越稳定",
        "retransmission_tips": "重传次数越少 → 网络适配性越好"
    }
}
EOF

# 用jq格式化JSON（确保可读性）
jq . $JSON_RESULT > /tmp/tmp_json && mv /tmp/tmp_json $JSON_RESULT

# ===================== 额外分析 =====================
echo -e "\n📈 额外分析："
echo -e "----------------------------------------"

# 计算性能提升/下降百分比
if [ -s $RESULT_LOG ]; then
    bbr_line=$(grep "^bbr|" $RESULT_LOG)
    davis_line=$(grep "^bbr_davis|" $RESULT_LOG)
    
    if [ -n "$bbr_line" ] && [ -n "$davis_line" ]; then
        IFS="|" read -r cc1 throughput1 retr1 rtt_avg1 rtt_min1 rtt_max1 rtt_var1 <<< "$bbr_line"
        IFS="|" read -r cc2 throughput2 retr2 rtt_avg2 rtt_min2 rtt_max2 rtt_var2 <<< "$davis_line"
        
        # 提取吞吐量数值（去除单位）
        bbr_val=$(echo $throughput1 | grep -oE '[0-9.]+')
        davis_val=$(echo $throughput2 | grep -oE '[0-9.]+')
        
        if [ -n "$bbr_val" ] && [ -n "$davis_val" ] && [ "$bbr_val" != "0" ]; then
            throughput_diff=$(echo "scale=2; ($davis_val - $bbr_val) / $bbr_val * 100" | bc)
            echo -e "吞吐变化: BBR-Davis相比原生BBR ${throughput_diff}%"
            # 将吞吐变化写入JSON
            jq --arg diff "$throughput_diff" '.analysis.throughput_change_percent = $diff' $JSON_RESULT > /tmp/tmp_json && mv /tmp/tmp_json $JSON_RESULT
        fi
        
        # RTT比较
        if [ "$rtt_avg1" != "N/A" ] && [ "$rtt_avg2" != "N/A" ]; then
            rtt_diff=$(echo "scale=2; ($rtt_avg2 - $rtt_avg1)" | bc)
            echo -e "平均RTT变化: ${rtt_diff}ms"
            # 将RTT变化写入JSON
            jq --arg diff "$rtt_diff" '.analysis.average_rtt_change_ms = $diff' $JSON_RESULT > /tmp/tmp_json && mv /tmp/tmp_json $JSON_RESULT
        fi
    fi
fi

echo -e "\n🔍 详细数据已保存到:"
echo -e "   BBR详细RTT数据: /tmp/bbr_detailed_rtt.csv"
echo -e "   BBR-Davis详细RTT数据: /tmp/bbr_davis_detailed_rtt.csv"
echo -e "   调试日志: $DEBUG_LOG"
echo -e "   JSON格式结果: $JSON_RESULT （新增）"  # 提示JSON文件路径

# 清理临时文件（可选）
# echo -e "\n是否清理临时文件? (y/n)"
# read -t 5 answer
# if [ "$answer" = "y" ]; then
#     rm -f /tmp/*_result.txt /tmp/*_rtt.csv /tmp/rtt_temp.log /tmp/result.log
#     echo "临时文件已清理"
# else
#     echo "临时文件保留在/tmp目录"
# fi

echo -e "\n✅ 测试完成！"
