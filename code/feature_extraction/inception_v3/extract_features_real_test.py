import numpy as np
import h5py
import os
import pandas as pd
from tqdm import tqdm
from inception import inception_model as inception
from inception import ops, scopes
import tensorflow as tf
from sklearn.neighbors import KNeighborsClassifier
from sklearn.metrics import confusion_matrix
from itertools import chain

script_path = os.path.abspath(__file__)
script_dir = os.path.dirname(script_path)
proj_root = os.path.join(script_dir, os.pardir, os.pardir, os.pardir)

processed_path = os.path.join(proj_root, 'input/')
features_path = os.path.join(proj_root, 'input/')

base_file_name = 'test_images'

info_csv = pd.read_csv(os.path.join(processed_path, '%s.csv' % base_file_name))

images = h5py.File(os.path.join(processed_path,
                                "%s.hdf5" % base_file_name),
                   "r")['images']

use_channel_as_image = True
batch_size = 128

input_placeholder = tf.placeholder(tf.float32,
                                   shape=(None,) + images.shape[1:])
# TODO: Check whether this is correct
inception_input = (input_placeholder - 0.5) * 2.
#inception_input = input_placeholder
# Parameters for BatchNorm.
batch_norm_params = {
    # Decay for the moving averages.
    'decay': 0.9997,
    # epsilon to prevent 0s in variance.
    'epsilon': 0.001,
}
# Set weight_decay for weights in Conv and FC layers.
with scopes.arg_scope([ops.conv2d, ops.fc], weight_decay=0.00004):
    with scopes.arg_scope([ops.conv2d],
                          stddev=0.1,
                          activation=tf.nn.relu,
                          batch_norm_params=batch_norm_params):
        logits, endpoints = inception.inception_v3(
            inception_input,
            dropout_keep_prob=0.8,
            num_classes=1001,
            is_training=False,
            restore_logits=True,
            scope=None)
variable_averages = tf.train.ExponentialMovingAverage(0.9999)
variables_to_restore = variable_averages.variables_to_restore()
saver = tf.train.Saver(variables_to_restore)

with tf.Session() as sess:
    print('Loading Inception-v3 model')
    saver.restore(sess, 'inception-v3/model.ckpt-157585')
    features = tf.get_default_graph(). \
        get_tensor_by_name("inception_v3/logits/flatten/Reshape:0")

    print('Processing images')
    pbar = tqdm(total=len(images))
    feature_columns = ['feature%d' % i for i in xrange(features.get_shape()[1])]
    if use_channel_as_image:
        feature_columns = (['chan0_feature%d' % i for i in xrange(features.get_shape()[1])]
                           + ['chan1_feature%d' % i for i in xrange(features.get_shape()[1])]
                           + ['chan2_feature%d' % i for i in xrange(features.get_shape()[1])])
    tmp_feat_df = np.zeros((len(images), len(feature_columns)))
    for idx in xrange(0, images.shape[0], batch_size):
        tmp_imgs = np.array(images[idx:idx+batch_size, :])
        if use_channel_as_image:
            for ch_idx in xrange(3):
                input_data = tmp_imgs[:, :, :, ch_idx][:, :, :, np.newaxis].repeat(3, axis=-1)
                print 'channel %d:' % ch_idx
                print input_data.shape
                print input_data.max()
                print input_data.min()
                print input_data.mean()
                extracted_features = \
                    sess.run(features,
                             feed_dict={input_placeholder: input_data})
                start_feature_idx = ch_idx * extracted_features.shape[1]
                end_feature_idx = (ch_idx + 1) * extracted_features.shape[1]
                tmp_feat_df[idx:idx+batch_size,
                            start_feature_idx:end_feature_idx] = extracted_features
                print extracted_features.max()
                print extracted_features.min()
                print extracted_features.mean()

        else:
            input_data = tmp_imgs
            extracted_features = \
                sess.run(features,
                         feed_dict={input_placeholder: input_data})
            tmp_feat_df[idx:idx+batch_size] = extracted_features
        pbar.update(batch_size)

    if idx + 1 < images.shape[0]:
        tmp_imgs = np.array(images[idx:, :])
        if use_channel_as_image:
            for ch_idx in xrange(3):
                input_data = tmp_imgs[:, :, :, ch_idx][:, :, :, np.newaxis].repeat(3, axis=-1)
                extracted_features = \
                    sess.run(features,
                             feed_dict={input_placeholder: input_data})
                start_feature_idx = ch_idx * extracted_features.shape[1]
                end_feature_idx = (ch_idx + 1) * extracted_features.shape[1]
                tmp_feat_df[idx:,
                            start_feature_idx:end_feature_idx] = extracted_features
        else:
            input_data = tmp_imgs
            extracted_features = \
                sess.run(features,
                         feed_dict={input_placeholder: input_data})
            tmp_feat_df[idx:idx+batch_size] = extracted_features
    info_csv = info_csv.loc[0:len(tmp_feat_df)]
    feature_csv = pd.concat([info_csv, pd.DataFrame(tmp_feat_df,
                                                    columns=feature_columns)],
                            axis=1)
    pbar.close()

print feature_csv.max()
print feature_csv.min()
print('Processing extracted features')
feature_csv.to_csv(os.path.join(features_path,
                                'inception_%s.csv' % base_file_name))

tmp_features = feature_csv[~(feature_csv['compound'] == 'DMSO')]
tmp_features = tmp_features[~((tmp_features['compound'] == 'taxol')
                              & (tmp_features['compound'] == 0.3))]

compounds = tmp_features['compound'].drop_duplicates()
compounds.sort_values(inplace=True)
unique_moas = tmp_features['moa'].drop_duplicates()
unique_moas.sort_values(inplace=True)

labels = np.array([list(unique_moas).index(elem) for elem
                   in tmp_features['moa']], dtype=int)

tmp_features['labels'] = labels

tmp_groups = tmp_features[['compound', 'concentration', 'labels']
                          + feature_columns].groupby(['compound',
                                                      'concentration'])
lat_reps = tmp_groups.mean().iloc[:, :]
lat_reps['labels'] = tmp_groups['labels'].apply(lambda x: np.unique(x)[0])
lat_reps.to_csv(os.path.join(features_path,
                'inception_reps_%s.csv' % base_file_name))


trueLabels = []

kNNpredLabels = []
svcpredLabels = []
naivepredLabels = []
np.set_printoptions(formatter={'float': '{: 0.3f}'.format},
                    linewidth=150)
kNN = KNeighborsClassifier(n_neighbors=1)
for numC, compound in enumerate(compounds):
    print 'leaving out %s , %d of %d' % (compound, numC,
                                         len(compounds))
    leaveout = [compound]

    mask = lat_reps.index.isin(leaveout, level=0)

    tmp = lat_reps.drop(['labels'], axis=1)

    trainFeatures = tmp[~mask]
    trainLabels = lat_reps[~mask]['labels']

    testFeatures = lat_reps.drop('labels', axis=1)[mask]
    testLabels = lat_reps[mask]['labels']

    trueLabels.append(testLabels)

    kNN.fit(trainFeatures, trainLabels)
    preds = kNN.predict(testFeatures)
    kNNpredLabels.append(preds)
    knnAcc = kNN.score(testFeatures, testLabels)
    print '1NN acc is %.2f %%' % (knnAcc * 100)

print '1NN:'
nn_confMatrix = \
    confusion_matrix(
        np.fromiter(chain.from_iterable(trueLabels),
                    dtype=int),
        np.fromiter(chain.from_iterable(kNNpredLabels),
                    dtype=int))
print nn_confMatrix
nn_acc = np.sum(nn_confMatrix.diagonal()) / np.sum(nn_confMatrix,
                                                   dtype=np.float)
print nn_acc
with open(os.path.join(features_path,
                       'inception_reps_%s.log' % base_file_name),
          'w') as logfile:
    print >>logfile, '1NN:'
    print >>logfile, nn_confMatrix
    print >>logfile, nn_acc
