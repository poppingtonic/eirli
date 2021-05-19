#!/usr/bin/env bash

CUDA_VISIBLE_DEVICES=3 xvfb-run -a python src/il_representations/scripts/pretrain_n_adapt.py with \
 cfg_base_3seed_1cpu_pt2gpu_2envs \
 cfg_repl_none \
 cfg_il_bc_nofreeze \
 cfg_bench_short_sweep_magical \
 tune_run_kwargs.num_samples=1 \
 tune_run_kwargs.resources_per_trial.gpu=0.25 \
 exp_ident=magical-no-augs \
 il_train.bc.n_batches=100000 \
 il_train.bc.augs=None
 # env_cfg.benchmark_name=procgen \
 # env_cfg.task_name=coinrun
 # il_train.bc.batch_size=512 \

