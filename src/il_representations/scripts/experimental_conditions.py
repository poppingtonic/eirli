from il_representations.algos import batch_extenders, encoders, losses, decoders, augmenters, pair_constructors
from il_representations.scripts.run_rep_learner import represent_ex



@represent_ex.named_config
def condition_one_temporal_cpc():
    # Baseline Temporal CPC with expert demonstrations
    algo = 'TemporalCPC'
    use_random_rollouts = False
    _ = locals()
    del _


@represent_ex.named_config
def condition_two_temporal_cpc_momentum():
    # Baseline Temporal CPC with momentum added
    algo = 'TemporalCPC'
    use_random_rollouts = False
    algo_params = {
        'batch_extender': batch_extenders.QueueBatchExtender,
        'encoder': encoders.MomentumEncoder,
        'loss_calculator': losses.QueueAsymmetricContrastiveLoss
    }
    _ = locals()
    del _


@represent_ex.named_config
def condition_three_temporal_cpc_sym_proj():
    # Baseline Temporal CPC with a symmetric projection head
    algo = 'TemporalCPC'
    use_random_rollouts = False
    algo_params = {'decoder': decoders.SymmetricProjectionHead}
    _ = locals()
    del _


@represent_ex.named_config
def condition_four_temporal_cpc_asym_proj():
    # Baseline Temporal CPC with an asymmetric projection head
    algo = 'TemporalCPC'
    use_random_rollouts = False
    algo_params = {'decoder': decoders.AsymmetricProjectionHead}
    _ = locals()
    del _


@represent_ex.named_config
def condition_five_temporal_cpc_augment_both():
    # Baseline Temporal CPC with augmentation of both context and target
    algo = 'TemporalCPC'
    use_random_rollouts = False
    algo_params = {'augmenter': augmenters.AugmentContextAndTarget}
    _ = locals()
    del _


@represent_ex.named_config
def condition_eight_temporal_autoencoder():
    # A variational autoencoder with weight on KLD loss set to 0, and temporal offset
    # between encoded image and target image
    algo = 'VariationalAutoencoder'
    use_random_rollouts = False
    algo_params = {
            'target_pair_constructor': pair_constructors.TemporalOffsetPairConstructor,
            'loss_calculator_kwargs': {'beta': 0}}
    _ = locals()
    del _



@represent_ex.named_config
def condition_nine_autoencoder():
    # A variational autoencoder with weight on KLD loss set to 0
    algo = 'VariationalAutoencoder'
    use_random_rollouts = False
    algo_params = {
            'loss_calculator_kwargs': {'beta': 0},
                    }
    _ = locals()
    del _


@represent_ex.named_config
def condition_ten_vae():
    # A variational autoencoder with weight on KLD loss set to 1.0
    algo = 'VariationalAutoencoder'
    use_random_rollouts = False
    algo_params = {
        'loss_calculator_kwargs': {'beta': 1.0}} # TODO What is a good default beta here?
    _ = locals()
    del _


@represent_ex.named_config
def condition_thirteen_temporal_vae_lowbeta():
    # A variational autoencoder with weight on KLD loss set to 0.01, and temporal offset
    # between encoded image and target image
    algo = 'VariationalAutoencoder'
    algo_params = {'loss_calculator_kwargs': {'beta': 0.01},
                   'target_pair_constructor': pair_constructors.TemporalOffsetPairConstructor,
                   }
    use_random_rollouts = False
    _ = locals()
    del _

@represent_ex.named_config
def condition_fourteen_temporal_vae_highbeta():
    # A variational autoencoder with weight on KLD loss set to 1.0, and temporal offset
    # between encoded image and target image
    algo = 'VariationalAutoencoder'
    algo_params = {'loss_calculator_kwargs': {'beta': 1.0},
                   'target_pair_constructor': pair_constructors.TemporalOffsetPairConstructor,
                   }
    use_random_rollouts = False
    _ = locals()
    del _


@represent_ex.named_config
def condition_eighteen_ac_temporal_vae_lowbeta():
    # An action-conditioned variational autoencoder with weight on KLD loss set to 0.01, and temporal offset
    # between encoded image and target image
    algo = 'ActionConditionedTemporalVAE'
    algo_params = {'loss_calculator_kwargs': {'beta': 0.01}},
    use_random_rollouts = False
    _ = locals()
    del _
