#!/usr/bin/env python3
# Copyright (c) Facebook, Inc. and its affiliates. All rights reserved.
"""
Environments that require short training and evaluation time (<=10min)
can be tested in this file.
"""
import logging
import os
import random
import unittest
from typing import Optional, Tuple

import gym
import numpy as np
import torch
from parameterized import parameterized
from reagent.core.configuration import make_config_class
from reagent.gym.agents.agent import Agent
from reagent.gym.agents.post_step import train_with_replay_buffer_post_step
from reagent.gym.envs.env_factory import EnvFactory
from reagent.gym.runners.gymrunner import run_episode
from reagent.parameters import NormalizationData
from reagent.replay_memory.circular_replay_buffer import ReplayBuffer
from reagent.tensorboardX import SummaryWriterContext
from reagent.test.base.utils import only_continuous_normalizer
from reagent.workflow.model_managers.union import ModelManager__Union
from reagent.workflow.types import RewardOptions
from ruamel.yaml import YAML


logger = logging.getLogger(__name__)
curr_dir = os.path.dirname(__file__)

SEED = 0


def build_normalizer(env):
    if isinstance(env.observation_space, gym.spaces.Box):
        assert (
            len(env.observation_space.shape) == 1
        ), f"{env.observation_space} not supported."
        return {
            "state": NormalizationData(
                dense_normalization_parameters=only_continuous_normalizer(
                    list(range(env.observation_space.shape[0])),
                    env.observation_space.low,
                    env.observation_space.high,
                )
            )
        }
    elif isinstance(env.observation_space, gym.spaces.Dict):
        # assuming env.observation_space is image
        return None
    else:
        raise NotImplementedError(f"{env.observation_space} not supported")


def run_test(
    env: str,
    model: ModelManager__Union,
    replay_memory_size: int,
    train_every_ts: int,
    train_after_ts: int,
    num_episodes: int,
    max_steps: Optional[int],
    passing_score_bar: float,
    use_gpu: bool,
):
    env = EnvFactory.make(env)
    env.seed(SEED)
    env.action_space.seed(SEED)
    normalization = build_normalizer(env)
    logger.info(f"Normalization is {normalization}")

    manager = model.value
    trainer = manager.initialize_trainer(
        use_gpu=use_gpu,
        reward_options=RewardOptions(),
        normalization_data_map=normalization,
    )

    policy = manager.create_policy(False)
    replay_buffer = ReplayBuffer.create_from_env(
        env=env,
        replay_memory_size=replay_memory_size,
        batch_size=trainer.minibatch_size,
    )

    post_step = train_with_replay_buffer_post_step(
        replay_buffer=replay_buffer,
        trainer=trainer,
        training_freq=train_every_ts,
        batch_size=trainer.minibatch_size,
        replay_burnin=train_after_ts,
    )

    agent = Agent.create_for_env(env, policy=policy, post_transition_callback=post_step)

    reward_history = []
    for i in range(num_episodes):
        logger.info(f"running episode {i}")
        ep_reward = run_episode(env=env, agent=agent, max_steps=max_steps)
        reward_history.append(ep_reward)

    assert reward_history[-1] >= passing_score_bar, (
        f"reward after {len(reward_history)} episodes is {reward_history[-1]},"
        f"less than < {passing_score_bar}...\n"
        f"Full reward history: {reward_history}"
    )

    def gym_to_reagent_serving(obs: np.array) -> Tuple[torch.Tensor, torch.Tensor]:
        obs_tensor = torch.tensor(obs).float().unsqueeze(0)
        presence_tensor = torch.ones_like(obs_tensor)
        return (obs_tensor, presence_tensor)

    agent = Agent.create_for_env(
        env,
        policy=manager.create_policy(True),
        post_transition_callback=None,
        obs_preprocessor=gym_to_reagent_serving,
    )
    ep_reward = run_episode(env=env, agent=agent, max_steps=max_steps)
    assert ep_reward >= passing_score_bar, (
        f"Predictor reward is {ep_reward},"
        f"less than < {passing_score_bar}...\n"
        f"Full reward history: {reward_history}"
    )

    return reward_history


def run_from_config(path, use_gpu):
    yaml = YAML(typ="safe")
    with open(path, "r") as f:
        config_dict = yaml.load(f.read())
    config_dict["use_gpu"] = use_gpu

    @make_config_class(run_test)
    class ConfigClass:
        pass

    config = ConfigClass(**config_dict)
    return run_test(**config.asdict())


GYM_TESTS = [
    ("Discrete Dqn Cartpole", "configs/cartpole/discrete_dqn_cartpole_online.yaml"),
    (
        "Discrete Dqn Open Gridworld",
        "configs/open_gridworld/discrete_dqn_open_gridworld.yaml",
    ),
]


class TestGym(unittest.TestCase):
    def setUp(self):
        SummaryWriterContext._reset_globals()
        logging.basicConfig(level=logging.INFO)
        np.random.seed(SEED)
        torch.manual_seed(SEED)
        random.seed(SEED)

    @parameterized.expand(GYM_TESTS)
    def test_gym_cpu(self, name: str, config_path: str):
        reward_history = run_from_config(os.path.join(curr_dir, config_path), False)
        logger.info(f"{name} passes, with reward_history={reward_history}.")

    @parameterized.expand(GYM_TESTS)
    @unittest.skipIf(not torch.cuda.is_available(), "CUDA not available")
    def test_gym_gpu(self, name: str, config_path: str):
        reward_history = run_from_config(os.path.join(curr_dir, config_path), True)
        logger.info(f"{name} passes, with reward_history={reward_history}.")


if __name__ == "__main__":
    unittest.main()
