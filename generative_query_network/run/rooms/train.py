import argparse
import sys
import os
import random
import numpy as np
import cupy as xp
import chainer
import chainer.functions as cf

sys.path.append(os.path.join("..", ".."))
import gqn
from hyper_parameters import HyperParameters
from model import Model


def main():
    dataset = gqn.data.Dataset(args.dataset_path)
    sampler = gqn.data.Sampler(dataset)
    iterator = gqn.data.Iterator(sampler, batch_size=args.batch_size)

    hyperparams = HyperParameters()
    model = Model(hyperparams)
    model.to_gpu()

    for indices in iterator:
        # shape: (batch, views, height, width, channels)
        # range: [-1, 1]
        images, viewpoints = dataset[indices]

        image_size = images.shape[2:4]
        total_views = images.shape[1]

        # sample number of views
        num_views = random.choice(range(total_views))
        query_index = random.choice(range(total_views))

        if num_views > 0:
            observed_images = images[:, :num_views]
            observed_viewpoints = viewpoints[:, :num_views]

            # (batch, views, height, width, channels) -> (batch * views, height, width, channels)
            observed_images = observed_images.reshape(
                (args.batch_size * num_views, ) + observed_images.shape[2:])
            observed_viewpoints = observed_viewpoints.reshape(
                (args.batch_size * num_views, ) + observed_viewpoints.shape[2:])

            # (batch * views, height, width, channels) -> (batch * views, channels, height, width)
            observed_images = observed_images.transpose((0, 3, 1, 2))

            # transfer to gpu
            observed_images = chainer.cuda.to_gpu(observed_images)
            observed_viewpoints = chainer.cuda.to_gpu(observed_viewpoints)

            r = model.representation_network.compute_r(observed_images,
                                                       observed_viewpoints)

            # (batch * views, channels, height, width) -> (batch, views, channels, height, width)
            r = r.reshape((args.batch_size, num_views) + r.shape[1:])
            
            # sum element-wise across views
            r = cf.sum(r, axis=1)
        else:
            r = None

        query_images = images[:, query_index]
        query_viewpoints = viewpoints[:, query_index]

        # (batch * views, height, width, channels) -> (batch * views, channels, height, width)
        query_images = query_images.transpose((0, 3, 1, 2))

        # transfer to gpu
        query_images = chainer.cuda.to_gpu(query_images)
        query_viewpoints = chainer.cuda.to_gpu(query_viewpoints)

        hg_0 = xp.zeros(
            (
                args.batch_size,
                hyperparams.channels_chz,
            ) + hyperparams.chrz_size,
            dtype="float32")
        cg_0 = xp.zeros(
            (
                args.batch_size,
                hyperparams.channels_chz,
            ) + hyperparams.chrz_size,
            dtype="float32")
        u_0 = xp.zeros(
            (
                args.batch_size,
                hyperparams.generator_u_channels,
            ) + image_size,
            dtype="float32")
        he_0 = xp.zeros(
            (
                args.batch_size,
                hyperparams.channels_chz,
            ) + hyperparams.chrz_size,
            dtype="float32")
        ce_0 = xp.zeros(
            (
                args.batch_size,
                hyperparams.channels_chz,
            ) + hyperparams.chrz_size,
            dtype="float32")

        zg_l = model.generation_network.sample_z(hg_0)
        hg_l, cg_l, u_l = model.generation_network.forward_onestep(
            hg_0, cg_0, u_0, zg_l, query_viewpoints, r)
        x = model.generation_network.sample_x(u_l)

        he_l, ce_l = model.inference_network.forward_onestep(
            hg_0, he_0, ce_0, query_images, query_viewpoints, r)
        ze_l = model.inference_network.sample_z(he_l)
        hg_l, cg_l, u_l = model.generation_network.forward_onestep(
            hg_0, cg_0, u_0, ze_l, query_viewpoints, r)

        return


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-path", type=str, default="rooms_dataset")
    parser.add_argument("--batch-size", "-b", type=int, default=32)
    args = parser.parse_args()
    main()