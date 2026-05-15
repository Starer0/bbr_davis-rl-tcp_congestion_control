# BBR c2tcp_target RL Tuning (Ubuntu 20.04)

本项目使用强化学习（PPO）在真实网络模拟环境（mahimahi + iperf3）中自动调优
bbr_davis 内核拥塞控制模块的 `c2tcp_target` 参数，目标是最大化 **Power = 吞吐量 / 时延**。

## 目录结构

```
BBR/                          # 内核模块
├── bbr_davis.c               # 改良版 BBR 拥塞控制算法 (含 C2TCP + Power 优化)
├── bbr_davis.ko              # 编译好的内核模块
└── Makefile                  # 内核模块构建
src/
├── common/                   # 参数解析、观测定义等公共工具 (沿用原项目)
├── gym/
│   ├── mahimahi_env.py       # [新] Mahimahi + iperf3 Gym 环境
│   ├── train_mahimahi.py     # [新] PPO 训练脚本 (stable-baselines3)
│   ├── network_sim.py        # [旧] 仿真环境 (保留作参考)
│   ├── stable_solve.py       # [旧] 仿真训练 (保留作参考)
│   └── online/               # [旧] 在线训练 (PCC-Uspace, 保留作参考)
└── __init__.py
```

## 与原项目的关键区别

| 项目 | 原项目 (Ubuntu 18.04) | 新项目 (Ubuntu 20.04) |
|------|----------------------|----------------------|
| RL 框架 | stable_baselines (TF1) | stable-baselines3 (PyTorch) |
| 训练环境 | 仿真 / PCC-Uspace | mahimahi + iperf3 真实网络 |
| 动作空间 | 调整发送速率 | 调整 c2tcp_target |
| 奖励函数 | throughput - 2e3 * latency | throughput / delay (Power) |
| c2tcp_target | 内核自调 | RL 智能体控制 (通过 sysfs) |

---

## 1. 系统要求

- **Ubuntu 20.04** (内核 5.4+)
- **root 权限** (写 sysfs 参数 + 运行 mahimahi)
- 至少 2 核 CPU，4 GB 内存

## 2. 安装依赖

### 2.1 系统包

```bash
# 内核编译 (如果没装)
sudo apt-get update
sudo apt-get install -y build-essential linux-headers-$(uname -r)

# mahimahi 网络模拟器
sudo apt-get install -y mahimahi

# iperf3 流量生成器
sudo apt-get install -y iperf3

# Python 3 及 pip
sudo apt-get install -y python3 python3-pip
```

### 2.2 Python 包

```bash
pip3 install --upgrade pip
pip3 install gym numpy
pip3 install stable-baselines3
pip3 install torch
```

> **注意**: 如果你要用 `tensorboard` 监控训练进度，还需安装:
> ```bash
> pip3 install tensorboard
> ```

## 3. 编译并加载内核模块

```bash
cd BBR
make clean
make
sudo insmod bbr_davis.ko

# 验证模块已加载
lsmod | grep bbr_davis

# 验证 sysfs 参数已暴露
cat /sys/module/bbr_davis/parameters/c2tcp_target_param
# 输出: 100000  (默认 100ms)
```

### 3.1 设置当前连接使用 bbr_davis

```bash
# 全局启用 bbr_davis
sudo sysctl net.ipv4.tcp_congestion_control=bbr_davis

# 或者只对特定连接使用 setsockopt (在 iperf3 中用 --congestion)
iperf3 -c <server> --congestion bbr_davis
```

### 3.2 手动测试 c2tcp_target 参数

```bash
# 修改 target 为 80ms
echo 80000 | sudo tee /sys/module/bbr_davis/parameters/c2tcp_target_param

# 确认已修改
cat /sys/module/bbr_davis/parameters/c2tcp_target_param
```

### 3.3 卸载模块

```bash
sudo rmmod bbr_davis
```

## 4. 运行训练

### 4.1 基本用法

```bash
# 必须用 sudo (需要写 sysfs 和运行 mahimahi)
sudo -E python3 src/gym/train_mahimahi.py
```

> 使用 `sudo -E` 保留用户环境变量，确保 Python 路径和已安装的包可用。

### 4.2 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--timesteps` | `500000` | 总训练步数 |
| `--history-len` | `5` | 观测历史长度 |
| `--iperf-dur` | `2` | 每次 iperf3 测试的秒数 |
| `--iperf-port` | `5201` | iperf3 服务端口 |
| `--steps-per-ep` | `100` | 每 episode 的步数 |
| `--lr` | `3e-4` | PPO 学习率 |
| `--n-steps` | `512` | PPO n_steps (每个 rollout 的步数) |
| `--batch-size` | `64` | PPO minibatch 大小 |
| `--gamma` | `0.99` | 折扣因子 |
| `--arch` | `64,64` | 策略网络架构 (逗号分隔) |
| `--model-dir` | `./bbr_rl_models/` | 模型保存目录（相对于当前工作目录） |
| `--load` | `""` | 加载指定模型继续训练 |
| `--resume` | `0` | 自动从 model-dir 中最新的 checkpoint 续训 |
| `--tb` | `/tmp/bbr_rl_tb/` | TensorBoard 日志目录 |
| `--early-stop` | `1` | 启用收敛自动终止 (`0` 关闭) |
| `--early-stop-window` | `50` | 收敛检测窗口 (episode 数) |
| `--early-stop-patience` | `5` | 连续无改善窗口数后停止 |
| `--early-stop-min-delta` | `0.01` | 最小 reward 提升阈值 |

参数格式为 `--key=value`，例如:

```bash
sudo -E python3 src/gym/train_mahimahi.py --timesteps=1000000 --iperf-dur=3 --steps-per-ep=150 --arch=128,128,64
```

### 4.3 收敛自动终止

默认启用。每 `--early-stop-window` 个 episode 检查最近窗口内 reward 的均值，
如果连续 `--early-stop-patience` 次未提升超过 `--early-stop-min-delta`，自动停止训练并保存模型。

关闭此功能:
```bash
sudo -E python3 src/gym/train_mahimahi.py --early-stop=0
```

### 4.4 存档与续训

训练过程中每 **5000 步**自动保存一次 checkpoint，文件名格式为 `bbr_target_model_{N}_steps.zip`。
Ctrl+C 中断时也会立即保存当前步数的 checkpoint。模型文件每个约 **60 KB**，50 万步跑完约占用 **6 MB**。

**中断后续训**（自动找到最新 checkpoint）:
```bash
sudo -E python3 src/gym/train_mahimahi.py --timesteps=500000 --resume
```

`--resume` 会扫描 `--model-dir` 下所有 `bbr_target_model_*_steps.zip` 文件，自动加载步数最大的那个，只跑剩余步数。

**加载指定的模型**:
```bash
sudo -E python3 src/gym/train_mahimahi.py --load=./bbr_rl_models/bbr_target_model_50000_steps
```

> **注意**: 模型默认保存在 `./bbr_rl_models/`（当前工作目录下），重启不会丢失。旧的默认路径 `/tmp/` 在重启后会被清空，不建议使用。

### 4.5 用 TensorBoard 监控

```bash
tensorboard --logdir /tmp/bbr_rl_tb/ --bind_all
# 浏览器打开 http://localhost:6006
```

## 5. 训练流程说明

每个 episode 的流程如下:

```
Episode 开始
  │
  ├─ 随机生成网络条件 (带宽 10-200 Mbps, 时延 5-150 ms, 丢包 0-2%)
  ├─ 生成 mahimahi trace 文件
  ├─ 重置 c2tcp_target = 100000 us (100 ms)
  │
  └─ 循环 100 步 (默认):
       ├─ Agent 输出 action: delta on c2tcp_target
       ├─ 写入 /sys/module/bbr_davis/parameters/c2tcp_target_param
       ├─ 重启 iperf3 server (避免 server busy)
       ├─ 运行: mm-link trace trace -- mm-delay <N> -- iperf3 -c ... -t 2 -J
       ├─ 解析 iperf3 JSON 输出 (吞吐量, 时延, 重传数)
       ├─ 计算 reward = (throughput / avg_rtt) * 0.001
       └─ 返回 observation, reward → Agent 学习
```

每一步需要约 2+ 秒 (iperf3 测试时长)。每 episode 100 步约需 3-4 分钟。

## 6. 使用训练好的模型

训练完成后，模型保存在 `--model-dir` 指定目录下。要使用模型进行推理:

```python
from stable_baselines3 import PPO
from mahimahi_env import MahimahiEnv, _write_target

# 加载模型
model = PPO.load("./bbr_rl_models/bbr_target_model_final")

# 加载环境获取观测
env = MahimahiEnv()
obs = env.reset()

# 推理: 获取最优 c2tcp_target
action, _ = model.predict(obs, deterministic=True)

# 手动应用 target
env._apply_delta(action)
print("最优 c2tcp_target: {} us ({} ms)".format(
    env.current_target, env.current_target / 1000))
```

或者直接用训练好的模型写入内核参数:

```python
import numpy as np
from stable_baselines3 import PPO
from mahimahi_env import _write_target, TARGET_DEFAULT

model = PPO.load("/tmp/bbr_rl_models/bbr_target_model_final")

# 需要构造当前观测... 简化版直接用一次推理
# 实际应用中应持续观测网络状态并动态调整
```

## 7. 常见问题

### 7.1 `PermissionError: /sys/module/bbr_davis/parameters/c2tcp_target_param`

确保用 `sudo -E` 运行脚本，且 bbr_davis 模块已加载。

### 7.2 `FileNotFoundError: /sys/module/bbr_davis/parameters/c2tcp_target_param`

模块未加载或加载名称不同。检查:
```bash
lsmod | grep bbr
ls /sys/module/bbr_davis/parameters/
```

如果路径不同，修改 `mahimahi_env.py` 中的 `SYSFS_TARGET_PATH` 变量。

### 7.3 mahimahi 错误: `mm-link: command not found`

```bash
sudo apt-get install -y mahimahi
```

### 7.4 iperf3 测试一直失败

检查 iperf3 服务器端口是否被占用:
```bash
sudo lsof -i :5201
sudo pkill iperf3
```

### 7.5 `ModuleNotFoundError: No module named 'stable_baselines3'`

```bash
pip3 install stable-baselines3
```

如果 `sudo -E` 后 Python 找不到包:
```bash
sudo -E python3 -m pip install stable-baselines3 gym numpy torch
```

### 7.6 训练很慢

- 默认 `--iperf-dur=2` (每次 iperf 测试 2 秒)，减少到 1 秒可加速但结果噪声较大
- 减少 `--steps-per-ep` 可加快 episode 循环
- 使用较小网络 (`--arch=32,16`) 可加速策略推理

---

## 8. 开发说明

- **动作**: `delta ∈ [-1, 1]`，应用于 `c2tcp_target = target * (1+delta)` 或 `target / (1-delta)`
- **观测**: 4 维特征 × `history_len` = 吞吐量、RTT、重传率、当前 target
- **奖励**: `power = throughput_bps / avg_rtt_us * 0.001`
- **c2tcp_target 范围**: [30000, 150000] us ([30ms, 150ms])
- **内核模块**: c2tcp_tuner 仍会基于 delay vs target 调整 alpha，但不再自动探索 target 值
