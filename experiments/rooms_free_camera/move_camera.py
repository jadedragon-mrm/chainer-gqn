import argparse
import math
import time
import sys
import os
import random

import matplotlib.pyplot as plt
import matplotlib.animation as animation
import chainer
import chainer.functions as cf
import cupy
import numpy as np
from chainer.backends import cuda

sys.path.append(".")
import gqn
from gqn.preprocessing import preprocess_images, make_uint8
from hyperparams import HyperParameters
from model import Model


def to_gpu(array):
    if isinstance(array, np.ndarray):
        return cuda.to_gpu(array)
    return array


def make_query_viewpoint(eye, center, batch_size, xp):
    yaw = gqn.math.yaw(eye, center)
    pitch = gqn.math.pitch(eye, center)
    query_viewpoints = xp.array(
        (eye[0], eye[1], eye[2], math.cos(yaw), math.sin(yaw), math.cos(pitch),
         math.sin(pitch)),
        dtype=xp.float32)
    query_viewpoints = xp.broadcast_to(query_viewpoints,
                                       (batch_size, ) + query_viewpoints.shape)
    return query_viewpoints


def interpolate(x, y, a):
    z = (
        y[0] * a + x[0] * (1.0 - a),
        y[1] * a + x[1] * (1.0 - a),
        y[2] * a + x[2] * (1.0 - a),
    )
    return z


def rotate_query_viewpoint(angle_rad, batch_size, xp):
    eye_radius = 3
    eye = (eye_radius * math.sin(angle_rad), -0.125,
           eye_radius * math.cos(angle_rad))
    center = (0, -0.125, 0)
    yaw = gqn.math.yaw(eye, center)
    pitch = gqn.math.pitch(eye, center)
    query_viewpoints = xp.array(
        (eye[0], eye[1], eye[2], math.cos(yaw), math.sin(yaw), math.cos(pitch),
         math.sin(pitch)),
        dtype=np.float32)
    query_viewpoints = xp.broadcast_to(query_viewpoints,
                                       (batch_size, ) + query_viewpoints.shape)
    return query_viewpoints


def add_annotation(axis, array):
    text = axis.text(-25, -2, "observations", fontsize=18)
    array.append(text)
    text = axis.text(7, -2, "neural rendering", fontsize=18)
    array.append(text)


def main():
    try:
        os.mkdir(args.output_directory)
    except:
        pass

    xp = np
    using_gpu = args.gpu_device >= 0
    if using_gpu:
        cuda.get_device(args.gpu_device).use()
        xp = cupy

    dataset = gqn.data.Dataset(args.dataset_path)

    hyperparams = HyperParameters(snapshot_directory=args.snapshot_path)
    model = Model(hyperparams, snapshot_directory=args.snapshot_path)
    if using_gpu:
        model.to_gpu()

    plt.style.use("dark_background")
    fig = plt.figure(figsize=(10, 5))

    axis_observation_array = []
    axis_observation_array.append(plt.subplot2grid((2, 4), (0, 0)))
    axis_observation_array.append(plt.subplot2grid((2, 4), (0, 1)))
    axis_observation_array.append(plt.subplot2grid((2, 4), (1, 0)))
    axis_observation_array.append(plt.subplot2grid((2, 4), (1, 1)))

    for axis in axis_observation_array:
        axis.axis("off")

    axis_generation = plt.subplot2grid((2, 4), (0, 2), rowspan=2, colspan=2)
    axis_generation.axis("off")

    num_views_per_scene = 4
    num_generation = 1
    total_frames_per_movement = 72

    image_shape = (3, ) + hyperparams.image_size
    blank_image = np.full(image_shape, -0.5)
    file_number = 1

    with chainer.no_backprop_mode():
        for subset in dataset:
            iterator = gqn.data.Iterator(subset, batch_size=1)

            for data_indices in iterator:
                artist_frame_array = []

                observed_image_array = xp.full(
                    (num_views_per_scene, ) + image_shape,
                    -0.5,
                    dtype=np.float32)
                observed_viewpoint_array = xp.zeros(
                    (num_views_per_scene, 7), dtype=np.float32)

                # shape: (batch, views, height, width, channels)
                # range: [-1, 1]
                images, viewpoints = subset[data_indices]

                # (batch, views, height, width, channels) -> (batch, views, channels, height, width)
                images = images.transpose((0, 1, 4, 2, 3)).astype(np.float32)
                images = preprocess_images(images)

                batch_index = 0

                # Generate images without observations
                r = xp.zeros(
                    (
                        num_generation,
                        hyperparams.representation_channels,
                    ) + hyperparams.chrz_size,
                    dtype=np.float32)

                # Generate images with observations
                for m in range(num_views_per_scene):
                    observed_image = images[batch_index, m]
                    observed_viewpoint = viewpoints[batch_index, m]

                    observed_image_array[m] = to_gpu(observed_image)
                    observed_viewpoint_array[m] = to_gpu(observed_viewpoint)

                    r = model.compute_observation_representation(
                        observed_image_array[None, :m + 1],
                        observed_viewpoint_array[None, :m + 1])

                    r = cf.broadcast_to(r, (num_generation, ) + r.shape[1:])

                    grid_size = 8
                    trajectory_length = grid_size / 3

                    eye_start = (-trajectory_length, -0.125, trajectory_length)
                    eye_end = (-trajectory_length, -0.125, -trajectory_length)
                    center_start = (-trajectory_length, -0.125, grid_size / 2)
                    center_end = (-trajectory_length, -0.125, 0)

                    for t in range(total_frames_per_movement):
                        artist_array = []

                        for axis, observed_image in zip(
                                axis_observation_array, observed_image_array):
                            axis_image = axis.imshow(
                                make_uint8(observed_image),
                                interpolation="none",
                                animated=True)
                            artist_array.append(axis_image)

                        interp = t / (total_frames_per_movement - 1)
                        eye = interpolate(eye_start, eye_end, interp)
                        center = interpolate(center_start, center_end, interp)
                        query_viewpoints = make_query_viewpoint(
                            eye, center, num_generation, xp)
                        generated_images = model.generate_image(
                            query_viewpoints, r)

                        image = make_uint8(generated_images[0])
                        axis_image = axis_generation.imshow(
                            image, interpolation="none", animated=True)
                        artist_array.append(axis_image)

                        # plt.pause(1e-8)
                        add_annotation(axis_generation, artist_array)
                        artist_frame_array.append(artist_array)

                    eye_start = (-trajectory_length, -0.125,
                                 -trajectory_length)
                    eye_end = (trajectory_length, -0.125, -trajectory_length)
                    center_start = (-trajectory_length, -0.125, 0)
                    center_end = (trajectory_length, -0.125, 0)

                    for t in range(total_frames_per_movement):
                        artist_array = []

                        for axis, observed_image in zip(
                                axis_observation_array, observed_image_array):
                            axis_image = axis.imshow(
                                make_uint8(observed_image),
                                interpolation="none",
                                animated=True)
                            artist_array.append(axis_image)

                        interp = t / (total_frames_per_movement - 1)
                        eye = interpolate(eye_start, eye_end, interp)
                        center = interpolate(center_start, center_end, interp)
                        query_viewpoints = make_query_viewpoint(
                            eye, center, num_generation, xp)
                        generated_images = model.generate_image(
                            query_viewpoints, r)

                        image = make_uint8(generated_images[0])
                        axis_image = axis_generation.imshow(
                            image, interpolation="none", animated=True)
                        artist_array.append(axis_image)

                        # plt.pause(1e-8)
                        add_annotation(axis_generation, artist_array)
                        artist_frame_array.append(artist_array)

                    eye_start = (trajectory_length, -0.125, -trajectory_length)
                    eye_end = (trajectory_length, -0.125, trajectory_length)
                    center_start = (trajectory_length, -0.125, 0)
                    center_end = (trajectory_length, -0.125, grid_size / 2)

                    for t in range(total_frames_per_movement):
                        artist_array = []

                        for axis, observed_image in zip(
                                axis_observation_array, observed_image_array):
                            axis_image = axis.imshow(
                                make_uint8(observed_image),
                                interpolation="none",
                                animated=True)
                            artist_array.append(axis_image)

                        interp = t / (total_frames_per_movement - 1)
                        eye = interpolate(eye_start, eye_end, interp)
                        center = interpolate(center_start, center_end, interp)
                        query_viewpoints = make_query_viewpoint(
                            eye, center, num_generation, xp)
                        generated_images = model.generate_image(
                            query_viewpoints, r)

                        image = make_uint8(generated_images[0])
                        axis_image = axis_generation.imshow(
                            image, interpolation="none", animated=True)
                        artist_array.append(axis_image)

                        # plt.pause(1e-8)
                        add_annotation(axis_generation, artist_array)
                        artist_frame_array.append(artist_array)

                plt.tight_layout()
                plt.subplots_adjust(
                    left=None,
                    bottom=None,
                    right=None,
                    top=None,
                    wspace=0,
                    hspace=0)
                anim = animation.ArtistAnimation(
                    fig,
                    artist_frame_array,
                    interval=1 / 24,
                    blit=True,
                    repeat_delay=0)

                anim.save(
                    "{}/rooms_free_camera_{}.gif".format(
                        args.output_directory, file_number),
                    writer="imagemagick")
                anim.save(
                    "{}/rooms_free_camera_{}.mp4".format(
                        args.output_directory, file_number),
                    writer="ffmpeg",
                    fps=12)
                file_number += 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-path", "-dataset", type=str, required=True)
    parser.add_argument(
        "--snapshot-path", "-snapshot", type=str, required=True)
    parser.add_argument("--gpu-device", "-gpu", type=int, default=0)
    parser.add_argument("--output-directory", "-out", type=str, default="gif")
    args = parser.parse_args()
    main()
