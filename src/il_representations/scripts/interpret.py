import torch
import sacred
import math
import cv2
import sys
import numpy as np
import matplotlib.pyplot as plt
import torch.nn as nn
from torch.utils.data.dataloader import DataLoader

from sacred import Experiment
from sacred.observers import FileStorageObserver
from stable_baselines3.common.utils import get_device
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas

from captum.attr import IntegratedGradients, Saliency, DeepLift, LayerConductance, LayerGradCam, LayerActivation, \
    LayerAttribution, LayerIntegratedGradients, LayerGradientXActivation
from captum.attr import visualization as viz
from stable_baselines3.common.policies import ActorCriticCnnPolicy
from stable_baselines3.common.preprocessing import preprocess_obs

from il_representations.scripts.il_train import make_policy
from il_representations.algos.encoders import MomentumEncoder, InverseDynamicsEncoder, RecurrentEncoder
from il_representations.utils import TensorFrameWriter
import il_representations.envs.auto as auto_env
from il_representations.data.read_dataset import InterleavedDataset
from il_representations.envs.config import (env_cfg_ingredient,
                                            env_data_ingredient,
                                            venv_opts_ingredient)

sacred.SETTINGS['CAPTURE_MODE'] = 'sys'
interp_ex = Experiment('interp', ingredients=[
                                            env_cfg_ingredient,
                                            env_data_ingredient,
                                            venv_opts_ingredient
])

@interp_ex.config
def base_config():
    # Network setting
    encoder_path = ''

    # Data settings
    device = get_device("auto")
    save_video = False
    length = 2
    save_image = True  # If true, {length} number of images will be saved.
    dataset_configs = [{'type': 'demos'}]

    # If log_dir is set to None, then the images will not be saved. If it's "default", the images will be saved
    # in Sacred's default observer folder. Otherwise they will be saved in the path specified by log_dir
    log_dir = "default"
    filename = "default"
    show_imgs = False
    verbose = False

    # interp_algos = [
    #     # Primary Attribution: Evaluates contribution of each input feature to the output of a model.
    #     'saliency',
    #     'integrated_gradient',
    #     'deep_lift',
    #     # Layer Attribution: Evaluates contribution of each neuron in a given layer to the output of the model.
    #     'layer_conductance',
    #     'layer_gradcam',
    #     'layer_activation',
    #     'layer_gradxact'
    # ]
    chosen_algo = 'integrated_gradient'

    layer_kwargs = {
        'layer_conductance': {'module': 'encoder', 'layer_idx': 2},
        'layer_gradcam': {'module': 'encoder', 'layer_idx': 4},
        'layer_activation': {'module': 'encoder', 'layer_idx': 4},
        'layer_gradxact': {'module': 'encoder', 'layer_idx': 4},
    }


class Network(nn.Module):
    def __init__(self, policy):
        super(Network, self).__init__()
        self.policy = policy

    def forward(self, obs):
        latent_pi, latent_vf, latent_sde = self.policy._get_latent(obs)
        mean_actions = self.policy.action_net(latent_pi)
        return mean_actions


class InterpAlgos:
    def __init__(self):
        self._algos = {}

    def get(self, name):
        return self._algos[name]

    def register(self, f):
        self._algos[f.__name__] = f


interp_algos = InterpAlgos()


@interp_ex.capture
def prepare_network(combined_meta, encoder_path, verbose, device):
    encoder = torch.load(encoder_path, map_location=device)
    if isinstance(encoder, ActorCriticCnnPolicy):
        policy = encoder
    else:
        policy = make_policy(combined_meta['observation_space'],
                             combined_meta['action_space'],
                             encoder,
                             None,
                             lr_schedule=None)
    network = Network(policy).to(device)
    network.eval()
    if verbose:
        print('Network structure:')
        print(network)
    return network


@interp_ex.capture
def process_data(device, env_cfg, length):
    img_list = []
    label_list = []
    benchmark_name = env_cfg['benchmark_name']
    print(f'Loading benchmark {benchmark_name}...')
    data_dict = auto_env.load_dict_dataset(benchmark_name)
    imgs = [x for x in range(length)]
    for img_idx in imgs:
        img = data_dict['obs'][img_idx]
        label = data_dict['acts'][img_idx]
        if isinstance(label, np.ndarray):
            label = np.argmax(label)
        img = torch.FloatTensor(img).to(device).unsqueeze(dim=0)
        img.requires_grad = True
        label = int(label)
        img_list.append(img)
        label_list.append(label)

    return img_list, label_list


@interp_ex.capture
def save_img(save_name, save_dir):
    plt.savefig(f'{save_dir}/{save_name}.png')
    print(f'Saved image at {save_dir}/{save_name}.png')
    plt.close()


def figure_2_tensor(fig):
    """
    Captum's visualize_image_attr method returns matplotlib.pyplot.figure object. To process and plot figures,
    we need to convert them to torch tensors first.
    """
    canvas = FigureCanvas(fig)
    canvas.draw()
    image_shape = fig.get_size_inches() * fig.dpi
    channel = np.array([3])
    image_shape = np.concatenate((image_shape, channel)).astype(int)
    image = np.frombuffer(canvas.tostring_rgb(), dtype='uint8')
    image = image.reshape(image_shape)
    image = torch.Tensor(image)
    return image


def attribute_image_features(network, algorithm, image, label, **kwargs):
    network.zero_grad()
    tensor_attributions = algorithm.attribute(image,
                                              target=label,
                                              **kwargs)
    return tensor_attributions


@interp_algos.register
def saliency(net, tensor_image, label):
    saliency = Saliency(net)
    grads = saliency.attribute(tensor_image, target=label)
    grads = np.transpose(grads.squeeze().cpu().detach().numpy(), (1, 2, 0))
    saliency_viz = viz.visualize_image_attr(grads,
                                            tensor_image[0].permute(1, 2, 0).detach().cpu().numpy(),
                                            method="blended_heat_map",
                                            sign="absolute_value",
                                            show_colorbar=True,
                                            title="Overlayed Gradient Magnitudes")
    return figure_2_tensor(saliency_viz[0])


@interp_algos.register
def integrated_gradient(net, tensor_image, label):
    ig = IntegratedGradients(net)
    attr_ig, delta = attribute_image_features(net, ig, tensor_image, label,
                                              baselines=tensor_image * 0,
                                              return_convergence_delta=True, )
    attr_ig = np.transpose(attr_ig.squeeze().cpu().detach().numpy(), (1, 2, 0))
    ig_viz = viz.visualize_image_attr(attr_ig,
                                      tensor_image[0].permute(1, 2, 0).detach().cpu().numpy(),
                                      method="blended_heat_map",
                                      sign="all",
                                      show_colorbar=True,
                                      title="Overlayed Integrated Gradients")
    return figure_2_tensor(ig_viz[0])


@interp_algos.register
def deep_lift(net, tensor_image, label):
    dl = DeepLift(net)
    attr_dl = attribute_image_features(net, dl, tensor_image, label,
                                       baselines=tensor_image * 0,)
    attr_dl = np.transpose(attr_dl.squeeze(0).cpu().detach().numpy(), (1, 2, 0))
    dl_viz = viz.visualize_image_attr(attr_dl,
                                      tensor_image[0].permute(1, 2, 0).detach().cpu().numpy(),
                                      method="blended_heat_map",
                                      sign="all",
                                      show_colorbar=True,
                                      title="Overlayed DeepLift")
    return figure_2_tensor(dl_viz[0])


@interp_algos.register
def layer_conductance(net, layer, image, label, log_dir, show_imgs=True, columns=10):
    layer_cond = LayerConductance(net, layer)
    attribution = layer_cond.attribute(image,
                                       n_steps=100,
                                       attribute_to_layer_input=True,
                                       target=label,)
    attribution = attribution[0]
    if len(attribution.shape) == 2:  # Attribution has 2 axes - usually seen in linear layers.
        l_weight = layer.weight
        plot_linear_layer_attributions(attribution, l_weight, 'layer_conductance', log_dir, show_imgs)
    elif len(attribution.shape) == 4:  # Attribution has 4 axes - usually seen in convolution layers.
        attribution = attribution[0]
        num_channels = attribution.shape[0]
        show_img_grid(attribution, math.ceil(num_channels/columns), columns, log_dir, 'layer_conductance',
                      'layer_conductance', show_imgs)
    else:
        raise NotImplementedError('Incompatible attribute shape.')

    return attribution


@interp_algos.register
def layer_gradcam(net, layer, image, label, original_img, log_dir, show_imgs):
    assert isinstance(layer, torch.nn.Conv2d), 'GradCAM is usually applied to the last ' \
                                               'convolutional layer in the network.'
    lgc = LayerGradCam(net, layer)
    gc_attr = lgc.attribute(image, target=label)
    upsampled_gc_attr = LayerAttribution.interpolate(gc_attr, image.shape[2:])  # Shape [1, 1, 84, 84]
    lg_viz_pos = viz.visualize_image_attr(upsampled_gc_attr[0].cpu().permute(1, 2, 0).detach().numpy(),
                                      original_img, method="blended_heat_map", sign="positive",
                                      show_colorbar=True,
                                      title="Layer GradCAM (Positive)")
    lg_viz_neg = viz.visualize_image_attr(upsampled_gc_attr[0].cpu().permute(1, 2, 0).detach().numpy(),
                                      original_img, method="blended_heat_map", sign="negative",
                                      show_colorbar=True,
                                      title="Layer GradCAM (Negative)")
    save_img(figure_2_tensor(lg_viz_pos[0]), 'layer_gradcam_pos', log_dir, show=show_imgs)
    save_img(figure_2_tensor(lg_viz_neg[0]), 'layer_gradcam_neg', log_dir, show=show_imgs)


@interp_algos.register
def layer_act(net, layer, algo, algo_name, image, log_dir, attr_kwargs=None, show_imgs=True, columns=10):
    layer_a = algo(net, layer)
    a_attr = layer_a.attribute(image, **attr_kwargs)
    if len(a_attr.shape) == 2:  # Attribution has 2 axes - usually seen in linear layers.
        l_weight = layer.weight
        plot_linear_layer_attributions(a_attr, l_weight.T, algo_name, log_dir, show_imgs)
    elif len(a_attr.shape) == 4:  # Attribution has 4 axes - usually seen in convolution layers.
        a_attr = a_attr[0]
        num_channels = a_attr.shape[0]
        layer_info = str(layer)
        img_title = f'{algo_name} of {layer_info}'
        show_img_grid(a_attr, math.ceil(num_channels/columns), columns, log_dir, algo_name,
                      img_title, show_imgs)
    else:
        raise NotImplementedError(f'Incompatible attribute shape {a_attr.shape}')


def show_img_grid(imgs, rows, columns, save_dir, save_name, img_title, show):
    fig = plt.figure()
    plt.title(img_title, verticalalignment='baseline')
    plt.axis('off')
    for i in range(len(imgs)):
        img = imgs[i]
        if isinstance(img, torch.Tensor):
            img = img.detach().numpy()
        img = cv2.resize(img, dsize=(40, 40), interpolation=cv2.INTER_CUBIC)
        fig.add_subplot(rows, columns, i+1)
        plt.axis('off')
        plt.imshow(img)
    if save_dir:
        plt.savefig(f'{save_dir}/{save_name}.png', dpi=400)
        print(f'Saved image at {save_dir}/{save_name}.png')
    if show:
        plt.show()
    plt.close(fig)


def plot_linear_layer_attributions(lc_attr_test, layer_weight, save_name, save_dir, show_imgs=True):
    plt.figure(figsize=(15, 8))

    x_axis_data = np.arange(lc_attr_test.shape[1])

    y_axis_lc_attr_test = lc_attr_test.mean(0).detach().numpy()
    y_axis_lc_attr_test = y_axis_lc_attr_test / np.linalg.norm(y_axis_lc_attr_test, ord=1)

    y_axis_lin_weight = layer_weight[0].detach().numpy()
    y_axis_lin_weight = y_axis_lin_weight / np.linalg.norm(y_axis_lin_weight, ord=1)

    width = 0.25
    legends = ['Attributions', 'Weights']
    x_axis_labels = ['Neuron {}'.format(i) for i in range(len(y_axis_lin_weight))]

    ax = plt.subplot()
    ax.set_title('Aggregated neuron importances and learned weights in the indicated linear layer of the model')

    ax.bar(x_axis_data + width, y_axis_lc_attr_test, width, align='center', alpha=0.5, color='red')
    ax.bar(x_axis_data + 2 * width, y_axis_lin_weight, width, align='center', alpha=0.5, color='green')
    plt.legend(legends, loc=2, prop={'size': 20})
    ax.autoscale_view()
    plt.tight_layout()

    ax.set_xticks(x_axis_data + 0.5)
    ax.set_xticklabels(x_axis_labels)

    if save_dir:
        plt.savefig(f'{save_dir}/{save_name}.png')
        print(f'Saved image at {save_dir}/{save_name}.png')
    if show_imgs:
        plt.show()


def choose_layer(network, module_name, layer_idx):
    """
    network - The network to be interpreted.
    module_name - The network module being interpreted. It should be either "encoder" or "decoder".
    layer_idx - The index of the layer to be studied. It belongs to the module specified by module_name.
    """

    if module_name == 'encoder':
        rep_encoder = network.policy.features_extractor.representation_encoder

        if isinstance(rep_encoder, MomentumEncoder):
            module = rep_encoder.query_encoder.network.shared_network
        elif isinstance(rep_encoder, RecurrentEncoder):
            module = rep_encoder.single_frame_encoder.network.shared_network
        else:
            raise NotImplementedError(f'The script does not support interpreting the current type of '
                                      f'encoder {type(rep_encoder)}.')
        return module[layer_idx]
    elif module_name == 'decoder':
        return network.policy.action_net


@interp_ex.main
def run(log_dir, chosen_algo, layer_kwargs, save_video, filename, 
        dataset_configs, save_image):
    # setup environment & dataset
    datasets, combined_meta = auto_env.load_wds_datasets(configs=dataset_configs)
    observation_space = combined_meta['observation_space']

    network = prepare_network(combined_meta)
    images, labels = process_data()

    log_dir = interp_ex.observers[0].dir if log_dir == 'default' else log_dir
    filename = chosen_algo if filename == 'default' else filename

    if save_video:
        video_writer = TensorFrameWriter(f"{log_dir}/{filename}.mp4",
                                         'RGB',
                                         fps=8,
                                         adjust_axis=False,
                                         make_grid=False)

    for itr, (tensor_image, label) in enumerate(zip(images, labels)):
        # Get policy prediction
        tensor_image = tensor_image.contiguous()
        interp_algo_func = interp_algos.get(chosen_algo)

        if 'layer' in chosen_algo:
            module, idx = layer_kwargs[chosen_algo]['module'], \
                          layer_kwargs[chosen_algo]['layer_idx']
            chosen_layer = choose_layer(network, module, idx)

        interpreted_img = interp_algo_func(network, tensor_image, label)  # shape (600, 600, 3)

        if save_video:
            video_writer.add_tensor(preprocess_obs(interpreted_img,
                                                   observation_space,
                                                   normalize_images=True))
        if save_image:
            save_img(interpreted_img,
                     save_name=f'{chosen_algo}_{itr}',
                     save_dir=log_dir,
                     observation_space=observation_space)
        plt.close('all')

        # if layer_gradxact:
        #     module, idx = layer_kwargs['layer_gradxact']['module'], \
        #                   layer_kwargs['layer_gradxact']['layer_idx']
        #     chosen_layer = choose_layer(network, module, idx)
        #     layer_act_(network, chosen_layer, LayerGradientXActivation, 'layer_GradXActivation',
        #                img, log_dir, show_imgs=show_imgs, attr_kwargs={'target': label})
        #
        # if layer_activation:
        #     module, idx = layer_kwargs['layer_activation']['module'], \
        #                   layer_kwargs['layer_activation']['layer_idx']
        #     chosen_layer = choose_layer(network, module, idx)
        #     layer_act_(network, chosen_layer, LayerActivation, 'layer_Activation',
        #                img, log_dir, show_imgs=show_imgs, attr_kwargs={})
    if save_video:
        video_writer.close()


if __name__ == '__main__':
    sacred.SETTINGS['CAPTURE_MODE'] = 'no'
    interp_ex.observers.append(FileStorageObserver('runs/interpret_runs'))
    interp_ex.run_commandline()
