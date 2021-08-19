"""Implementation of FQF (Fully-parameterised Quantile Function) variant of
DQN. Going to check performance on Atari and then later tune for Procgen.
Originally copied from @ku2482 on Github (MIT license):

https://github.com/ku2482/fqf-iqn-qrdqn.pytorch/blob/master/fqf_iqn_qrdqn/agent/fqf_agent.py
"""
import warnings
from collections import deque
from typing import Iterable, Optional, Tuple

import numpy as np
import torch
import torch.nn.functional as F
from imitation.data.rollout import (generate_trajectories, min_timesteps,
                                    rollout_stats)
from stable_baselines3.common.vec_env import VecEnv
from torch import nn
from torch.optim import Adam, RMSprop


def update_params(optim: torch.optim.Optimizer,
                  loss: torch.Tensor,
                  networks: Iterable[torch.nn.Module],
                  retain_graph: bool = False,
                  grad_clipping: Optional[float] = None) -> None:
    """Perform backprop and optimiser step with gradient clipping.

    Args:
        optim: Torch optimiser for `networks`.
        loss: loss to do .backward() call on.
        networks: networks to update.
        retain_graph: should graph be retained in `loss.backward()`?
        grad_clipping: optional magnitude at which to clip each network's
            gradient norm. Leave as None (the default) to disable gradient
            clipping.
    """
    optim.zero_grad()
    loss.backward(retain_graph=retain_graph)
    # Clip norms of gradients to stebilize training.
    if grad_clipping is not None:
        for net in networks:
            torch.nn.utils.clip_grad_norm_(net.parameters(), grad_clipping)
    optim.step()


def disable_gradients(network: torch.nn.Module) -> None:
    """Set `.requires_grad=False` for all network params.

    This is used to prevent target networks from being updated after
    backprop."""
    # Disable calculations of gradients.
    for param in network.parameters():
        param.requires_grad = False


def calculate_huber_loss(td_errors: torch.Tensor,
                         kappa: float = 1.0) -> torch.Tensor:
    """Standard Huber loss with quadratic portion occupying the `[-kappa,
    kappa]` range; linear with gradient `kappa` outside that range."""
    return torch.where(td_errors.abs() <= kappa, 0.5 * td_errors.pow(2),
                       kappa * (td_errors.abs() - 0.5 * kappa))


def calculate_quantile_huber_loss(td_errors: torch.Tensor, taus: torch.Tensor,
                                  weights: torch.Tensor = None,
                                  kappa: float = 1.0) -> torch.Tensor:
    """Calculate quantile Huber loss, just like QR-DQN and FQF papers."""
    assert not taus.requires_grad
    batch_size, N, N_dash = td_errors.shape

    # Calculate huber loss element-wisely.
    element_wise_huber_loss = calculate_huber_loss(td_errors, kappa)
    assert element_wise_huber_loss.shape == (batch_size, N, N_dash)

    # Calculate quantile huber loss element-wisely.
    element_wise_quantile_huber_loss = torch.abs(taus[..., None] - (
            td_errors.detach() < 0).float()) * element_wise_huber_loss / kappa
    assert element_wise_quantile_huber_loss.shape == (batch_size, N, N_dash)

    # Quantile huber loss.
    batch_quantile_huber_loss = element_wise_quantile_huber_loss.sum(
        dim=1).mean(dim=1, keepdim=True)
    assert batch_quantile_huber_loss.shape == (batch_size, 1)

    # TODO(sam): what do these weights represent? Are we double-normalising
    # when we multiply by the weights and then take a mean, rather than a sum?
    # Extend docstring to explain what is going on.
    if weights is not None:
        quantile_huber_loss = (batch_quantile_huber_loss * weights).mean()
    else:
        quantile_huber_loss = batch_quantile_huber_loss.mean()

    return quantile_huber_loss


def evaluate_quantile_at_action(s_quantiles: torch.Tensor,
                                actions: torch.Tensor) -> torch.Tensor:
    assert s_quantiles.shape[0] == actions.shape[0]

    batch_size = s_quantiles.shape[0]
    N = s_quantiles.shape[1]

    # Expand actions into (batch_size, N, 1).
    action_index = actions[..., None].expand(batch_size, N, 1)

    # Calculate quantile values at specified actions.
    sa_quantiles = s_quantiles.gather(dim=2, index=action_index)

    return sa_quantiles


class RunningMeanStats:
    """Record a running mean by storing a sliding window of values."""

    def __init__(self, n: int = 10):
        self.n = n
        self.stats = deque(maxlen=n)

    def append(self, x: float):
        self.stats.append(x)

    def get(self) -> float:
        return np.mean(self.stats)


class LinearAnnealer:
    """Linearly anneal some value over a particular number of time steps."""

    def __init__(self, start_value: float, end_value: float, num_steps: int):
        assert num_steps > 0 and isinstance(num_steps, int)

        self.steps = 0
        self.start_value = start_value
        self.end_value = end_value
        self.num_steps = num_steps

        self.a = (self.end_value - self.start_value) / self.num_steps
        self.b = self.start_value

    def step(self) -> None:
        self.steps = min(self.num_steps, self.steps + 1)

    def get(self) -> float:
        assert 0 < self.steps <= self.num_steps
        return self.a * self.steps + self.b


class LazyMemory(dict):
    def __init__(self, capacity: int, state_shape: tuple,
                 device: torch.device):
        super(LazyMemory, self).__init__()
        self.capacity = int(capacity)
        self.state_shape = state_shape
        self.device = device
        self.reset()

    def reset(self) -> None:
        self['state'] = []
        self['next_state'] = []

        self['action'] = np.empty((self.capacity, 1), dtype=np.int64)
        self['reward'] = np.empty((self.capacity, 1), dtype=np.float32)
        self['done'] = np.empty((self.capacity, 1), dtype=np.float32)

        self._n = 0
        self._p = 0

    def append(self, state: np.ndarray, action: int, reward: float,
               next_state: np.ndarray, done: bool) -> None:
        self._append(state, action, reward, next_state, done)

    def _append(self, state: np.ndarray, action: int, reward: float,
                next_state: np.ndarray, done: bool) -> None:
        self['state'].append(state)
        self['next_state'].append(next_state)
        self['action'][self._p] = action
        self['reward'][self._p] = reward
        self['done'][self._p] = done

        self._n = min(self._n + 1, self.capacity)
        self._p = (self._p + 1) % self.capacity

        self.truncate()

    def truncate(self) -> None:
        while len(self) > self.capacity:
            del self['state'][0]
            del self['next_state'][0]

    def sample(self, batch_size: int) -> Tuple[np.ndarray, np.ndarray,
                                               np.ndarray, np.ndarray,
                                               np.ndarray]:
        indices = np.random.randint(low=0, high=len(self), size=batch_size)
        return self._sample(indices, batch_size)

    def _sample(self, indices: np.ndarray,
                batch_size: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray,
                                          np.ndarray, np.ndarray]:
        bias = -self._p if self._n == self.capacity else 0

        states = np.empty((batch_size, *self.state_shape), dtype=np.uint8)
        next_states = np.empty((batch_size, *self.state_shape),
                               dtype=np.uint8)

        for i, index in enumerate(indices):
            _index = np.mod(index + bias, self.capacity)
            states[i, ...] = self['state'][_index]
            next_states[i, ...] = self['next_state'][_index]

        states = torch.ByteTensor(states).to(self.device).float() / 255.
        next_states = torch.ByteTensor(next_states).to(
            self.device).float() / 255.
        actions = torch.LongTensor(self['action'][indices]).to(self.device)
        rewards = torch.FloatTensor(self['reward'][indices]).to(self.device)
        dones = torch.FloatTensor(self['done'][indices]).to(self.device)

        return states, actions, rewards, next_states, dones

    def get(self) -> dict:
        return dict(self)

    def __len__(self) -> int:
        return len(self['state'])


def has_params(module: torch.nn.Module, recurse: bool = True):
    """Check whether Torch module has parameters"""
    for param in module.parameters(recurse=recurse):
        return True
    return False


def initialize_weights_xavier(m: torch.nn.Module, gain: float = 1.0):
    if isinstance(m, nn.Linear) or isinstance(m, nn.Conv2d):
        torch.nn.init.xavier_uniform_(m.weight, gain=gain)
        if m.bias is not None:
            torch.nn.init.constant_(m.bias, 0)
    elif has_params(m, recurse=False):
        warnings.warn(f"Module {m} has its own parameters, but "
                      "initialize_weights_xavier cannot handle it.",
                      stacklevel=2)


def initialize_weights_he(m: torch.nn.Module):
    if isinstance(m, nn.Linear) or isinstance(m, nn.Conv2d):
        torch.nn.init.kaiming_uniform_(m.weight)
        if m.bias is not None:
            torch.nn.init.constant_(m.bias, 0)
    elif has_params(m, recurse=False):
        warnings.warn(f"Module {m} has its own parameters, but "
                      "initialize_weights_he cannot handle it.", stacklevel=2)


def record_mean_dict(sb_logger, value_dict):
    """Log all the {name: value} pairs in the given dict to SB3, converting
    Torch objects etc. as necessary."""
    for name, value in value_dict.items():
        if isinstance(value, torch.Tensor):
            value = value.item()
        sb_logger.record_mean(name, value)


class DQNBase(nn.Module):
    """State embedding netowrk. May be a slightly deeper Nature DQN?"""

    def __init__(self, num_channels: int, embedding_dim: int = 7 * 7 * 64):
        super(DQNBase, self).__init__()

        self.net = nn.Sequential(
            nn.Conv2d(num_channels, 32, kernel_size=8, stride=4, padding=0),
            nn.ReLU(),
            nn.Conv2d(32, 64, kernel_size=4, stride=2, padding=0),
            nn.ReLU(),
            nn.Conv2d(64, 64, kernel_size=3, stride=1, padding=0),
            nn.ReLU(),
        ).apply(initialize_weights_he)

        self.embedding_dim = embedding_dim

    def forward(self, states: torch.Tensor) -> torch.Tensor:
        batch_size = states.shape[0]

        # Calculate embeddings of states.
        state_embedding = self.net(states)
        # flatten
        state_embedding = state_embedding.view(state_embedding.size(0), -1)
        assert state_embedding.shape == (batch_size, self.embedding_dim)

        return state_embedding


class FractionProposalNetwork(nn.Module):
    """Network to propose quantile fractions for FQF.."""

    def __init__(self, N: int = 32, embedding_dim: int = 7 * 7 * 64):
        super(FractionProposalNetwork, self).__init__()

        self.net = nn.Sequential(nn.Linear(
            embedding_dim,
            N)).apply(lambda x: initialize_weights_xavier(x, gain=0.01))

        self.N = N
        self.embedding_dim = embedding_dim

    def forward(self, state_embeddings: torch.Tensor) -> torch.Tensor:
        batch_size = state_embeddings.shape[0]

        # Calculate (log of) probabilities q_i in the paper.
        log_probs = F.log_softmax(self.net(state_embeddings), dim=1)
        probs = log_probs.exp()
        assert probs.shape == (batch_size, self.N)

        tau_0 = torch.zeros((batch_size, 1),
                            dtype=state_embeddings.dtype,
                            device=state_embeddings.device)
        taus_1_N = torch.cumsum(probs, dim=1)

        # Calculate \tau_i (i=0,...,N).
        taus = torch.cat((tau_0, taus_1_N), dim=1)
        assert taus.shape == (batch_size, self.N + 1)

        # Calculate \hat \tau_i (i=0,...,N-1).
        tau_hats = (taus[:, :-1] + taus[:, 1:]).detach() / 2.
        assert tau_hats.shape == (batch_size, self.N)

        # Calculate entropies of value distributions.
        entropies = -(log_probs * probs).sum(dim=-1, keepdim=True)
        assert entropies.shape == (batch_size, 1)

        return taus, tau_hats, entropies


class CosineFractionEmbeddingNetwork(nn.Module):
    """Network that computes an embedding for quantile fractions tau by
      (1) first applying cosine to tau*pi*i for different values of i=1 to
          num_cosines, then
      (2) applying a linear layer and ReLU.
    The cosine-then-linear trick is used in both the IQN paper and the FQF
    paper."""

    def __init__(self, num_cosines: int = 64,
                 embedding_dim: int = 7 * 7 * 64):
        super(CosineFractionEmbeddingNetwork, self).__init__()

        self.net = nn.Sequential(nn.Linear(num_cosines, embedding_dim),
                                 nn.ReLU())
        self.num_cosines = num_cosines
        self.embedding_dim = embedding_dim

    def forward(self, taus: torch.Tensor) -> torch.Tensor:
        batch_size = taus.shape[0]
        N = taus.shape[1]

        # Calculate i * \pi (i=1,...,N).
        i_pi = np.pi * torch.arange(start=1,
                                    end=self.num_cosines + 1,
                                    dtype=taus.dtype,
                                    device=taus.device).view(
            1, 1, self.num_cosines)

        # Calculate cos(i * \pi * \tau).
        cosines = torch.cos(taus.view(batch_size, N, 1) * i_pi).view(
            batch_size * N, self.num_cosines)

        # Calculate embeddings of taus.
        tau_embeddings = self.net(cosines).view(batch_size, N,
                                                self.embedding_dim)

        return tau_embeddings


class QuantileValueNetwork(nn.Module):
    """Compute quantile values for each action, given merged state and
    quantile fraction embeddings."""

    def __init__(self, num_actions: int, embedding_dim: int = 7 * 7 * 64):
        super().__init__()

        self.net = nn.Sequential(
            nn.Linear(embedding_dim, 512),
            nn.ReLU(),
            nn.Linear(512, num_actions),
        )

        self.num_actions = num_actions
        self.embedding_dim = embedding_dim

    def forward(self, state_embeddings: torch.Tensor,
                tau_embeddings: torch.Tensor) -> torch.Tensor:
        assert state_embeddings.shape[0] == tau_embeddings.shape[0]
        assert state_embeddings.shape[1] == tau_embeddings.shape[2]

        # NOTE: Because variable taus correspond to either \tau or \hat \tau
        # in the paper, N isn't neccesarily the same as fqf.N.
        batch_size = state_embeddings.shape[0]
        N = tau_embeddings.shape[1]

        # Reshape into (batch_size, 1, embedding_dim).
        state_embeddings = state_embeddings.view(batch_size, 1,
                                                 self.embedding_dim)

        # Calculate embeddings of states and taus.
        embeddings = (state_embeddings * tau_embeddings).view(
            batch_size * N, self.embedding_dim)

        # Calculate quantile values.
        quantile_values = self.net(embeddings)

        return quantile_values.view(batch_size, N, self.num_actions)


class FQF(nn.Module):
    """Container for FQF networks and loss. Makes it easy to share
    representation (in the form of `self.dqn_net`). Also makes it easy to
    instantiate separate target networks with identical architecture."""

    def __init__(self,
                 num_channels: int,
                 num_actions: int,
                 N: int = 32,
                 num_cosines: int = 32,
                 embedding_dim: int = 7 * 7 * 64,
                 target: bool = False):
        super().__init__()

        # Feature extractor of DQN.
        self.feature_extractor = DQNBase(num_channels=num_channels)
        # Cosine embedding network.
        self.fraction_embedding_net = CosineFractionEmbeddingNetwork(
            num_cosines=num_cosines,
            embedding_dim=embedding_dim)
        # Quantile network.
        self.quantile_value_net = QuantileValueNetwork(
            num_actions=num_actions, )

        # Fraction proposal network.
        if not target:
            self.fraction_proposal_net = FractionProposalNetwork(
                N=N, embedding_dim=embedding_dim)

        self.N = N
        self.num_actions = num_actions
        self.num_cosines = num_cosines
        self.embedding_dim = embedding_dim
        self.target = target

    def calculate_state_embeddings(self,
                                   states: torch.Tensor) -> torch.Tensor:
        return self.feature_extractor(states)

    def calculate_fractions(self,
                            states: torch.Tensor = None,
                            state_embeddings: torch.Tensor = None,
                            fraction_proposal_net: torch.Tensor = None) -> \
            Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        assert states is not None or state_embeddings is not None
        assert not self.target or fraction_proposal_net is not None

        if state_embeddings is None:
            state_embeddings = self.feature_extractor(states)

        fraction_proposal_net = fraction_proposal_net if self.target \
            else self.fraction_proposal_net
        taus, tau_hats, entropies = fraction_proposal_net(state_embeddings)

        return taus, tau_hats, entropies

    def calculate_quantile_values(self, taus: torch.Tensor,
                                  states: torch.Tensor = None,
                                  state_embeddings: torch.Tensor = None) -> torch.Tensor:
        assert states is not None or state_embeddings is not None

        if state_embeddings is None:
            state_embeddings = self.feature_extractor(states)

        tau_embeddings = self.fraction_embedding_net(taus)
        return self.quantile_value_net(state_embeddings, tau_embeddings)

    def calculate_q(
            self,
            taus: Optional[torch.Tensor] = None,
            tau_hats: Optional[torch.Tensor] = None,
            states: Optional[torch.Tensor] = None,
            state_embeddings: Optional[torch.Tensor] = None,
            fraction_net: Optional[torch.nn.Module] = None) -> torch.Tensor:
        # FIXME(sam): make it so that this function takes:
        # - taus xor tau_hats
        # - states xor state_embeddings
        # (unless there is some compelling reason to support both interfaces)
        # Same goes for calculate_fractions().
        assert states is not None or state_embeddings is not None
        assert not self.target or fraction_net is not None

        if state_embeddings is None:
            state_embeddings = self.feature_extractor(states)

        batch_size = state_embeddings.shape[0]

        # Calculate fractions.
        if taus is None or tau_hats is None:
            taus, tau_hats, _ = self.calculate_fractions(
                state_embeddings=state_embeddings,
                fraction_proposal_net=fraction_net)

        # Calculate quantiles.
        quantile_value_hats = self.calculate_quantile_values(
            tau_hats, state_embeddings=state_embeddings)
        assert quantile_value_hats.shape == (
            batch_size, self.N, self.num_actions)

        # Calculate expectations of value distribution.
        q = ((taus[:, 1:, None] - taus[:, :-1, None]) * quantile_value_hats) \
            .sum(dim=1)
        assert q.shape == (batch_size, self.num_actions)

        return q


class FQFAgent:
    """Trainer class for FQF."""

    def __init__(self,
                 venv: VecEnv,
                 test_venv: VecEnv,
                 num_steps: int = 5 * (10 ** 7),
                 batch_size: int = 32,
                 N: int = 32,
                 num_cosines: int = 64,
                 ent_coef: float = 0.0,
                 kappa: float = 1.0,
                 quantile_lr: float = 5e-5,
                 fraction_lr: float = 2.5e-9,
                 memory_size: int = int(10 ** 6),
                 gamma: float = 0.99,
                 update_interval: int = 4,
                 target_update_interval: int = 10000,
                 log_interval: int = 1000,
                 start_steps: int = 50000,
                 epsilon_train: float = 0.01,
                 epsilon_eval: float = 0.001,
                 epsilon_decay_steps: int = 250000,
                 running_mean_steps: int = 100,
                 eval_interval: int = 250000,
                 num_eval_steps: int = 125000,
                 max_episode_steps: int = 27000,
                 grad_clipping: Optional[float] = None,
                 device: torch.device = torch.device('cpu')):
        """Construct the FQF agent.

        TODO(sam): finish this (long) docstring.

        Args:
           venv: vec environment to use for training. Should have only one
               underlying env (i.e. we can't train on actual batched vecenvs).
           test_venv: vec environment to use for evaluation. A separate
               environment is used for testing so that the agent can step the
               test environment without resetting the training environment.
           num_steps:
           batch_size: batch size for updates.
           N: number of quantiles to propose.
           num_cosines:
           ent_coef:
           kappa:
           quantile_lr:
           fraction_lr:
           memory_size: max number of time steps to keep in replay buffer.
           gamma: discount rate.
           update_interval:
           target_update_interval:
           log_interval: number of steps to wait between dumping logs
           start_steps:
           epsilon_train:
           epsilon_eval:
           epsilon_decay_steps:
           running_mean_steps: length of running mean to keep for train stats.
           eval_interval:
           num_eval_steps:
           max_episode_steps:
           grad_clipping: magnitude at which to clip gradients. Each network
               will be clipped separately with a global 2-norm clip (set to
               None to disable).
           device: device to place networks on.
        """
        # for training, we assume a venv with just one underlying environment
        # (we are using a venv rather than a gym env because all our other
        # code in the IL representations project works with venvs)
        assert venv.num_envs == 1, \
            f"expected venv with 1 env, got venv with {self.venv.num_envs} " \
            "envs"
        assert test_venv.num_envs >= 1
        self.venv = venv
        self.test_venv = test_venv

        self.device = device

        self.log_interval = log_interval

        # Replay memory which is memory-efficient to store stacked frames.
        self.memory = LazyMemory(
            memory_size, self.venv.observation_space.shape, self.device)

        self.train_return_running_mean = RunningMeanStats(running_mean_steps)

        self.steps = 0
        self.episodes = 0
        self.best_eval_score = -np.inf
        self.num_actions = self.venv.action_space.n
        self.num_steps = num_steps
        self.batch_size = batch_size

        self.eval_interval = eval_interval
        self.num_eval_steps = num_eval_steps
        self.gamma_n = gamma
        self.start_steps = start_steps
        self.epsilon_train = LinearAnnealer(1.0, epsilon_train,
                                            epsilon_decay_steps)
        self.epsilon_eval = epsilon_eval
        self.update_interval = update_interval
        self.target_update_interval = target_update_interval
        self.max_episode_steps = max_episode_steps
        self.grad_clipping = grad_clipping

        # ^^^ attrs above were originally set in BaseAgent constructor ^^^
        # vvv attrs below are from original FQF constructor vvv

        # Online network.
        self.online_net = FQF(
            num_channels=venv.observation_space.shape[0],
            num_actions=self.num_actions,
            N=N,
            num_cosines=num_cosines,
        ).to(self.device)
        # Target network.
        self.target_net = FQF(num_channels=venv.observation_space.shape[0],
                              num_actions=self.num_actions,
                              N=N,
                              num_cosines=num_cosines,
                              target=True).to(self.device)

        # Copy parameters of the learning network to the target network.
        self.update_target()
        # Disable calculations of gradients of the target network.
        disable_gradients(self.target_net)

        self.fraction_optim = RMSprop(
            self.online_net.fraction_proposal_net.parameters(),
            lr=fraction_lr,
            alpha=0.95,
            eps=0.00001)

        self.quantile_optim = Adam(
            list(self.online_net.feature_extractor.parameters()) +
            list(self.online_net.fraction_embedding_net.parameters()) +
            list(self.online_net.quantile_value_net.parameters()),
            lr=quantile_lr,
            eps=1e-2 / batch_size)

        # NOTE: The author said the training of Fraction Proposal Net is
        # unstable and value distribution degenerates into a deterministic
        # one rarely (e.g. 1 out of 20 seeds). So you can use entropy of value
        # distribution as a regularizer to stabilize (but possibly slow down)
        # training.
        self.ent_coef = ent_coef
        self.N = N
        self.num_cosines = num_cosines
        self.kappa = kappa

    def run(self, sb_logger):
        while True:
            stats = self.train_episode(sb_logger)
            # print(f'Episode done, total steps {self.steps}. Stats:')
            # print('-' * 50)
            # for k, v in stats.items():
            #     if isinstance(v, float):
            #         v_fmt = f'{v:.5g}'
            #     else:
            #         v_fmt = str(v)
            #     print(f'| {k:25} | {v_fmt:>18} |')
            # print('-' * 50)
            # print()
            if self.steps > self.start_steps and self.steps % self.log_interval == 0:
                sb_logger.dump()
            if self.steps > self.num_steps:
                break

    def should_explore(self, is_eval: bool = False) -> bool:
        # Use e-greedy for evaluation.
        if self.steps < self.start_steps:
            return True
        if is_eval:
            return np.random.rand() < self.epsilon_eval
        return np.random.rand() < self.epsilon_train.get()

    def explore(self, states: np.ndarray) -> np.ndarray:
        # Act with randomness.
        n_states = len(states)
        action = np.array([
            self.venv.action_space.sample() for _ in range(n_states)
        ])
        return action

    def exploit(self, states: np.ndarray) -> np.ndarray:
        # Act without randomness.
        state = torch.ByteTensor(states).to(
            self.device).float() / 255.
        with torch.no_grad():
            actions_dev = self.online_net.calculate_q(states=state).argmax()
            actions = actions_dev.cpu().numpy()
        return actions

    def train_episode(self, sb_logger):
        self.online_net.train()
        self.target_net.train()

        self.episodes += 1
        episode_return = 0
        episode_steps = 0

        done = False
        state, = self.venv.reset()

        while not done and episode_steps <= self.max_episode_steps:
            if self.should_explore(is_eval=False):
                action, = self.explore(state[None])
            else:
                action, = self.exploit(state[None])

            (next_state,), (reward,), (done,), _ = self.venv.step([action])

            # To calculate efficiently, I just set priority=max_priority here.
            self.memory.append(state, action, reward, next_state, done)

            self.steps += 1
            episode_steps += 1
            episode_return += reward
            state = next_state

            # doing the update
            self.epsilon_train.step()

            if self.steps % self.target_update_interval == 0:
                self.update_target()

            if self.steps % self.update_interval == 0 \
                    and self.steps >= self.start_steps:
                learn_stats = self.learn()
                record_mean_dict(sb_logger, learn_stats)

            if self.steps % self.eval_interval == 0:
                eval_stats = self.evaluate()
                record_mean_dict(sb_logger, eval_stats)
                self.online_net.train()

        # We log running mean of stats.
        self.train_return_running_mean.append(episode_return)

        train_stats_dict = {
            'train_return': self.train_return_running_mean.get(),
            'episodes': self.episodes,
            'episode_steps': episode_steps,
            'episode_return': episode_return,
        }
        record_mean_dict(sb_logger, train_stats_dict)

    def _eval_policy(self, states: np.ndarray) -> np.ndarray:
        """This is the policy that we use to sample trajectories during
        evaluation. It independently chooses to explore or exploit at each time
        step and for each environment in the vecenv."""
        random_mask = np.array(
            [self.should_explore(eval=True) for _ in range(len(states))])
        explore_inds = np.nonzero(random_mask)
        exploit_inds = np.nonzero(~random_mask)
        all_actions = np.zeros((len(states, ),), dtype=np.int64)
        all_actions[explore_inds] = self.explore(states[explore_inds])
        all_actions[exploit_inds] = self.exploit(states[exploit_inds])
        return all_actions

    def evaluate(self) -> dict:
        self.online_net.eval()
        trajectories = generate_trajectories(
            self._eval_policy, self.test_venv,
            sample_until=min_timesteps(self.num_eval_steps))
        stats = rollout_stats(trajectories)
        mean_return = stats['return_mean']

        if mean_return > self.best_eval_score:
            self.best_eval_score = mean_return

        return {
            'eval_mean_return': mean_return,
            'eval_num_steps': sum(len(traj.obs) for traj in trajectories),
        }

    def update_target(self) -> None:
        self.target_net.feature_extractor.load_state_dict(
            self.online_net.feature_extractor.state_dict())
        self.target_net.quantile_value_net.load_state_dict(
            self.online_net.quantile_value_net.state_dict())
        self.target_net.fraction_embedding_net.load_state_dict(
            self.online_net.fraction_embedding_net.state_dict())

    def learn(self) -> dict:
        states, actions, rewards, next_states, dones = \
            self.memory.sample(self.batch_size)
        weights = None

        # Calculate embeddings of current states.
        state_embeddings = self.online_net.calculate_state_embeddings(states)

        # Calculate fractions of current states and entropies.
        taus, tau_hats, entropies = \
            self.online_net.calculate_fractions(
                state_embeddings=state_embeddings.detach())

        # Calculate quantile values of current states and actions at tau_hats.
        current_sa_quantile_hats = evaluate_quantile_at_action(
            self.online_net.calculate_quantile_values(
                tau_hats, state_embeddings=state_embeddings), actions)
        assert current_sa_quantile_hats.shape == (self.batch_size, self.N, 1)

        # NOTE: Detach state_embeddings not to update convolution layers. Also,
        # detach current_sa_quantile_hats because I calculate gradients of taus
        # explicitly, not by backpropagation.
        fraction_loss = self.calculate_fraction_loss(
            state_embeddings.detach(), current_sa_quantile_hats.detach(),
            taus,
            actions, weights)

        quantile_loss, mean_q, errors = self.calculate_quantile_loss(
            state_embeddings, tau_hats, current_sa_quantile_hats, actions,
            rewards, next_states, dones, weights)
        assert errors.shape == (self.batch_size, 1)

        entropy_loss = -self.ent_coef * entropies.mean()

        update_params(self.fraction_optim,
                      fraction_loss + entropy_loss,
                      networks=[self.online_net.fraction_proposal_net],
                      retain_graph=True,
                      grad_clipping=self.grad_clipping)
        update_params(self.quantile_optim,
                      quantile_loss,
                      networks=[
                          self.online_net.feature_extractor,
                          self.online_net.fraction_embedding_net,
                          self.online_net.quantile_value_net
                      ],
                      retain_graph=False,
                      grad_clipping=self.grad_clipping)

        mean_ent_of_frac_dist = entropies.mean()
        rv_dict = {
            'fraction_loss': fraction_loss,
            'quantile_loss': quantile_loss,
            'entropy_loss': entropy_loss,
            'mean_q': mean_q,
            'mean_ent_of_frac_dist': mean_ent_of_frac_dist,
        }
        return rv_dict

    def calculate_fraction_loss(self, state_embeddings, sa_quantile_hats,
                                taus, actions, weights):
        assert not state_embeddings.requires_grad
        assert not sa_quantile_hats.requires_grad

        batch_size = state_embeddings.shape[0]

        with torch.no_grad():
            sa_quantiles = evaluate_quantile_at_action(
                self.online_net.calculate_quantile_values(
                    taus=taus[:, 1:-1], state_embeddings=state_embeddings),
                actions)
            assert sa_quantiles.shape == (batch_size, self.N - 1, 1)

        # NOTE: Proposition 1 in the paper requires F^{-1} is non-decreasing.
        # I relax this requirements and calculate gradients of taus even when
        # F^{-1} is not non-decreasing.

        values_1 = sa_quantiles - sa_quantile_hats[:, :-1]
        signs_1 = sa_quantiles > torch.cat(
            [sa_quantile_hats[:, :1], sa_quantiles[:, :-1]], dim=1)
        assert values_1.shape == signs_1.shape

        values_2 = sa_quantiles - sa_quantile_hats[:, 1:]
        signs_2 = sa_quantiles < torch.cat(
            [sa_quantiles[:, 1:], sa_quantile_hats[:, -1:]], dim=1)
        assert values_2.shape == signs_2.shape

        gradient_of_taus = (torch.where(signs_1, values_1, -values_1) +
                            torch.where(signs_2, values_2, -values_2)).view(
            batch_size, self.N - 1)
        assert not gradient_of_taus.requires_grad
        assert gradient_of_taus.shape == taus[:, 1:-1].shape

        # Gradients of the network parameters and corresponding loss
        # are calculated using chain rule.
        if weights is not None:
            fraction_loss = ((
                                 (gradient_of_taus * taus[:, 1:-1]).sum(dim=1,
                                                                        keepdim=True)) *
                             weights).mean()
        else:
            fraction_loss = \
                (gradient_of_taus * taus[:, 1:-1]).sum(dim=1).mean()

        return fraction_loss

    def calculate_quantile_loss(self, state_embeddings, tau_hats,
                                current_sa_quantile_hats, actions, rewards,
                                next_states, dones, weights):
        assert not tau_hats.requires_grad

        with torch.no_grad():
            # NOTE: Current and target quantiles share the same proposed
            # fractions to reduce computations. (i.e. next_tau_hats = tau_hats)

            # Calculate Q values of next states.
            next_state_embeddings = \
                self.target_net.calculate_state_embeddings(next_states)
            next_q = self.target_net.calculate_q(
                state_embeddings=next_state_embeddings,
                fraction_net=self.online_net.fraction_proposal_net)

            # Calculate greedy actions.
            next_actions = torch.argmax(next_q, dim=1, keepdim=True)
            assert next_actions.shape == (self.batch_size, 1)

            # Calculate quantile values of next states and actions at tau_hats.
            next_sa_quantile_hats = evaluate_quantile_at_action(
                self.target_net.calculate_quantile_values(
                    taus=tau_hats, state_embeddings=next_state_embeddings),
                next_actions).transpose(1, 2)
            assert next_sa_quantile_hats.shape == (self.batch_size, 1, self.N)

            # Calculate target quantile values.
            target_sa_quantile_hats = rewards[..., None] + (
                    1.0 - dones[
                ..., None]) * self.gamma_n * next_sa_quantile_hats
            assert target_sa_quantile_hats.shape == (self.batch_size, 1,
                                                     self.N)

        td_errors = target_sa_quantile_hats - current_sa_quantile_hats
        assert td_errors.shape == (self.batch_size, self.N, self.N)

        quantile_huber_loss = calculate_quantile_huber_loss(
            td_errors, tau_hats, weights=weights, kappa=self.kappa)

        return quantile_huber_loss, next_q.detach().mean().item(), \
            td_errors.detach().abs().sum(dim=1).mean(dim=1, keepdim=True)

    def __del__(self):
        self.venv.close()
        self.test_venv.close()
