from il_representations.algos.representation_learner import RepresentationLearner, DEFAULT_HARDCODED_PARAMS
from il_representations.algos.encoders import MomentumEncoder, InverseDynamicsEncoder, DynamicsEncoder, RecurrentEncoder, StochasticEncoder, DeterministicEncoder
from il_representations.algos.decoders import ProjectionHead, NoOp, MomentumProjectionHead, BYOLProjectionHead, ActionConditionedVectorDecoder, TargetProjection
from il_representations.algos.losses import SymmetricContrastiveLoss, AsymmetricContrastiveLoss, MSELoss, CEBLoss, \
    QueueAsymmetricContrastiveLoss, BatchAsymmetricContrastiveLoss

from il_representations.algos.augmenters import AugmentContextAndTarget, AugmentContextOnly, NoAugmentation
from il_representations.algos.pair_constructors import IdentityPairConstructor, TemporalOffsetPairConstructor
from il_representations.algos.batch_extenders import QueueBatchExtender
from il_representations.algos.optimizers import LARS


class SimCLR(RepresentationLearner):
    """
    Implementation of SimCLR: A Simple Framework for Contrastive Learning of Visual Representations
    https://arxiv.org/abs/2002.05709

    This method works by using a contrastive loss to push together representations of two differently-augmented
    versions of the same image. In particular, it uses a symmetric contrastive loss, which compares the
    (target, context) similarity against similarity of context with all other targets, and also similarity
     of target with all other contexts.
    """
    def __init__(self, env, log_dir, **kwargs):
        self.validate_and_update_kwargs(kwargs)

        super().__init__(env=env,
                         log_dir=log_dir,
                         encoder=DeterministicEncoder,
                         decoder=ProjectionHead,
                         loss_calculator=SymmetricContrastiveLoss,
                         augmenter=AugmentContextAndTarget,
                         target_pair_constructor=IdentityPairConstructor,
                         **kwargs)


class TemporalCPC(RepresentationLearner):
    def __init__(self, env, log_dir, temporal_offset=1, **kwargs):
        """
        Implementation of a non-recurrent version of CPC: Contrastive Predictive Coding
        https://arxiv.org/abs/1807.03748

        By default, augments only the context, but can be modified to augment both context and target.
        """
        kwargs_updates = {'target_pair_constructor_kwargs': {'temporal_offset': temporal_offset}}
        self.validate_and_update_kwargs(kwargs, kwargs_updates=kwargs_updates)

        super().__init__(env=env,
                         log_dir=log_dir,
                         encoder=DeterministicEncoder,
                         decoder=NoOp,
                         loss_calculator=BatchAsymmetricContrastiveLoss,
                         target_pair_constructor=TemporalOffsetPairConstructor,
                         **kwargs)


class RecurrentCPC(RepresentationLearner):
    """
    Implementation of a recurrent version of CPC: Contrastive Predictive Coding
    https://arxiv.org/abs/1807.03748

    The encoder first encodes individual frames for both context and target, and then, for the context,
    builds up a recurrent representation of all prior frames in the same trajectory, to use to predict the target.

    By default, augments only the context, but can be modified to augment both context and target.
    """
    def __init__(self, env, log_dir, **kwargs):
        self.validate_and_update_kwargs(kwargs, kwargs_updates={'shuffle_batches': False})
        super().__init__(env=env,
                         log_dir=log_dir,
                         encoder=RecurrentEncoder,
                         decoder=NoOp,
                         loss_calculator=BatchAsymmetricContrastiveLoss,
                         target_pair_constructor=TemporalOffsetPairConstructor,
                         **kwargs)


class MoCo(RepresentationLearner):
    """
    Implementation of MoCo: Momentum Contrast for Unsupervised Visual Representation Learning
    https://arxiv.org/abs/1911.05722
    """
    def __init__(self, env, log_dir, **kwargs):
        hardcoded_params = DEFAULT_HARDCODED_PARAMS + ['batch_extender']
        self.validate_and_update_kwargs(kwargs, hardcoded_params=hardcoded_params)
        super().__init__(env=env,
                         log_dir=log_dir,
                         encoder=MomentumEncoder,
                         decoder=NoOp,
                         loss_calculator=QueueAsymmetricContrastiveLoss,
                         augmenter=AugmentContextAndTarget,
                         target_pair_constructor=TemporalOffsetPairConstructor,
                         batch_extender=QueueBatchExtender,
                         **kwargs)


class MoCoWithProjection(RepresentationLearner):
    """
    Implementation of MoCo: Momentum Contrast for Unsupervised Visual Representation Learning
    https://arxiv.org/abs/1911.05722

    Includes an additional projection head atop the representation and before the prediction
    """

    def __init__(self, env, log_dir, **kwargs):
        hardcoded_params = DEFAULT_HARDCODED_PARAMS + ['batch_extender']
        self.validate_and_update_kwargs(kwargs, hardcoded_params=hardcoded_params)

        super().__init__(env=env,
                         log_dir=log_dir,
                         encoder=MomentumEncoder,
                         decoder=MomentumProjectionHead,
                         loss_calculator=QueueAsymmetricContrastiveLoss,
                         augmenter=AugmentContextAndTarget,
                         target_pair_constructor=TemporalOffsetPairConstructor,
                         batch_extender=QueueBatchExtender,
                         **kwargs)


class DynamicsPrediction(RepresentationLearner):
    def __init__(self, env, log_dir, **kwargs):
        kwargs_updates = {'target_pair_constructor_kwargs': {'mode': 'dynamics'}}
        self.validate_and_update_kwargs(kwargs, kwargs_updates=kwargs_updates)
        super().__init__(env=env,
                         log_dir=log_dir,
                         encoder=DynamicsEncoder,
                         # Should be a pixel decoder that takes in action, currently errors
                         decoder=NoOp,
                         loss_calculator=MSELoss,
                         augmenter=AugmentContextOnly,
                         target_pair_constructor=TemporalOffsetPairConstructor,
                         **kwargs)

    def learn(self, dataset, training_epochs):
        raise NotImplementedError("DynamicsPrediction is not yet fully implemented")


class InverseDynamicsPrediction(RepresentationLearner):
    def __init__(self, env, log_dir, **kwargs):
        kwargs_updates = {'target_pair_constructor_kwargs': {'mode': 'inverse_dynamics'}}
        self.validate_and_update_kwargs(kwargs, kwargs_updates=kwargs_updates)

        super().__init__(env=env,
                         log_dir=log_dir,
                         encoder=InverseDynamicsEncoder,
                         # Should be a action decoder that takes in next obs representation
                         decoder=NoOp,
                         loss_calculator=MSELoss,
                         augmenter=AugmentContextOnly,
                         target_pair_constructor=TemporalOffsetPairConstructor,
                         **kwargs)

    def learn(self, dataset, training_epochs):
        raise NotImplementedError("InverseDynamicsPrediction is not yet fully implemented")


class BYOL(RepresentationLearner):
    """Implementation of Bootstrap Your Own Latent: https://arxiv.org/pdf/2006.07733.pdf

    The hyperparameters do _not_ match those in the paper (see Section 3.2).
    Most notably, the momentum weight is set to a constant, instead of annealed
    from 0.996 to 1 via cosine scheduling.
    """
    def __init__(self, env, log_dir, **kwargs):
        self.validate_and_update_kwargs(kwargs)
        super().__init__(env=env,
                         log_dir=log_dir,
                         encoder=MomentumEncoder,
                         decoder=BYOLProjectionHead,
                         loss_calculator=MSELoss,
                         augmenter=AugmentContextAndTarget,
                         target_pair_constructor=IdentityPairConstructor,
                         **kwargs)


class CEB(RepresentationLearner):
    """
    CEB with variance that is learned by StochasticEncoder
    """
    def __init__(self, env, log_dir, **kwargs):
        self.validate_and_update_kwargs(kwargs)
        super().__init__(env=env,
                         log_dir=log_dir,
                         encoder=StochasticEncoder,
                         decoder=NoOp,
                         loss_calculator=CEBLoss,
                         augmenter=NoAugmentation,
                         target_pair_constructor=TemporalOffsetPairConstructor,
                         **kwargs)

class FixedVarianceCEB(RepresentationLearner):
    """
    CEB with fixed rather than learned variance
    """
    def __init__(self, env, log_dir, **kwargs):
        self.validate_and_update_kwargs(kwargs)
        super().__init__(env=env,
                         log_dir=log_dir,
                         encoder=DeterministicEncoder,
                         decoder=NoOp,
                         loss_calculator=CEBLoss,
                         augmenter=AugmentContextAndTarget,
                         target_pair_constructor=TemporalOffsetPairConstructor,
                         **kwargs)

class FixedVarianceTargetProjectedCEB(RepresentationLearner):
    """
    """
    def __init__(self, env, log_dir, **kwargs):
        self.validate_and_update_kwargs(kwargs)
        super().__init__(env=env,
                         log_dir=log_dir,
                         encoder=DeterministicEncoder,
                         decoder=TargetProjection,
                         loss_calculator=CEBLoss,
                         augmenter=AugmentContextAndTarget,
                         target_pair_constructor=TemporalOffsetPairConstructor,
                         **kwargs)



class ActionConditionedTemporalCPC(RepresentationLearner):
    """
    Implementation of reinforcement-learning-specific variant of Temporal CPC which adds a projection layer on top
    of the learned representation which integrates an encoding of the actions taken between time (t) and whatever
    time (t+k) is specified in temporal_offset and used for pulling out the target frame. This, notionally, allows
    the algorithm to construct frame representations that are action-independent, rather than marginalizing over an
    expected policy, as might need to happen if the algorithm needed to predict the frame at time (t+k) over any
    possible action distribution.
    """
    def __init__(self, env, log_dir, **kwargs):
        kwargs_updates = {'preprocess_extra_context': False,
                          'target_pair_constructor_kwargs': {"mode": "dynamics"},
                          'decoder_kwargs': env.action_space}
        self.validate_and_update_kwargs(kwargs, kwargs_updates=kwargs_updates)

        super().__init__(env=env,
                         log_dir=log_dir,
                         target_pair_constructor=TemporalOffsetPairConstructor,
                         encoder=DeterministicEncoder,
                         decoder=ActionConditionedVectorDecoder,
                         loss_calculator=BatchAsymmetricContrastiveLoss,
                         **kwargs)

## Algos that should not be run in all-algo test because they are not yet finished
WIP_ALGOS = [DynamicsPrediction, InverseDynamicsPrediction]
