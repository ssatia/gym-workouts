import gym
import multiprocessing
from network import PolicyNetwork, ValueNetwork
import tensorflow as tf
import threading
from worker import Worker

NUM_ACTIONS = 3

if __name__ == '__main__':
    tf.reset_default_graph()

    with tf.device("/cpu:0"):
        with tf.variable_scope('global'):
            policy_network = PolicyNetwork(NUM_ACTIONS)
            value_network = ValueNetwork()

        num_workers = multiprocessing.cpu_count()
        workers = []
        for i in range(num_workers):
            env = gym.make('Breakout-v0')
            new_worker = Worker(
                'worker_' + str(i),
                env,
                policy_network,
                value_network
            )
            workers.append(new_worker)

    with tf.Session() as sess:
        sess.run(tf.global_variables_initializer())
        coordinator = tf.train.Coordinator()

        worker_threads = []
        for worker in workers:
            work = lambda: worker.run(sess, coordinator)
            thread = threading.Thread(target = work)
            thread.start()
            worker_threads.append(thread)

        coordinator.join(worker_threads)