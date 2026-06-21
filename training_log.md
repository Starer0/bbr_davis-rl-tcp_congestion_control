# BBR c2tcp_target RL Training Log

## Run 1 — 2026-06-19 (baseline + clip [-1,1])  *未保留完整日志*

改动：
- power_reward 从 `actual_power / ideal_power` 改为 `actual_power / baseline_power`（baseline 为每 episode 开始跑 3 次默认 target 的均值）
- PID excess 也从绝对排队延迟改为减去 baseline_excess
- clip 保留，reward = clip(power_reward - pid_penalty, -1, 1)

配置：`--timesteps=50000`，从头训练。

结果（口述记录）：ep_rew_mean 在 **80 多**，从未超过 100。因为 clip 上限 1.0 卡住了——即使某些步比 baseline 好很多（power_reward > 1.0），进 clip 后只能拿到 1.0，跟 "不动参数" 没区别，无法区分好坏。checkpoint 保存在 3673 步。

---

## Run 2 — 2026-06-20 (baseline + 无 clip)

改动：去掉 reward clip，reward = power_reward - pid_penalty

配置：`--timesteps=50000 --resume`，从 Run 1 的 3673 步继续训练，其余超参不变。

```
[Episode] bw=6.8Mbps delay=102.4ms loss=1.29%
[Episode done] 100/100 ok  sum_reward=139.5
[Episode] bw=59.5Mbps delay=95.1ms loss=0.58%
[Episode done] 100/100 ok  sum_reward=106.2
[Episode] bw=160.6Mbps delay=359.6ms loss=0.57%
[Episode done] 100/100 ok  sum_reward=187.2
[Episode] bw=183.3Mbps delay=396.0ms loss=1.14% queue=droptail/437pkts
[Episode done] 100/100 ok  sum_reward=135.0
[Episode] bw=176.4Mbps delay=427.7ms loss=1.25%
[Episode done] 100/100 ok  sum_reward=46.4
[Episode] bw=20.7Mbps delay=277.9ms loss=1.81%
---------------------------------
| rollout/           |          |
|    ep_len_mean     | 100      |
|    ep_rew_mean     | 123      |
| time/              |          |
|    fps             | 0        |
|    iterations      | 1        |
|    time_elapsed    | 9053     |
|    total_timesteps | 512      |
---------------------------------
[Episode done] 100/100 ok  sum_reward=177.6
[Episode] bw=122.3Mbps delay=11.5ms loss=0.90%
[Episode done] 100/100 ok  sum_reward=110.0
[Episode] bw=102.9Mbps delay=368.3ms loss=0.00% queue=droptail/395pkts
[Episode done] 100/100 ok  sum_reward=150.7
[Episode] bw=134.7Mbps delay=431.0ms loss=1.37%
[Episode done] 100/100 ok  sum_reward=72.7
[Episode] bw=97.7Mbps delay=127.9ms loss=1.48% queue=droptail/424pkts
[Episode done] 100/100 ok  sum_reward=45.5
[Episode] bw=32.5Mbps delay=478.5ms loss=1.74% queue=droptail/377pkts
-----------------------------------------
| rollout/                |             |
|    ep_len_mean          | 100         |
|    ep_rew_mean          | 117         |
| time/                   |             |
|    fps                  | 0           |
|    iterations           | 2           |
|    time_elapsed         | 17791       |
|    total_timesteps      | 1024        |
| train/                  |             |
|    approx_kl            | 0.001796525 |
|    clip_fraction        | 0.00117     |
|    clip_range           | 0.2         |
|    entropy_loss         | -1.43       |
|    explained_variance   | -0.00858    |
|    learning_rate        | 0.0003      |
|    loss                 | 45          |
|    n_updates            | 25          |
|    policy_gradient_loss | -0.0014     |
|    std                  | 1.01        |
|    value_loss           | 97.8        |
-----------------------------------------
[Episode done] 100/100 ok  sum_reward=87.0
[Episode] bw=25.4Mbps delay=415.1ms loss=1.58%
[Episode done] 100/100 ok  sum_reward=78.9
[Episode] bw=132.3Mbps delay=26.5ms loss=0.36%
[Episode done] 100/100 ok  sum_reward=94.4
[Episode] bw=91.6Mbps delay=375.0ms loss=1.88%
[Checkpoint] Saved model to ./bbr_rl_models/bbr_target_model_5000_steps
[Episode done] 100/100 ok  sum_reward=157.7
[Episode] bw=195.4Mbps delay=168.9ms loss=1.41% queue=droptail/87pkts
[Episode done] 100/100 ok  sum_reward=88.6
[Episode] bw=127.0Mbps delay=309.8ms loss=1.04% queue=droptail/130pkts
------------------------------------------
| rollout/                |              |
|    ep_len_mean          | 100          |
|    ep_rew_mean          | 112          |
| time/                   |              |
|    fps                  | 0            |
|    iterations           | 3            |
|    time_elapsed         | 26923        |
|    total_timesteps      | 1536         |
| train/                  |              |
|    approx_kl            | 0.0043300213 |
|    clip_fraction        | 0.00898      |
|    clip_range           | 0.2          |
|    entropy_loss         | -1.43        |
|    explained_variance   | 0.00824      |
|    learning_rate        | 0.0003       |
|    loss                 | 38.2         |
|    n_updates            | 30           |
|    policy_gradient_loss | -0.00317     |
|    std                  | 1.01         |
|    value_loss           | 76.4         |
------------------------------------------
[Episode done] 100/100 ok  sum_reward=190.3
[Episode] bw=136.5Mbps delay=397.1ms loss=0.71% queue=droptail/375pkts
[Episode done] 100/100 ok  sum_reward=96.0
[Episode] bw=124.9Mbps delay=192.8ms loss=0.17% queue=droptail/409pkts
[Episode done] 100/100 ok  sum_reward=375.8
[Episode] bw=27.9Mbps delay=22.6ms loss=1.31%
[Episode done] 100/100 ok  sum_reward=94.6
[Episode] bw=2.5Mbps delay=97.4ms loss=1.99%
[Episode done] 100/100 ok  sum_reward=118.2
[Episode] bw=119.1Mbps delay=67.0ms loss=0.28% queue=droptail/438pkts
-----------------------------------------
| rollout/                |             |
|    ep_len_mean          | 100         |
|    ep_rew_mean          | 128         |
| time/                   |             |
|    fps                  | 0           |
|    iterations           | 4           |
|    time_elapsed         | 34873       |
|    total_timesteps      | 2048        |
| train/                  |             |
|    approx_kl            | 0.008826137 |
|    clip_fraction        | 0.0344      |
|    clip_range           | 0.2         |
|    entropy_loss         | -1.42       |
|    explained_variance   | 0.00479     |
|    learning_rate        | 0.0003      |
|    loss                 | 28.2        |
|    n_updates            | 35          |
|    policy_gradient_loss | -0.00515    |
|    std                  | 1           |
|    value_loss           | 66.3        |
-----------------------------------------
```

---

## 对比

| 指标 | Run 1 (baseline+clip) | Run 2 (baseline 无clip) |
|------|----------------------|-------------------------|
| ep_rew_mean | ~80~90 | 112~128 |
| clip_fraction | — | 0.001~0.034 (正常) |
| approx_kl | — | 0.002~0.009 (活跃) |
| sum_reward 范围 | — | +45 ~ +376 |
| 策略 std | — | 1.01 → 1.0 |
