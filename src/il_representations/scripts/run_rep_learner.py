from glob import glob
import inspect
import logging
import os

import numpy as np
from sacred import Experiment
from sacred.observers import FileStorageObserver
from stable_baselines3.common.policies import ActorCriticPolicy
from stable_baselines3.ppo import PPO

from il_representations import algos
from il_representations.algos.representation_learner import RepresentationLearner
from il_representations.algos.utils import LinearWarmupCosine
import il_representations.envs.auto as auto_env
from il_representations.envs.config import benchmark_ingredient
from il_representations.policy_interfacing import EncoderFeatureExtractor

represent_ex = Experiment('representation_learning',
                          ingredients=[benchmark_ingredient])


@represent_ex.config
def default_config():
    algo = "SimCLR"
    use_random_rollouts = False
    n_envs = 1
    timesteps = 640
    pretrain_only = False
    pretrain_epochs = 50
    scheduler = None
    representation_dim = 128
    ppo_finetune = True
    batch_size = 256
    scheduler_kwargs = dict()
    # this is useful for constructing tests where we want to truncate the
    # dataset to be small
    unit_test_max_train_steps = None
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
    trajectory = {'obs': [], 'acts': [], 'dones': []}
    obs = env.reset()
    for i in range(timesteps):
        trajectory['obs'].append(obs.squeeze())
        action = np.array([env.action_space.sample() for _ in range(env.num_envs)])
        obs, rew, dones, info = env.step(action)
        trajectory['acts'].append(action[0])
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
def run(benchmark, use_random_rollouts,
        seed, algo, n_envs, timesteps, representation_dim,
        ppo_finetune, pretrain_epochs, _config):
    # TODO fix to not assume FileStorageObserver always present
    log_dir = os.path.join(represent_ex.observers[0].dir, 'training_logs')
    os.mkdir(log_dir)

    if isinstance(algo, str):
        algo = getattr(algos, algo)

    # setup environment & dataset
    venv = auto_env.load_vec_env()
    color_space = auto_env.load_color_space()
    if use_random_rollouts:
        dataset_dict = get_random_traj(env=venv,
                                       timesteps=timesteps)
    else:
        dataset_dict = auto_env.load_dataset()

    # FIXME(sam): this creates weird action-at-a-distance, and doesn't save us
    # from specifying parameters in the default config anyway (Sacred will
    # complain if we include a param that isn't in the default config). Should
    # do one of the following:
    # 1. Decorate RepresentationLearner constructor with a Sacred ingredient.
    # 2. Just pass things manually.
    assert issubclass(algo, RepresentationLearner)
    init_sig = inspect.signature(RepresentationLearner.__init__)
    rep_learner_params = [p for p in init_sig.parameters if p != 'self']
    algo_params = {k: v for k, v in _config.items() if k in rep_learner_params}
    algo_params['color_space'] = color_space
    logging.info(f"Running {algo} with parameters: {algo_params}")
    model = algo(venv, log_dir=log_dir, **algo_params)

    # setup model
    model.learn(dataset_dict, pretrain_epochs)
    if ppo_finetune and not isinstance(model, algos.RecurrentCPC):
        encoder_checkpoint = model.encoder_checkpoints_path
        all_checkpoints = glob(os.path.join(encoder_checkpoint, '*'))
        latest_checkpoint = max(all_checkpoints, key=os.path.getctime)
        encoder_feature_extractor_kwargs = {'features_dim': representation_dim, 'encoder_path': latest_checkpoint}

        # TODO figure out how to not have to set `ortho_init` to False for the whole policy
        policy_kwargs = {'features_extractor_class': EncoderFeatureExtractor,
                         'features_extractor_kwargs': encoder_feature_extractor_kwargs,
                         'ortho_init': False}
        ppo_model = PPO(policy=ActorCriticPolicy, env=venv,
                        verbose=1, policy_kwargs=policy_kwargs)
        ppo_model = initialize_non_features_extractor(ppo_model)
        ppo_model.learn(total_timesteps=1000)

    venv.close()


if __name__ == '__main__':
    represent_ex.observers.append(FileStorageObserver('rep_learning_runs'))
    represent_ex.run_commandline()