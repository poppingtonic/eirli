from il_representations.algos import augmenters, pair_constructors, encoders, losses, batch_extenders
import collections
from copy import deepcopy
from il_representations.scripts.utils import StagesToRun

def get_space_and_ref_configs(contrastive=True):
    if contrastive:
        space = collections.OrderedDict([
                ('repl:algo_params:batch_size', (64, 384)),
                ('repl:algo_params:optimizer_kwargs:lr', (1e-6, 1e-2, 'log-uniform')),
                ('repl:algo_params:representation_dim', (8, 256)),
                ('repl:algo_params:encoder_kwargs:obs_encoder_cls', ['BasicCNN',
                                                                     'MAGICALCNN']),
                ('il_train:freeze_encoder', [True, False]),
                ('il_test:n_rollouts', [20])
                                                ])
        base_refs = [
                {'repl:algo_params:batch_size': 256,
                 'repl:algo_params:optimizer_kwargs:lr': 0.0003,
                 'repl:algo_params:representation_dim': 128,
                 'repl:algo_params:encoder_kwargs:obs_encoder_cls': 'MAGICALCNN',
                 'il_train:freeze_encoder':  True,
                 'il_test:n_rollouts': 20
                    }]
    else:
        # Do not include batch size in search space
        space = collections.OrderedDict([
                ('repl:algo_params:representation_dim', (8, 256)),
                ('repl:algo_params:optimizer_kwargs:lr', (1e-6, 1e-2, 'log-uniform')),
                ('repl:algo_params:encoder_kwargs:obs_encoder_cls', ['BasicCNN',
                                                                     'MAGICALCNN']),
                ('il_train:freeze_encoder', [True, False]),
                ('il_test:n_rollouts', [20])
                ])
        base_refs = [
                {'repl:algo_params:representation_dim': 128,
                 'repl:algo_params:optimizer_kwargs:lr': 0.0003,
                 'repl:algo_params:encoder_kwargs:obs_encoder_cls': 'MAGICALCNN',
                 'il_train:freeze_encoder':  True,
                 'il_test:n_rollouts': 20
               }]

    return space, base_refs


def make_hp_tuning_configs(experiment_obj):
    @experiment_obj.named_config
    def tuning():
        use_skopt = True
        skopt_search_mode = 'max'
        metric = 'return_mean'
        stages_to_run = StagesToRun.REPL_AND_IL
        repl = {
            # this isn't a lot of training, but should be enough to tell whether
            # loss goes down quickly
            'batches_per_epoch': 1000,
            'n_epochs': 10,
        }
        tune_run_kwargs = dict(num_samples=50)
        _ = locals()
        del _

    @experiment_obj.named_config
    def temporal_cpc_tune():
        repl = {'algo': 'TemporalCPC'}
        skopt_space, skopt_ref_configs = get_space_and_ref_configs()
        _ = locals()
        del _

    @experiment_obj.named_config
    def temporal_cpc_aug_tune():
        repl = {'algo': 'TemporalCPC',
                'algo_params': {'augmenter': augmenters.AugmentContextAndTarget}
                }

        skopt_space, skopt_ref_configs = get_space_and_ref_configs()
        skopt_space[
            'repl:algo_params:augmenter_kwargs:augmenter_spec'] = ["translate,rotate,gaussian_blur",
                                                                   "translate,rotate",
                                                                   "translate,rotate,flip_ud,flip_lr"]
        skopt_ref_configs[0][
            'repl:algo_params:augmenter_kwargs:augmenter_spec'] = "translate,rotate,gaussian_blur"
        _ = locals()
        del _


    @experiment_obj.named_config
    def ac_temporal_cpc_tune():
        repl = {'algo': 'ActionConditionedTemporalCPC'}
        skopt_space, skopt_ref_configs = get_space_and_ref_configs()

        skopt_space['repl:algo_params:decoder_kwargs:action_encoding_dim'] = (64, 512)
        skopt_space['repl:algo_params:decoder_kwargs:action_embedding_dim'] = (5, 30)

        skopt_ref_configs[0]['repl:algo_params:decoder_kwargs:action_encoding_dim'] = 64
        skopt_ref_configs[0]['repl:algo_params:decoder_kwargs:action_embedding_dim'] = 5
        _ = locals()
        del _

    @experiment_obj.named_config
    def identity_cpc_aug_tune():
        repl = {'algo': 'TemporalCPC',
                'algo_params': {'target_pair_constructor': pair_constructors.IdentityPairConstructor,
                                'augmenter': augmenters.AugmentContextAndTarget}
                }
        skopt_space, skopt_ref_configs = get_space_and_ref_configs()
        skopt_space[
            'repl:algo_params:augmenter_kwargs:augmenter_spec'] = ["translate,rotate,gaussian_blur",
                                                                   "translate,rotate",
                                                                   "translate,rotate,flip_ud,flip_lr"]
        skopt_ref_configs[0][
            'repl:algo_params:augmenter_kwargs:augmenter_spec'] = "translate,rotate,gaussian_blur"

        _ = locals()
        del _

    @experiment_obj.named_config
    def temporal_ceb_tune():
        repl = {'algo': 'CEB'}
        skopt_space, skopt_ref_configs = get_space_and_ref_configs()
        skopt_space['repl:algo_params:loss_constructor_kwargs:beta'] = (0, 0.1)
        skopt_ref_configs[0]['repl:algo_params:loss_constructor_kwargs:beta'] = 0.01
        _ = locals()
        del _


    @experiment_obj.named_config
    def temporal_ceb_fixed_variance_tune():
        repl = {'algo': 'FixedVarianceCEB'}
        skopt_space, skopt_ref_configs = get_space_and_ref_configs()
        skopt_space['repl:algo_params:loss_constructor_kwargs:beta'] = (0, 0.1)
        skopt_ref_configs[0]['repl:algo_params:loss_constructor_kwargs:beta'] = 0.01
        _ = locals()
        del _

    @experiment_obj.named_config
    def identity_ceb_aug_tune():
        repl = {'algo': 'CEB',
                'algo_params': {'target_pair_constructor': pair_constructors.IdentityPairConstructor,
                                'augmenter': augmenters.AugmentContextAndTarget}
                }
        skopt_space, skopt_ref_configs = get_space_and_ref_configs()
        skopt_space['repl:algo_params:loss_constructor_kwargs:beta'] = (0, 0.1)
        skopt_space[
            'repl:algo_params:augmenter_kwargs:augmenter_spec'] = ["translate,rotate,gaussian_blur",
                                                                   "translate,rotate",
                                                                   "translate,rotate,flip_ud,flip_lr"]

        skopt_ref_configs[0]['repl:algo_params:loss_constructor_kwargs:beta'] = 0.01
        skopt_ref_configs[0][
            'repl:algo_params:augmenter_kwargs:augmenter_spec'] = "translate,rotate,gaussian_blur"
        _ = locals()
        del _

    @experiment_obj.named_config
    def temporal_cpc_momentum_tune():
        repl = {'algo': 'TemporalCPC',
                'algo_params': {'batch_extender': batch_extenders.QueueBatchExtender,
                                'encoder': encoders.MomentumEncoder,
                                'loss_calculator': losses.QueueAsymmetricContrastiveLoss}}
        skopt_space, skopt_ref_configs = get_space_and_ref_configs()
        skopt_space[
            'repl:algo_params:augmenter_kwargs:augmenter_spec'] = ["translate,rotate,gaussian_blur",
                                                                   "translate,rotate",
                                                                   "translate,rotate,flip_ud,flip_lr"]
        skopt_space['repl:algo_params:encoder_kwargs:momentum_weight'] = (0.99, 1.0)
        skopt_ref_configs[0][
            'repl:algo_params:augmenter_kwargs:augmenter_spec'] = "translate,rotate,gaussian_blur"
        skopt_ref_configs[0]['repl:algo_params:encoder_kwargs:momentum_weight'] = 0.995
        _ = locals()
        del _


    ### Non-Contrastive ###

    @experiment_obj.named_config
    def vae_tune():
        repl = {'algo': 'VariationalAutoencoder', 'algo_params': {'batch_size': 64}}
        skopt_space, skopt_ref_configs = get_space_and_ref_configs(contrastive=False)
        skopt_space['repl:algo_params:loss_constructor_kwargs:beta'] = (0, 1)
        skopt_ref_configs[0]['repl:algo_params:loss_constructor_kwargs:beta'] = 0.01
        _ = locals()
        del _

    @experiment_obj.named_config
    def dynamics_tune():
        repl = {'algo': 'DynamicsPrediction', 'algo_params': {'batch_size': 64}}
        skopt_space, skopt_ref_configs = get_space_and_ref_configs(contrastive=False)
        skopt_space['repl:algo_params:encoder_kwargs:action_encoding_dim'] = (8, 128)
        skopt_space['repl:algo_params:encoder_kwargs:action_embedding_dim'] = (5, 30)
        skopt_ref_configs[0]['repl:algo_params:decoder_kwargs:action_encoding_dim'] = 64
        skopt_ref_configs[0]['repl:algo_params:decoder_kwargs:action_embedding_dim'] = 5
        _ = locals()
        del _

    @experiment_obj.named_config
    def inverse_dynamics_tune():
        repl = {'algo': 'InverseDynamicsPrediction', 'algo_params': {'batch_size': 64}}
        skopt_space, skopt_ref_configs = get_space_and_ref_configs(contrastive=False)
        _ = locals()
        del _

    @experiment_obj.named_config
    def gail_tune():
        use_skopt = True
        skopt_search_mode = 'max'
        metric = 'return_mean'
        stages_to_run = StagesToRun.IL_ONLY
        il_train = {
            'algo': 'gail',
            'gail': {
                # 1e6 timesteps is enough to solve at least half the MAGICAL
                # tasks, so 500k is enough for the easy ones. For DMC I'm not
                # sure how many steps we need. CURL and RAD papers
                # (https://arxiv.org/pdf/2004.04136.pdf,
                # https://arxiv.org/pdf/2004.14990.pdf) suggest that 500k steps
                # is only enough to get ~200 reward on half-cheetah and
                # finger-spin using SAC-from-pixels, but they're using RL
                # rather than GAIL. Will try 500k first & see where we go from
                # there.
                'total_timesteps': 500000,
                # basically disable intermediate checkpoint saving
                'save_every_n_steps': int(1e10),
                # log, but not too often
                'log_interval_steps': int(1e3),
                # base type should be dict
                # (the space will customise this later on)
                'disc_augs': {},
                # things that seem reasonable but which I haven't quite decided
                # on fixing yet
                'ppo_batch_size': 48,
                'disc_batch_size': 48,
            }
        }
        venv_opts = {
            'venv_parallel': True,
        }
        skopt_space = collections.OrderedDict([
            ('il_train:gail:ppo_n_steps', (8, 32)),
            ('il_train:gail:ppo_n_epochs', (4, 12)),
            ('il_train:gail:ppo_init_learning_rate',
             (5e-6, 5e-4, 'log-uniform')),
            ('il_train:gail:ppo_gamma', (0.85, 1.0, 'uniform')),
            ('il_train:gail:ppo_gae_lambda', (0.6, 0.9, 'uniform')),
            ('il_train:gail:ppo_ent', (1e-10, 1e-3, 'log-uniform')),
            ('il_train:gail:ppo_adv_clip', (0.01, 0.15)),
            ('il_train:gail:disc_n_updates_per_round', (1, 8)),
            ('venv_opts:n_envs', (32, 48)),
            ('il_train:gail:disc_lr', (1e-6, 1e-3, 'log-uniform')),
            # lots of augmentation options
            ('il_train:gail:disc_augs:translate_ex', [True, False]),
            ('il_train:gail:disc_augs:rotate', [True, False]),
            ('il_train:gail:disc_augs:color_jitter_mid', [True, False]),
            ('il_train:gail:disc_augs:flip_lr', [True, False]),
            ('il_train:gail:disc_augs:noise', [True, False]),
            ('il_train:gail:disc_augs:erase', [True, False]),
            ('il_train:gail:disc_augs:gaussian_blur', [True, False]),
        ])
        skopt_ref_configs = [
            collections.OrderedDict([
                ('il_train:gail:ppo_n_steps', 8),
                ('il_train:gail:ppo_n_epochs', 12),
                ('il_train:gail:ppo_init_learning_rate', 1e-4),
                ('il_train:gail:ppo_gamma', 0.95),
                ('il_train:gail:ppo_gae_lambda', 0.8),
                ('il_train:gail:ppo_ent', 1e-7),
                ('il_train:gail:ppo_adv_clip', 0.05),
                ('il_train:gail:disc_n_updates_per_round', 4),
                ('venv_opts:n_envs', 32),
                ('il_train:gail:disc_lr', 2.5e-5),
                ('il_train:gail:disc_augs:translate_ex', True),
                ('il_train:gail:disc_augs:rotate', True),
                ('il_train:gail:disc_augs:color_jitter_mid', True),
                ('il_train:gail:disc_augs:flip_lr', True),
                ('il_train:gail:disc_augs:noise', True),
                ('il_train:gail:disc_augs:erase', True),
                ('il_train:gail:disc_augs:gaussian_blur', True),
            ]),
        ]

        _ = locals()
        del _
