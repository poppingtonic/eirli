import torch
from stable_baselines3.common.policies import BaseFeaturesExtractor


class EncoderFeatureExtractor(BaseFeaturesExtractor):
    def __init__(self, observation_space, features_dim=None, encoder=None, encoder_path=None, finetune=True):
        # Allow user to either pass in an existing encoder, or a path from which to load a pickled encoder
        assert encoder is not None or encoder_path is not None, \
            "You must pass in either an encoder object or a path to an encoder"
        assert not (encoder is not None and encoder_path is not None), \
            "Please pass in only one of `encoder` and `encoder_path`"
        if encoder is not None:
            representation_encoder = encoder
        else:
            representation_encoder = torch.load(encoder_path)

        # do forward prop to infer the feature dim
        if features_dim is None:
            # the [None] adds a batch dimension
            sample_obs = torch.FloatTensor(observation_space.sample()[None])
            dev_encoder = encoder.to(sample_obs.device)
            sample_dist = dev_encoder(sample_obs, traj_info=None)
            sample_out, = sample_dist.sample()
            features_dim, = sample_out.shape

        super().__init__(observation_space, features_dim)

        self.representation_encoder = representation_encoder

        if not finetune:
            # Set requires_grad to false if we want to not further train weights
            for param in self.representation_encoder.parameters():
                param.requires_grad = False

    def forward(self, observations):
        features_dist = self.representation_encoder(observations, traj_info=None)
        return features_dist.loc


class EncoderSimplePolicyHead(EncoderFeatureExtractor):
    # Not actually a FeatureExtractor for SB use, but a very simple Policy for use in Cynthia's BC code
    def __init__(self, observation_space, features_dim, action_size, encoder=None, encoder_path=None, finetune=True):
        super().__init__(observation_space, features_dim, encoder, encoder_path, finetune)
        self.action_layer = torch.nn.Linear(encoder.representation_dim, action_size)
        self.softmax = torch.nn.Softmax(dim=-1)

    def forward(self, observations):
        representation = super().forward(observations)
        action_probas = self.softmax(self.action_layer(representation))
        return action_probas