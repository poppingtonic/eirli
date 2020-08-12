import os
import gym
import torch
from glob import glob
from stable_baselines3.common.cmd_util import make_atari_env
from stable_baselines3.common.vec_env import VecFrameStack
from stable_baselines3.common.policies import ActorCriticPolicy
from stable_baselines3.ppo import PPO
import algos
from algos.representation_learner import RepresentationLearner
from policy_interfacing import EncoderFeatureExtractor
from sacred import Experiment
from sacred.observers import FileStorageObserver
import logging
import numpy as np
import inspect
from algos.utils import LinearWarmupCosine
represent_ex = Experiment('representation_learning')



@represent_ex.config
def default_config():
    env_id = 'BreakoutNoFrameskip-v4'
    algo = "MoCo"
    n_envs = 1
    train_from_expert = True
    timesteps = 640
    pretrain_only = False
    pretrain_epochs = 50
    scheduler = None
    representation_dim = 128
    ppo_finetune = True
    scheduler_kwargs = dict()
    _ = locals()
    del _


@represent_ex.named_config
def cosine_warmup_scheduler():
    scheduler = LinearWarmupCosine
    scheduler_kwargs = {'warmup_epoch': 2, 'T_max': 10}
    _ = locals()
    del _


def get_random_traj(env, timesteps):
    # Currently not designed for VecEnvs with n>1
    trajectory = {'states': [], 'actions': [], 'dones': []}
    obs = env.reset()
    for i in range(timesteps):
        trajectory['states'].append(obs.squeeze())
        action = np.array([env.action_space.sample() for _ in range(env.num_envs)])
        obs, rew, dones, info = env.step(action)
        trajectory['actions'].append(action[0])
        trajectory['dones'].append(dones[0])
    return trajectory


def initialize_non_features_extractor(sb3_model):
    # This is a hack to get around the fact that you can't initialize only some of the components of a SB3 policy
    # upon creation, and we in fact want to keep the loaded representation frozen, but orthogonally initalize other
    # components.
    sb3_model.policy.init_weights(sb3_model.policy.mlp_extractor, np.sqrt(2))
    sb3_model.policy.init_weights(sb3_model.policy.action_net, 0.01)
    sb3_model.policy.init_weights(sb3_model.policy.value_net, 1)
    return sb3_model


@represent_ex.main
def run(env_id, seed, algo, n_envs, timesteps, representation_dim, ppo_finetune, pretrain_epochs, _config):

    # TODO fix to not assume FileStorageObserver always present
    log_dir = os.path.join(represent_ex.observers[0].dir, 'training_logs')
    os.mkdir(log_dir)

    if isinstance(algo, str):
        correct_algo_cls = None
        for algo_name, algo_cls in inspect.getmembers(algos):
            if algo == algo_name:
                correct_algo_cls = algo_cls
                break
        algo = correct_algo_cls
    is_atari = 'NoFrameskip' in env_id

    # setup environment
    if is_atari:
        env = VecFrameStack(make_atari_env(env_id, n_envs, seed), 4)
    else:
        env = gym.make(env_id)

    data = get_random_traj(env=env, timesteps=timesteps)
    assert issubclass(algo, RepresentationLearner)

    rep_learner_params = inspect.getfullargspec(RepresentationLearner.__init__).args
    algo_params = {k: v for k, v in _config.items() if k in rep_learner_params}
    logging.info(f"Running {algo} with parameters: {algo_params}")
    model = algo(env, log_dir=log_dir, **algo_params)

    # setup model
    model.learn(data, pretrain_epochs)
    if ppo_finetune and not isinstance(model, algos.RecurrentCPC):
        encoder_checkpoint = model.encoder_checkpoints_path
        all_checkpoints = glob(os.path.join(encoder_checkpoint, '*'))
        latest_checkpoint = max(all_checkpoints, key=os.path.getctime)
        encoder_feature_extractor_kwargs = {'features_dim': representation_dim, 'encoder_path': latest_checkpoint}

        #TODO figure out how to not have to set `ortho_init` to False for the whole policy
        policy_kwargs = {'features_extractor_class': EncoderFeatureExtractor,
                         'features_extractor_kwargs': encoder_feature_extractor_kwargs,
                         'ortho_init': False}
        ppo_model = PPO(policy=ActorCriticPolicy, env=env, verbose=1, policy_kwargs=policy_kwargs)
        ppo_model = initialize_non_features_extractor(ppo_model)
        ppo_model.learn(total_timesteps=1000)
        env.close()


if __name__ == '__main__':
    represent_ex.observers.append(FileStorageObserver('rep_learning_runs'))
    represent_ex.run_commandline()