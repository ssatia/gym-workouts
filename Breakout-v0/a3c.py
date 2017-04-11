import gym
from atari_environment import AtariEnvironment
import multiprocessing
import numpy as np
import tensorflow as tf
import tensorflow.contrib.slim as slim
import threading
from time import sleep

# Look into cost fn constants

class AC_Network():
    def __init__(self, action_size, scope, optimizer):
        with tf.variable_scope(scope):
            HIDDEN_LAYER_1_SIZE = 8
            HIDDEN_LAYER_2_SIZE = 8
            W1 = 0.5
            W2 = 1.0
            W3 = 1e-2

            self.input_layer = tf.placeholder(shape = [None, 4, 84, 84], dtype = tf.float32)
            self.normalized_input = tf.div(tf.to_float(self.input_layer), 255.0)

            self.convolution_layer_1 = slim.conv2d(self.input_layer, 16, 8, 4)
            self.convolution_layer_2 = slim.conv2d(self.convolution_layer_1, 32, 4, 4)
            self.fully_connected_layer_1 = slim.fully_connected(slim.flatten(self.convolution_layer_2), 256, biases_initializer = None, activation_fn = tf.identity)

            self.policy_layer = slim.fully_connected(self.fully_connected_layer_1, action_size, biases_initializer = None, activation_fn = tf.nn.softmax)
            self.value_layer = slim.fully_connected(self.fully_connected_layer_1, 1, biases_initializer = None, activation_fn = None)

            if scope != 'global':
                self.actions = tf.placeholder(shape = [None], dtype = tf.int32)
                self.actions_onehot = tf.one_hot(self.actions, action_size, dtype=tf.float32)
                self.target_values = tf.placeholder(shape = [None], dtype = tf.float32)
                self.advantages = tf.placeholder(shape = [None], dtype = tf.float32)

                # self.value_function_loss = tf.reduce_sum(tf.square(self.target_values - tf.reshape(self.value_layer, [-1])))
                self.value_function_loss = tf.reduce_mean(tf.square(tf.subtract(self.target_values, self.value_layer)))
                # self.policy_loss = - tf.reduce_sum(tf.multiply(tf.transpose(tf.square(self.actions)), self.advantages))
                self.policy_loss = - tf.reduce_sum(tf.multiply(tf.log(self.policy_layer), self.actions_onehot), reduction_indices = 1)
                self.policy_loss = tf.multiply(self.policy_loss, tf.subtract(self.target_values, self.value_layer))
                # self.entropy = - tf.reduce_sum(tf.multiply(self.policy_layer, tf.log(self.policy_layer)))
                self.total_loss = W1 * self.value_function_loss + W2 * self.policy_loss #+ W3 * self.entropy

                train_vars_local = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope)
                self.gradients = tf.gradients(self.total_loss, train_vars_local)

                train_vars_global = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, 'global')
                self.apply_gradients = optimizer.apply_gradients(zip(self.gradients, train_vars_global))


class Worker():
    GAMMA = 0.99

    def __init__(self, number, env, action_size, optimizer):
        RESIZED_WIDTH = 84
        RESIZED_HEIGHT = 84
        AGENT_HISTORY_LENGTH = 4

        self.name = 'worker_' + str(number)
        self.optimizer = optimizer
        self.local_AC_Network = AC_Network(action_size, self.name, self.optimizer)
        self.env = AtariEnvironment(gym_env = env, resized_width = RESIZED_WIDTH, resized_height = RESIZED_HEIGHT, agent_history_length = AGENT_HISTORY_LENGTH)

        global_vars = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, 'global')
        local_vars = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, self.name)
        op_holder = []
        for global_var, local_var in zip(global_vars, local_vars):
            op_holder.append(local_var.assign(global_var))
        self.update_local_network = op_holder

    def discount_rewards(self, rewards):
        discounted_rewards = np.zeros_like(rewards)
        total = 0
        for i in reversed(xrange(0, rewards.size)):
            total = total * self.GAMMA + rewards[i]
            discounted_rewards[i] = total

        return discounted_rewards

    def train(self, sess, rollout, bootstrap_value):
        rollout = np.array(rollout)
        states = rollout[:, 0]
        actions = rollout[:, 1]
        rewards = rollout[:, 2]
        discounted_rewards = self.discount_rewards(rewards)
        next_states = rollout[:, 3]
        values = rollout[:, 4]
        value_plus = np.asarray(values.tolist() + [bootstrap_value])

        advantages = rewards + self.GAMMA * value_plus[1:] - value_plus[:-1]
        advantages = self.discount_rewards(advantages)

        feed_dict = {
            self.local_AC_Network.input_layer: states.tolist(),
            self.local_AC_Network.actions: actions,
            self.local_AC_Network.target_values: discounted_rewards,
            self.local_AC_Network.advantages: advantages
        }

        loss, _ = sess.run([self.local_AC_Network.total_loss, self.local_AC_Network.apply_gradients], feed_dict = feed_dict)

    def work(self, sess, coordinator):
        MAX_EPISODES = 10000
        EPISODE_BUFFER_SIZE = 30

        print "Starting " + self.name

        with sess.as_default() and sess.graph.as_default():
            episode_count = 0
            while not coordinator.should_stop() and episode_count < MAX_EPISODES:
                sess.run(self.update_local_network)
                episode_history = []
                episode_reward = 0
                state = self.env.get_initial_state()
                done = False

                while not done:
                    action_dist, value = sess.run([
                        self.local_AC_Network.policy_layer,
                        self.local_AC_Network.value_layer
                    ],
                    feed_dict = {
                        self.local_AC_Network.input_layer: [state]
                    })

                    action_dist = np.squeeze(action_dist)
                    # print action_dist
                    action = np.random.choice(range(len(action_dist)), p = action_dist)
                    value = np.squeeze(value)
                    next_state, reward, done, _ = self.env.step(action)
                    episode_history.append([state.tolist(), action, reward, next_state.tolist(), value])
                    episode_reward += reward
                    state = next_state

                if len(episode_history) > 0:
                    self.train(sess, episode_history, 0.0)

                print "Name: %s Episode #%d: Reward: %f" %(self.name, episode_count, episode_reward)

                episode_count += 1


if __name__ == '__main__':
    ACTION_SIZE = 3
    ALPHA = 1e-4

    MAX_EPISODES = 10000

    tf.reset_default_graph()

    with tf.device("/cpu:0"):
        optimizer = tf.train.AdamOptimizer(learning_rate = ALPHA)
        master_network = AC_Network(ACTION_SIZE, 'global', optimizer)
        num_workers = multiprocessing.cpu_count()

        workers = []
        for i in range(num_workers):
            env = gym.make('Breakout-v0')
            new_worker = Worker(i, env, ACTION_SIZE, optimizer)
            workers.append(new_worker)

    with tf.Session() as sess:
        coordinator = tf.train.Coordinator()
        sess.run(tf.global_variables_initializer())

        worker_threads = []
        for worker in workers:
            work = lambda: worker.work(sess, coordinator)
            thread = threading.Thread(target = (work))
            thread.start()
            sleep(0.5)
            worker_threads.append(thread)

        coordinator.join(worker_threads)