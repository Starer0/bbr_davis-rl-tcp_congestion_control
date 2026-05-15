#!/usr/bin/env python3
"""
Train an RL agent to tune bbr_davis's c2tcp_target for maximum power.

Power = throughput / delay

Uses stable-baselines3 PPO with a MahimahiEnv that runs real iperf3 tests
through mahimahi-simulated network links.

Usage:
    sudo python train_mahimahi.py [--args]

Requires root because:
  - Writing to /sys/module/bbr_davis/parameters/c2tcp_target
  - Running mahimahi (mm-link, mm-delay)

If you see permission errors, run with:  sudo -E python train_mahimahi.py
"""

import sys
import os

# Ensure the gym and src directories are on the path
_current = os.path.dirname(os.path.abspath(__file__))
_parent = os.path.dirname(_current)
for p in (_current, _parent):
    if p not in sys.path:
        sys.path.insert(0, p)

from mahimahi_env import MahimahiEnv

# stable-baselines3
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback, EvalCallback, CallbackList
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.policies import ActorCriticPolicy
import numpy as np

from common.simple_arg_parse import arg_or_default


# ---------------------------------------------------------------------------
# CLI arguments (same parser style as the original project)
# ---------------------------------------------------------------------------
TIMESTEPS      = arg_or_default("--timesteps", default=500000)
HISTORY_LEN    = arg_or_default("--history-len", default=5)
IPERF_DURATION = arg_or_default("--iperf-dur", default=2)
IPERF_PORT     = arg_or_default("--iperf-port", default=5201)
STEPS_PER_EP   = arg_or_default("--steps-per-ep", default=100)
LEARNING_RATE  = arg_or_default("--lr", default=3e-4)
N_STEPS        = arg_or_default("--n-steps", default=512)
BATCH_SIZE     = arg_or_default("--batch-size", default=64)
GAMMA          = arg_or_default("--gamma", default=0.99)
ARCH           = arg_or_default("--arch", default="64,64")
MODEL_DIR      = arg_or_default("--model-dir", default="./bbr_rl_models/")
LOAD_MODEL     = arg_or_default("--load", default="")
TENSORBOARD    = arg_or_default("--tb", default="/tmp/bbr_rl_tb/")
EARLY_STOP     = arg_or_default("--early-stop", default=1)
ES_PATIENCE    = arg_or_default("--early-stop-patience", default=5)
ES_WINDOW      = arg_or_default("--early-stop-window", default=50)
ES_MIN_DELTA   = arg_or_default("--early-stop-min-delta", default=0.01)
RESUME         = arg_or_default("--resume", default=0)

os.makedirs(MODEL_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Resume logic: find latest checkpoint in model directory
# ---------------------------------------------------------------------------
import glob as _glob
completed_steps = 0
resume_checkpoint = None
CB_PATTERN = "bbr_target_model_*_steps.zip"

if RESUME:
    checkpoints = _glob.glob(os.path.join(MODEL_DIR, CB_PATTERN))
    if checkpoints:
        def _parse_steps(path):
            # path/to/bbr_target_model_100000_steps.zip -> 100000
            return int(os.path.basename(path).rsplit("_", 2)[1])

        resume_checkpoint = max(checkpoints, key=_parse_steps)
        completed_steps = _parse_steps(resume_checkpoint)
        print("[Resume] Loading checkpoint: {}".format(resume_checkpoint))
        print("[Resume] Completed steps: {}".format(completed_steps))
    else:
        print("[Resume] No {} found in {}. Starting fresh.".format(
            CB_PATTERN, MODEL_DIR))

remaining_timesteps = max(0, TIMESTEPS - completed_steps)

print("=" * 60)
print("BBR c2tcp_target RL Training (Mahimahi + iperf3)")
print("=" * 60)
print("  Total timesteps:  {}".format(TIMESTEPS))
print("  Completed steps:  {}".format(completed_steps))
print("  Remaining steps:  {}".format(remaining_timesteps))
print("  History length:   {}".format(HISTORY_LEN))
print("  iperf duration:   {} s".format(IPERF_DURATION))
print("  Steps per episode: {}".format(STEPS_PER_EP))
print("  Policy arch:      {}".format(ARCH))
print("  Learning rate:    {}".format(LEARNING_RATE))
print("  Model dir:        {}".format(MODEL_DIR))
print("=" * 60)


# ---------------------------------------------------------------------------
# Build policy network architecture from CLI
# ---------------------------------------------------------------------------
arch = [int(w) for w in ARCH.split(",")] if ARCH else [64, 64]
policy_kwargs = dict(net_arch=dict(pi=arch, vf=arch))

print("Policy net_arch: pi={pi}, vf={vf}".format(pi=arch, vf=arch))


# ---------------------------------------------------------------------------
# Progress logging callback
# ---------------------------------------------------------------------------
class ProgressCallback(BaseCallback):
    """Log training progress and save model checkpoints periodically."""

    def __init__(self, save_freq=50000, model_dir=MODEL_DIR, step_offset=0, verbose=1):
        super().__init__(verbose)
        self.save_freq = save_freq
        self.model_dir = model_dir
        self.step_offset = step_offset

    def _on_step(self) -> bool:
        total_steps = self.step_offset + self.n_calls
        if total_steps % self.save_freq == 0:
            path = os.path.join(self.model_dir,
                                "bbr_target_model_{}_steps".format(total_steps))
            self.model.save(path)
            if self.verbose > 0:
                print("[Checkpoint] Saved model to {}".format(path))
        return True


# ---------------------------------------------------------------------------
# Early stopping callback
# ---------------------------------------------------------------------------
class EarlyStopCallback(BaseCallback):
    """Stop training when mean episode reward stops improving.

    Every ``window`` episodes, the mean reward of the most recent window is
    compared against the best seen so far. If it does not improve by at least
    ``min_delta`` for ``patience`` consecutive windows, training stops.
    """

    def __init__(self, window=50, patience=5, min_delta=0.01, verbose=1):
        super().__init__(verbose)
        self.window = window
        self.patience = patience
        self.min_delta = min_delta
        self.episode_rewards = []
        self.best_mean = -np.inf
        self.no_improvement_count = 0
        self.episodes_since_check = 0

    def _on_step(self) -> bool:
        for info in self.locals.get("infos", []):
            if "episode" in info:
                self.episode_rewards.append(info["episode"]["r"])
                self.episodes_since_check += 1

        if self.episodes_since_check >= self.window:
            recent_mean = np.mean(self.episode_rewards[-self.window:])
            self.episodes_since_check = 0

            if recent_mean > self.best_mean + self.min_delta:
                self.best_mean = recent_mean
                self.no_improvement_count = 0
                if self.verbose > 0:
                    print("[EarlyStop] New best mean reward ({}-ep): {:.4f}".format(
                        self.window, self.best_mean))
            else:
                self.no_improvement_count += 1
                if self.verbose > 0:
                    print("[EarlyStop] No improvement ({}/{}). "
                          "Current: {:.4f}, Best: {:.4f}".format(
                              self.no_improvement_count, self.patience,
                              recent_mean, self.best_mean))

            if self.no_improvement_count >= self.patience:
                if self.verbose > 0:
                    print("\n[EarlyStop] Converged! Best mean reward ({}-ep): {:.4f}".format(
                        self.window, self.best_mean))
                return False

        return True


# ---------------------------------------------------------------------------
# Build environment
# ---------------------------------------------------------------------------
env = MahimahiEnv(
    history_len=HISTORY_LEN,
    iperf_duration=IPERF_DURATION,
    iperf_port=IPERF_PORT,
    steps_per_episode=STEPS_PER_EP,
)
env = Monitor(env)  # wraps with reward/episode-length tracking


# ---------------------------------------------------------------------------
# Create or load model
# ---------------------------------------------------------------------------
if resume_checkpoint is not None:
    print("Loading checkpoint for resume: {}".format(resume_checkpoint))
    model = PPO.load(resume_checkpoint, env=env, learning_rate=LEARNING_RATE)
elif LOAD_MODEL and os.path.exists(LOAD_MODEL + ".zip"):
    print("Loading existing model from {}".format(LOAD_MODEL))
    model = PPO.load(LOAD_MODEL, env=env, learning_rate=LEARNING_RATE)
else:
    print("Creating new PPO model...")
    model = PPO(
        "MlpPolicy",
        env,
        learning_rate=LEARNING_RATE,
        n_steps=N_STEPS,
        batch_size=BATCH_SIZE,
        gamma=GAMMA,
        policy_kwargs=policy_kwargs,
        tensorboard_log=TENSORBOARD if os.path.exists(os.path.dirname(TENSORBOARD)) else None,
        verbose=1,
    )

# ---------------------------------------------------------------------------
# Train
# ---------------------------------------------------------------------------
if remaining_timesteps <= 0:
    print("Already at or past target timesteps ({}). Nothing to train.".format(TIMESTEPS))
    print("To train further, increase --timesteps (e.g. --timesteps=800000 --resume)")
else:
    print("\nStarting training... ({} remaining timesteps)\n".format(remaining_timesteps))

    progress_cb = ProgressCallback(
        save_freq=5000,
        model_dir=MODEL_DIR,
        step_offset=completed_steps)
    callbacks = [progress_cb]
    if EARLY_STOP:
        early_stop_cb = EarlyStopCallback(
            window=ES_WINDOW, patience=ES_PATIENCE, min_delta=ES_MIN_DELTA, verbose=1)
        callbacks.append(early_stop_cb)
    callback = CallbackList(callbacks)

    try:
        # Note: learn(total_timesteps=TIMESTEPS) because model.num_timesteps
        # is already restored to `completed_steps` from the checkpoint.
        # The internal loop runs while num_timesteps < TIMESTEPS.
        model.learn(total_timesteps=TIMESTEPS, callback=callback)
    except KeyboardInterrupt:
        # model.num_timesteps reflects total steps seen (including resumed)
        int_path = os.path.join(MODEL_DIR,
                                "bbr_target_model_{}_steps".format(
                                    model.num_timesteps))
        model.save(int_path)
        print("\n[Interrupted] Saved checkpoint to {}".format(int_path))

    # -----------------------------------------------------------------------
    # Save final model
    # -----------------------------------------------------------------------
    final_path = os.path.join(MODEL_DIR, "bbr_target_model_final")
    model.save(final_path)
    print("Final model saved to {}".format(final_path))

env.close()
print("Done.")
