#!/usr/bin/env python3
"""Loads a webdataset and renders it into images, while printing out some
debugging info. Useful for verifying that the file contains what you think it
contains!"""
import itertools as it
import logging
import os
import pprint
from typing import Any, Dict, List, Optional

import numpy as np
from PIL import Image
import sacred
from sacred import Experiment

from il_representations.algos.utils import set_global_seeds
from il_representations.envs import auto
from il_representations.envs.config import (env_cfg_ingredient,
                                            env_data_ingredient)
from il_representations.utils import NUM_CHANS

sacred.SETTINGS['CAPTURE_MODE'] = 'no'  # workaround for sacred issue#740
render_dataset_ex = Experiment(
    'render_dataset',
    ingredients=[
        env_cfg_ingredient, env_data_ingredient
    ])


@render_dataset_ex.config
def default_config():
    # number of trajectories to illustrate
    n_traj = None
    # number of frames to write per trajectory (default: all of them)
    # (if more frames are specified than the length of the trajectory, then
    # some will be repeated)
    frames_per_traj = 10
    # where to write output?
    out_dir = None
    # config to load
    dataset_config = {'type': 'demos'}
    # when dealing with frame stacks, drop all but the latest frame
    keep_only_latest = False
    # size of border around images
    border_size = 4

    _ = locals()
    del _


@render_dataset_ex.named_config
def random_data():
    dataset_config = {'type': 'random'}
    _ = locals()
    del _

def trajectory_iter(dataset):
    """Yields one trajectory at a time from a webdataset."""
    traj = []
    ind = 0
    for frame in dataset:
        traj.append(frame)
        print(f"Appended frame {ind}")
        ind += 1
        if frame['dones']:
            yield traj
            traj = []


def sample_points(traj_len: int, n_points: Optional[int]=None) -> np.ndarray:
    """Collect `n_points` indices into array, spaced ~evenly (or just return
    all points, if n_points is None)."""
    if n_points is None:
        return np.arange(traj_len)
    lin_samples = np.linspace(0, traj_len - 1, n_points)
    rounded = np.round(lin_samples)
    return rounded.astype('int64')


def concat_traj(traj: List[Dict[str, Any]]) -> Dict[str, np.ndarray]:
    """Combine the per-step dictionaries that make up a trajectory into a
    single dictionary that maps keys to concatenated values."""
    frame0: Dict[str, Any] = traj[0]
    keys_to_stack: List[str] = []
    for key, value in frame0.items():
        if isinstance(value, np.ndarray):
            keys_to_stack.append(key)
    # for some reason using a dict comprehension here was confusing pytype
    # (2020.08.10)
    rv_dict = {}
    for key in keys_to_stack:
        stacked = np.stack([f[key] for f in traj], axis=0)
        rv_dict[key] = stacked
    return rv_dict


def get_n_chans() -> int:
    return NUM_CHANS[auto.load_color_space()]


def simplify_stacks(obs_vec: np.ndarray, keep_only_latest: bool) -> np.ndarray:
    # simple sanity checks to make sure frames are N*(C*H)*W
    assert obs_vec.ndim == 4, f"obs_vec.shape={obs_vec.shape}, so ndim != 4"
    if obs_vec.shape[-1] != obs_vec.shape[-2]:
        logging.warn(
            f"obs_vec.shape={obs_vec.shape} does not look N(C*F)HW, "
            "since H!=W")
    n_chans = get_n_chans()
    stack_len = obs_vec.shape[1] // n_chans
    assert stack_len * n_chans == obs_vec.shape[1], \
        f"obs_vec.shape={obs_vec.shape} should be N(C*F)HW, "\
        f"but first dim is not divisible by n_chans={n_chans}"
    new_shape = obs_vec.shape[:1] + (stack_len, n_chans) + obs_vec.shape[2:]
    destacked = np.reshape(obs_vec, new_shape)
    # put stack dimension first
    transposed = np.transpose(destacked, (1, 0, 2, 3, 4))
    if keep_only_latest:
        final_obs_vec = transposed[-1]
    else:
        final_obs_vec = np.concatenate(transposed, axis=3)
    # now it's actually N*C*H*W', where W' has absorbed all the stacked frames
    # from before
    return final_obs_vec


def to_film_strip(images: np.ndarray, border_size: int=1) -> np.ndarray:
    """Convert an N*C*H*W array of image frames into a horizontal 'film strip'
    with a black border of `border_size` separating the frames (as will as a
    border on the outsides)."""
    # make a big array to hold all the images
    n_images, n_chans, height, width = images.shape
    out_array_size = (n_chans, 2 * border_size + height,
                      n_images * width + (n_images + 1) * border_size)
    out_array = np.zeros(out_array_size, dtype=images.dtype)
    for idx, imag in enumerate(images):
        h_start = border_size
        h_stop = h_start + imag.shape[1]
        w_start = border_size * (idx + 1) + width * idx
        w_stop = w_start + imag.shape[2]
        out_array[:, h_start:h_stop, w_start:w_stop] = imag
    return out_array


def save_obs_as_film(obs: np.ndarray, dest: str, keep_only_latest: bool,
                     border_size: int, frames_per_traj: int) -> None:
    """Save a list of observations in N*(C*F)*H*W format into a file, after
    converting to a 'film strip' (appropriate for representing, e.g., a
    continuous trajectory)."""
    d = os.path.dirname(dest)
    if d:
        os.makedirs(d, exist_ok=True)
    simple_indices = sample_points(len(obs), frames_per_traj)
    obs = obs[simple_indices]
    images = simplify_stacks(obs, keep_only_latest=keep_only_latest)
    film = to_film_strip(images, border_size=border_size)
    film_hwc = np.transpose(film, (1, 2, 0))
    try:
        pil_image = Image.fromarray(film_hwc)
    except TypeError:
        # Minecraft is being saved in float (0, 1) format... maybe fi
        pil_image = Image.fromarray((film_hwc * 255).astype(np.uint8))

    pil_image.save(dest)


@render_dataset_ex.main
def run(n_traj: int, frames_per_traj: int, out_dir: str, dataset_config: dict,
        keep_only_latest: bool, border_size: int, seed: int) -> None:
    set_global_seeds(seed)
    logging.getLogger().setLevel(logging.INFO)

    print(f'Supplied dataset config:')
    pprint.pprint(dataset_config)

    # we only support loading one dataset (hence the [dataset_config] thing)
    (webdataset, ), combined_meta = auto.load_wds_datasets(
        configs=[dataset_config])

    print(f"Collected metadata from loaded dataset:")
    pprint.pprint(combined_meta)

    # now write same trajectories to out_dir
    os.makedirs(out_dir, exist_ok=True)
    trajectories = it.islice(trajectory_iter(webdataset), n_traj)
    for idx, trajectory in enumerate(trajectories):
        breakpoint()
        print(f"Hit trajectory {idx}")
        traj_dict = concat_traj(trajectory)
        num_str = f'{idx:06d}'
        for key in ('obs', 'next_obs'):
            save_obs_as_film(
                traj_dict[key],
                os.path.join(out_dir, f'{key}_{num_str}.png'),
                keep_only_latest=keep_only_latest,
                border_size=border_size,
                frames_per_traj=frames_per_traj)


if __name__ == '__main__':
    render_dataset_ex.run_commandline()