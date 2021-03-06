import numpy as np
import random
import tensorflow as tf
import os
import sys
from utils import plot_network_output

LOG_DIR = './log/'
A_DIR = './data/trainA/*.jpg'
B_DIR = './data/trainB/*.jpg'

#CHECKPT_FILE = './savedModel_inst_big.ckpt'
CHECKPT_FILE = './savedModel.ckpt'
BATCH_SIZE = 4
LAMBDA = 10
LAMBDA_CYCLE = 10
LEARNING_RATE = 0.001

MAX_ITERATION = 100000
NUM_CRITIC_TRAIN = 4

NUM_THREADS = 2

BETA_1 = 0.5
BETA_2 = 0.9

SUMMARY_PERIOD = 10
SAVE_PERIOD =  100 #10000

IS_TRAINING = True
#=====================================================
# DEFINE OUR INPUT PIPELINE FOR THE A / B IMAGE GROUPS
#=====================================================


def input_pipeline(filenames, batch_size, num_epochs=None, image_size=142, crop_size=256):
    with tf.device('/cpu:0'):
        filenames = tf.train.match_filenames_once(filenames)
        filename_queue = tf.train.string_input_producer(filenames, num_epochs=num_epochs, shuffle=True)
        reader = tf.WholeFileReader()
        filename, value = reader.read(filename_queue)

        image = tf.image.decode_jpeg(value, channels=3)

        processed = tf.image.resize_images(
            image,
            [image_size, image_size],
            tf.image.ResizeMethod.BILINEAR )

        processed = tf.image.random_flip_left_right(processed)
        processed = tf.random_crop(processed, [crop_size, crop_size, 3] )
        # CHANGE TO 'CHW' DATA_FORMAT FOR FASTER GPU PROCESSING
        processed = tf.transpose(processed, [2, 0, 1])
        processed = (tf.cast(processed, tf.float32) - 128.0) / 128.0

        images = tf.train.batch(
            [processed],
            batch_size = batch_size,
            num_threads = NUM_THREADS,
            capacity=batch_size * 5)

    return images

a = input_pipeline(A_DIR, BATCH_SIZE, image_size=282, crop_size=256)
b = input_pipeline(B_DIR, BATCH_SIZE, image_size=282, crop_size=256)

#=====================================================
# DEFINE OUR GENERATOR
#
# NOTE: We need to define additional helper functions
#       to supplement tensorflow:
#       instance_normalization and ResidualBlocks
#=====================================================

def instance_normalization(outputs):
    batch, channels, rows, cols = outputs.get_shape().as_list()
    var_shape = [rows]
    mu, sigma_sq = tf.nn.moments(outputs, [2, 3], keep_dims=True)
    shift = tf.Variable(tf.zeros(var_shape))
    scale = tf.Variable(tf.ones(var_shape))
    epsilon = 1e-3
    normalized = (outputs - mu) / (sigma_sq + epsilon)**(.5)
    return scale * normalized + shift

def ResBlock128(outputs, name=None):
    with tf.variable_scope(name):
        # WE MAY REQUIRED REFLECT PADDING AS IN HERE: https://github.com/vanhuyz/CycleGAN-TensorFlow/blob/master/ops.py
        res1 = tf.layers.conv2d(outputs, filters=128,kernel_size=3, padding='same', data_format='channels_first', name='rb-conv2d-1')

        res1 = instance_normalization(res1)
        res1 = tf.nn.relu(res1)
        res2 = tf.layers.conv2d(res1, filters=128, kernel_size=3, padding='same', data_format='channels_first', name='rb-conv-2d-2')
        return outputs + res2

def build_generator(source, isTraining, reuse=False) :

    batch, channels, image_size, _ = source.get_shape().as_list()

    with tf.variable_scope('generator'):

        # c7s1-32
        outputs = tf.layers.conv2d(source, filters=32, kernel_size=7, strides=1, padding='same',
            data_format='channels_first', name='c7s1-32-prebatch' )
        outputs = tf.layers.batch_normalization(outputs, training=isTraining, reuse=reuse, epsilon=1e-5,
            momentum=0.9, name="c7s1-32")
        outputs = tf.nn.relu(outputs)

        # d64
        outputs = tf.layers.conv2d(outputs, filters=64, kernel_size=3, strides=2, padding='same',
            data_format='channels_first', name='d64-prebatch' )
        outputs = tf.layers.batch_normalization(outputs, training=isTraining, reuse=reuse, epsilon=1e-5,
            momentum=0.9, name="d64")
        outputs = tf.nn.relu(outputs)

        # d128
        outputs = tf.layers.conv2d(outputs, filters=128, kernel_size=3, strides=2, padding='same',
            data_format='channels_first', name='d128-prebatch' )
        outputs = tf.layers.batch_normalization(outputs, training=isTraining, reuse=reuse, epsilon=1e-5,
            momentum=0.9, name="d128")
        outputs = tf.nn.relu(outputs)

        # ADD RESIDUALBLOCKS (9 x R128)
        res1 = ResBlock128(outputs, 'res1')
        res2 = ResBlock128(res1, 'res2')
        res3 = ResBlock128(res2, 'res3')
        res4 = ResBlock128(res3, 'res4')
        res5 = ResBlock128(res4, 'res5')
        res6 = ResBlock128(res5, 'res6')
        res7 = ResBlock128(res6, 'res7')
        res8 = ResBlock128(res7, 'res8')
        res9 = ResBlock128(res8, 'res9')

        # u64
        outputs = tf.layers.conv2d_transpose(res9, filters=64, kernel_size=3, 
            strides=2, padding='same', 
            data_format='channels_first', name='u64-prebatch' )
                
        outputs = tf.layers.batch_normalization(outputs, training=isTraining, reuse=reuse, epsilon=1e-5,
            momentum=0.9, name="u64")
        outputs = tf.nn.relu(outputs)

        # u32
        outputs = tf.layers.conv2d_transpose(outputs, filters=32, kernel_size=3, 
            strides=2, padding='same', 
            data_format='channels_first', name='u32-prebatch' )
        
        outputs = tf.layers.batch_normalization(outputs, training=isTraining, reuse=reuse, epsilon=1e-5,
            momentum=0.9, name="u32")
        outputs = tf.nn.relu(outputs)

        # c7s1-3
        outputs = tf.layers.conv2d(outputs, filters=3, kernel_size=7, padding='same', 
            data_format='channels_first', name='c7s1-3' )
        outputs = tf.nn.tanh(outputs, name='final-tanh')

        return outputs



with tf.variable_scope('generator_A2B') as a_to_b_scope :
    b_generator = build_generator(a, IS_TRAINING)

with tf.variable_scope('generator_B2A') as b_to_a_scope :
    a_generator = build_generator(b, IS_TRAINING)

with tf.variable_scope('generator_B2A',reuse=True) :
    a_identity = build_generator(b_generator, IS_TRAINING, True)

with tf.variable_scope('generator_A2B',reuse=True) :
    b_identity = build_generator(a_generator, IS_TRAINING, True)


#=====================================================
# DEFINE OUR DISCRIMINATOR
#=====================================================
def lrelu(outputs, name="lr"):
    return tf.maximum(outputs, 0.2*outputs, name=name)


def build_discriminator(source, isTraining, reuse=None) :
    _, channels, _, _ = source.get_shape().as_list()

    with tf.variable_scope('discriminator'):

        #c64
        outputs = tf.layers.conv2d(source, filters=64, kernel_size=4, strides=2, padding='same',
            data_format='channels_first', name='c64' )
        outputs = lrelu(outputs, 'c64-lr')
        
        #c128
        outputs = tf.layers.conv2d(outputs, filters=128, kernel_size=4, strides=2, padding='same',
            data_format='channels_first', name='c128-prebatch' )
        outputs = tf.layers.batch_normalization(outputs, training=isTraining, reuse=reuse, epsilon=1e-5,
            momentum=0.9, name="c128")
        outputs = lrelu(outputs, 'c128-lr')
        
        #c256
        outputs = tf.layers.conv2d(outputs, filters=256, kernel_size=4, strides=2, padding='same',
            data_format='channels_first', name='c256-prebatch' )
        outputs = tf.layers.batch_normalization(outputs, training=isTraining, reuse=reuse, epsilon=1e-5,
            momentum=0.9, name="c256")
        outputs = lrelu(outputs, 'c256-lr')
        
        #c512
        outputs = tf.layers.conv2d(outputs, filters=512, kernel_size=4, strides=2, padding='same',
            data_format='channels_first', name='c512-prebatch' )
        outputs = tf.layers.batch_normalization(outputs, training=isTraining, reuse=reuse, epsilon=1e-5,
            momentum=0.9, name="c512")
        outputs = lrelu(outputs, 'c512-lr')

        #c512REPEAT
        outputs = tf.layers.conv2d(outputs, filters=512, kernel_size=4, strides=2, padding='same',
            data_format='channels_first', name='c512REPEAT-prebatch' )
        outputs = tf.layers.batch_normalization(outputs, training=isTraining, reuse=reuse, epsilon=1e-5,
            momentum=0.9, name="c512REPEAT")
        outputs = lrelu(outputs, 'c512REPEAT-lr')

        # FINAL RESHAPE
        outputs = tf.layers.conv2d(outputs, filters=1, kernel_size=1, padding='same',
            data_format='channels_first', name='final_reshape' )

    return outputs

with tf.variable_scope('discriminator_a') as scope:
    alpha = tf.random_uniform(shape=[BATCH_SIZE, 1, 1, 1], minval=0., maxval=1.)
    a_hat = alpha * a + (1.0 - alpha) * a_generator

    # print("alpha.get_shape()", alpha.get_shape())  #4,3,1,1
    # print("a_hat.get_shape()", a_hat.get_shape()) #4, 3, 256, 256

    v_a_real = build_discriminator(a, IS_TRAINING)

    scope.reuse_variables()
    v_a_gen = build_discriminator(a_generator,IS_TRAINING)
    v_a_hat = build_discriminator(a_hat, IS_TRAINING, reuse=True)

with tf.variable_scope('discriminator_b') as scope:
    alpha = tf.random_uniform(shape=[BATCH_SIZE, 1, 1, 1], minval=0., maxval=1.)
    b_hat = alpha * b + (1.0 - alpha) * b_generator

    v_b_real = build_discriminator(b, IS_TRAINING)
    scope.reuse_variables()
    v_b_gen = build_discriminator(b_generator, IS_TRAINING)
    v_b_hat = build_discriminator(b_hat, IS_TRAINING, reuse=True)

disc_vars = [v for v in tf.trainable_variables() if v.name.startswith('discriminator_')]
gen_vars = [v for v in tf.trainable_variables() if v.name.startswith('generator_')]

#=====================================================
# DEFINE OUR LOSS FUNCTION
#=====================================================
d_optimizer = tf.train.AdamOptimizer(LEARNING_RATE, BETA_1, BETA_2)

g_optimizer = tf.train.AdamOptimizer(LEARNING_RATE, BETA_1, BETA_2)

W_a = tf.reduce_mean(v_a_real) - tf.reduce_mean(v_a_gen)
W_b = tf.reduce_mean(v_b_real) - tf.reduce_mean(v_b_gen)
W = W_a + W_b

GP_a = tf.reduce_mean(
    (tf.sqrt(tf.reduce_sum(tf.gradients(v_a_hat, a_hat)[0] ** 2, reduction_indices=[1, 2, 3])) - 1.0) ** 2
)
GP_b = tf.reduce_mean(
    (tf.sqrt(tf.reduce_sum(tf.gradients(v_b_hat, b_hat)[0] ** 2, reduction_indices=[1, 2, 3])) - 1.0) ** 2
)
GP = GP_a + GP_b

loss_c = -1.0 * W + LAMBDA * GP


with tf.variable_scope('d_train'):
        gvs = d_optimizer.compute_gradients(loss_c, var_list=disc_vars)
        train_d_op = d_optimizer.apply_gradients(gvs)

loss_g_a = -1.0 * tf.reduce_mean(v_a_gen)
loss_g_b = -1.0 * tf.reduce_mean(v_b_gen)
loss_g = loss_g_a + loss_g_b

loss_cycle_a = tf.reduce_mean(
    tf.reduce_mean(tf.abs(a - a_identity),reduction_indices=[1,2,3])) # following the paper implementation.(divide by #pixels)
loss_cycle_b = tf.reduce_mean(
    tf.reduce_mean(tf.abs(b - b_identity),reduction_indices=[1,2,3])) # following the paper implementation.(divide by #pixels)
loss_cycle = loss_cycle_a + loss_cycle_b

with tf.variable_scope('g_train') :
    gvs = g_optimizer.compute_gradients(loss_g+LAMBDA_CYCLE*loss_cycle,var_list=gen_vars)
    train_g_op  = g_optimizer.apply_gradients(gvs)

#=====================================================
# SETUP TENSORBOARD
#=====================================================

tf.summary.image('real_a',tf.transpose(a,perm=[0,2,3,1]),max_outputs=10)
tf.summary.image('fake_a',tf.transpose(a_generator, perm=[0,2,3,1]),max_outputs=10)
tf.summary.image('identity_a',tf.transpose(a_identity,perm=[0,2,3,1]),max_outputs=10)
tf.summary.image('real_b',tf.transpose(b,perm=[0,2,3,1]),max_outputs=10)
tf.summary.image('fake_b',tf.transpose(b_generator, perm=[0,2,3,1]),max_outputs=10)
tf.summary.image('identity_b',tf.transpose(b_identity,perm=[0,2,3,1]),max_outputs=10)

tf.summary.scalar('Estimated W',W)
tf.summary.scalar('gradient_penalty',GP)
tf.summary.scalar('loss_g', loss_g)
tf.summary.scalar('loss_cycle', loss_cycle)

# Summary Operations
summary_op = tf.summary.merge_all()

# Saver
saver = tf.train.Saver(max_to_keep = 5)

#=====================================================
# TRAIN OUR MODEL
#=====================================================
sess = tf.Session()
sess.run(tf.local_variables_initializer())
sess.run(tf.global_variables_initializer())


def get_script_path():
    return os.path.dirname(os.path.realpath(sys.argv[0]))

try:
    summary_writer = tf.summary.FileWriter(LOG_DIR, sess.graph)

    coord = tf.train.Coordinator()
    threads = tf.train.start_queue_runners(sess=sess, coord=coord)
    for step in range(MAX_ITERATION) :
        if coord.should_stop():
            break

        for _ in range(NUM_CRITIC_TRAIN):
            _ = sess.run(train_d_op)
        W_eval, GP_eval, loss_g_eval, loss_cycle_eval, _, genratedA, generatedB = sess.run(
          [W,GP,loss_g,loss_cycle,train_g_op, a_generator, b_generator])
        print('%7d : W : %1.6f, GP : %1.6f, Loss G : %1.6f, Loss Cycle : %1.6f'%(
          step,W_eval,GP_eval,loss_g_eval,loss_cycle_eval))


        if( step % SUMMARY_PERIOD == 0 ) :
            summary_str = sess.run(summary_op)
            summary_writer.add_summary(summary_str, step)
            summary_writer.flush()
        if (step > 0 and IS_TRAINING and step % SAVE_PERIOD == 0):
            print("Saving model...")
            saver.save(sess, CHECKPT_FILE)

except Exception as e:
    coord.request_stop(e)
finally:
    coord.request_stop()
    coord.join(threads)
    sess.close()
