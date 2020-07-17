import torch
import torch.nn as nn
import copy
from torch.distributions import Normal
import numpy as np

"""
Encoders conceptually serve as the bit of the representation learning architecture that learns the representation itself
(except in RNN cases, where encoders only learn the per-frame representation). 

The only real complex thing to note here is the MomentumEncoder architecture, which creates two CNNEncoders, 
and updates weights of one as a slowly moving average of the other. Note that this bit of momentum is separated 
from the creation and filling of a queue of representations, which is handled by the BatchExtender module 
"""

DEFAULT_CNN_ARCHITECTURE = {
    'CONV': [
                {'out_dim': 32, 'kernel_size': 8, 'stride': 4},
                {'out_dim': 64, 'kernel_size': 4, 'stride': 2},
                {'out_dim': 64, 'kernel_size': 3, 'stride': 1},
            ],
    'DENSE': [
                {'in_dim': 64*7*7}
             ]
}

class Encoder(nn.Module):
    # Calls to self() will call self.forward()
    def encode_target(self, x, traj_info):
        return self(x, traj_info)

    def encode_context(self, x, traj_info):
        return self(x, traj_info)

    def encode_extra_context(self, x, traj_info):
        return x



class CNNEncoder(Encoder):
    def __init__(self, obs_shape, representation_dim, architecture=None, learn_scale=False):
        super(CNNEncoder, self).__init__()
        if architecture is None:
            architecture = DEFAULT_CNN_ARCHITECTURE
        self.input_channel = obs_shape[2]
        self.representation_dim = representation_dim
        self.conv_layers = []
        self.dense_layers = []
        for layer_spec in architecture['CONV']:
            self.conv_layers.append(nn.Conv2d(self.input_channel, layer_spec['out_dim'],
                                              kernel_size=layer_spec['kernel_size'], stride=layer_spec['stride']))
            self.input_channel = layer_spec['out_dim']
        # Needs to be a ModuleList rather than just a list for the parameters of the listed layers
        # to be visible as part of the module .parameters() return
        self.conv_layers = nn.ModuleList(self.conv_layers)

        for ind, layer_spec in enumerate(architecture['DENSE'][:-1]):
            in_dim, out_dim = layer_spec.get('in_dim'), layer_spec.get('out_dim')
            self.dense_layers.append(nn.Linear(in_dim, out_dim))
        self.mean_layer = nn.Linear(architecture['DENSE'][-1]['in_dim'], self.representation_dim)
        if learn_scale:
            self.scale_layer = nn.Linear(architecture['DENSE'][-1]['in_dim'], self.representation_dim)
        else:
            self.scale_layer = lambda x: torch.ones(self.representation_dim)

        self.dense_layers = nn.ModuleList(self.dense_layers)
        self.relu = nn.ReLU()

    def forward(self, x, traj_info=None):
        x = x.permute(0, 3, 1, 2)
        x /= 255
        for conv_layer in self.conv_layers:
            x = self.relu(conv_layer(x))
        x = torch.flatten(x, 1)
        for dense_layer in self.dense_layers:
            x = self.relu(dense_layer(x))

        mean = self.mean_layer(x)
        scale = torch.exp(self.scale_layer(x))
        return Normal(loc=mean, scale=scale)


class DynamicsEncoder(CNNEncoder):
    # For the Dynamics encoder we want to keep the ground truth pixels as unencoded pixels
    def encode_target(self, x, traj_info):
        return Normal(loc=x, scale=0)


class InverseDynamicsEncoder(CNNEncoder):
    def encode_extra_context(self, x, traj_info):
        return self.forward(x, traj_info)


class MomentumEncoder(Encoder):
    # TODO have some way to pass in optional momentum_weight param
    def __init__(self, obs_shape, representation_dim, learn_scale=False,
                 momentum_weight=0.999, architecture=None):
        super(MomentumEncoder, self).__init__()
        self.query_encoder = CNNEncoder(obs_shape, representation_dim, architecture, learn_scale)
        self.key_encoder = copy.deepcopy(self.query_encoder)
        self.momentum_weight = momentum_weight

    def parameters(self, recurse=True):
        return self.query_encoder.parameters()

    def forward(self, x, traj_info):
        return self.query_encoder(x, traj_info)

    def encode_target(self, x, traj_info):
        """
        Encoder target/keys using momentum-updated key encoder. Had some thought of making _momentum_update_key_encoder
        a backwards hook, but seemed overly complex for an initial POC
        :param x:
        :return:
        """
        with torch.no_grad():
            self._momentum_update_key_encoder()
            return self.key_encoder(x, traj_info)

    @torch.no_grad()
    def _momentum_update_key_encoder(self):
        for param_q, param_k in zip(self.query_encoder.parameters(), self.key_encoder.parameters()):
            param_k.data = param_k.data * self.momentum_weight + param_q.data * (1. - self.momentum_weight)


class RecurrentEncoder(Encoder):
    def __init__(self, obs_shape, representation_dim, learn_scale=False,
                 single_frame_architecture=None, num_recurrent_layers=2,
                 single_frame_repr_dim=None, min_traj_size=5):
        super(RecurrentEncoder, self).__init__()
        self.num_recurrent_layers = num_recurrent_layers
        self.min_traj_size = min_traj_size
        self.single_frame_repr_dim = representation_dim if single_frame_repr_dim is None else single_frame_repr_dim
        self.single_frame_encoder = CNNEncoder(obs_shape, self.single_frame_repr_dim,
                                               single_frame_architecture, learn_scale)
        self.context_rnn = nn.LSTM(self.single_frame_repr_dim, representation_dim,
                                   self.num_recurrent_layers, batch_first=True)

    def _reshape_and_stack(self, z, traj_info):
        batch_size = z.shape[0]
        input_shape = z.shape[1:]
        trajectory_id, timesteps = traj_info
        # We should have trajectory_id values for every element in the batch z
        assert len(z) == len(trajectory_id), "Every element in z must have a trajectory ID in a RecurrentEncoder"
        trajectory_id_arr = trajectory_id
        # A set of all distinct trajectory IDs
        trajectories = torch.unique(trajectory_id_arr)
        padded_trajectories = []
        mask_lengths = []
        for trajectory in trajectories:
            traj_timesteps = timesteps[trajectory_id_arr == trajectory]
            assert list(traj_timesteps) == sorted(list(traj_timesteps)), "Batches must be sorted to use a RecurrentEncoder"
            # Get all Z vectors associated with a trajectory, which have now been confirmed to be sorted timestep-wise
            traj_z = z[trajectory_id_arr == trajectory]
            # Keep track of how many actual unpadded values were in the trajectory
            mask_lengths.append(traj_z.shape[0])
            pad_size = batch_size - traj_z.shape[0]
            padding = torch.zeros((pad_size,) + input_shape)
            padded_z = torch.cat([traj_z, padding])
            padded_trajectories.append(padded_z)
        assert np.mean(mask_lengths) > self.min_traj_size, f"Batches must contain trajectories with an average " \
                                                           f"length above {self.min_traj_size}. Trajectories found: {traj_info}"
        stacked_trajectories = torch.stack(padded_trajectories, dim=0)
        return stacked_trajectories, mask_lengths

    def encode_target(self, x, traj_info):
        return self.single_frame_encoder(x, traj_info)

    def encode_context(self, x, traj_info):
        # Reshape the input z to be (some number of) batch_size-length trajectories
        z = self.single_frame_encoder(x, traj_info).loc
        stacked_trajectories, mask_lengths = self._reshape_and_stack(z, traj_info)
        hiddens, final = self.context_rnn(stacked_trajectories)
        # Pull out only the hidden states corresponding to actual non-padding inputs, and concat together
        masked_hiddens = []
        for i, trajectory_length in enumerate(mask_lengths):
            masked_hiddens.append(hiddens[i][:trajectory_length])
        flattened_hiddens = torch.cat(masked_hiddens, dim=0)

        # TODO update the RNN to be able to actually learn standard deviations
        return Normal(loc=flattened_hiddens, scale=1)

