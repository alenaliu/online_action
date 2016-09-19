
import numpy
import logging
import theano
import theano.tensor as TT
import theano.tensor.nnet
import random
import h5py
from sparnn.utils import *

logger = logging.getLogger(__name__)

'''
AdvancedVideoIterator is the iterator for large video data

1. Data Format

The data it iterates from contains these attributes:

input data:  4-dimensional numpy array, (Frame, FeatureDim, Row, Col)
        or   2-dimensional numpy array, (Frame, FeatureDim)
input label: 1-dimensional numpy array, (Frame,)
        or   2-dimensional numpy array, (Frame, Label) for multi-label output

2. Batch Format

input_batch:  5-dimensional numpy array, (Timestep, Minibatch, FeatureDim, Row, Col)
         or   3-dimensional numpy array, (Timestep, Minibatch, FeatureDim)
output_batch: 2-dimensional numpy array, (Timestep, Minibatch,)
         or   3-dimensional numpy array, (Timestep, Minibatch, Label) for multi-label output

3. About Mask

The VideoIterator class will automatically generate input/output mask if set the `use_mask` flag.
The mask has 2 dims, (Timestep, Minibatch), all elements are either 0 or 1

'''


class AdvancedVideoIterator(object):
    def __init__(self, iterator_param):
        self.name = iterator_param['name']
        self.use_mask = iterator_param.get('use_mask', None)
        self.input_data_type = iterator_param.get('input_data_type', theano.config.floatX)
        self.output_data_type = iterator_param.get('output_data_type', theano.config.floatX)
        self.minibatch_size = iterator_param['minibatch_size']
        self.is_output_multilabel =  iterator_param['is_output_multilabel']
        self.one_hot_label =  iterator_param['one_hot_label']
        
        self.dataset = iterator_param['dataset']
        self.data_file = iterator_param['data_file']
        self.num_frames_file = iterator_param['num_frames_file']
        self.labels_file = iterator_param['labels_file']
        self.vid_name_file = iterator_param['vid_name_file']
        self.dataset_name = iterator_param['dataset_name']

        self.reshape = iterator_param.get('reshape', False)

        self.seq_length = iterator_param['seq_length']
        self.seq_stride = iterator_param['seq_stride']
        self.seq_fps = iterator_param['seq_fps']
        self.seq_skip = int(30.0/self.seq_fps)

        self.rng = iterator_param['rng']

        self.data = {}
        self.indices = {}
        self.current_position = 0
        self.current_batch_size = 0
        self.current_batch_indices = []

        self.load()

    def load(self):

        # load data
        self.data = self.data_file

        # load labels
        if self.is_output_multilabel:
            init_labels = self.get_map_labels(self.labels_file) # multi class labels for mAP
        else:
            init_labels = self.get_labels(self.labels_file)     # labels

        # load number of frames
        num_frames = []                                         # number of frames in each example
        for line in open(self.num_frames_file):
            num_frames.append(int(line.strip()))
        assert len(num_frames) == len(init_labels)
        self.num_videos = len(init_labels)

        # load video file names
        self.video_names = []
        for line in open(self.vid_name_file):
            self.video_names.append(line.strip())
        assert len(self.video_names) == self.num_videos

        # set up dataset
        self.dataset_size = 0

        frame_local_indices = []
        frame_indices = []
        video_indices = []
        labels = []
        lengths = []
        start = 0
        for v, f in enumerate(num_frames):
            end = start + f - self.seq_length*self.seq_skip + 1
            if end <= start: # short length sequences also selected
                end = start+1
            seqs = range(start, end, self.seq_stride)
            if seqs[-1] != (end-1):
                seqs.append(end-1)
            frame_indices.extend(seqs)

            for i in seqs:
                frame_local_indices.append(i-start)
                video_indices.append(v)
                labels.append(init_labels[v])
                lengths.append(num_frames[v])
            start += f
        self.dataset_size = len(frame_indices)
        print 'Dataset size', self.dataset_size

        assert len(frame_local_indices) == len(labels) == len(lengths)
        self.frame_local_indices = numpy.array(frame_local_indices)   # indices of sequence beginnings within the video
        self.frame_indices = numpy.array(frame_indices)   # indices of sequence beginnings
        self.video_indices = numpy.array(video_indices)   # indices of video sequence from
        self.labels = numpy.array(labels)
        self.lengths = numpy.array(lengths)
        self.num_frames = numpy.array(num_frames)
        self.vid_boundary = numpy.array(num_frames).cumsum()

        # data statistics
        self.data_dims = h5py.File('%s/%s.h5' % (self.data,self.video_names[0]), 'r')[self.dataset_name].shape[1:]
        print 'Data dim', self.data_dims
        if self.is_output_multilabel:
            self.label_dims = self.labels.shape[1:]
        else:
            self.label_dims = (numpy.unique(self.labels).size,)
        print 'Label dim', self.label_dims

        self.check_data()

    def get_labels(self, filename):
        labels = []
        if filename != '':
            for line in open(filename,'r'):
                labels.append(int(line.strip()))
        return labels

    def get_map_labels(self, filename):
        labels = []
        if filename != '':
            for line in open(filename,'r'):
                labels.append([int(x) for x in line.split(',')])
        return labels

    def check_data(self):
        #assert 2 == self.data.ndim or 4 == self.data.ndim
        return

    def total(self):
        return self.dataset_size

    def begin(self, do_shuffle=True):
        self.indices = numpy.arange(self.total(), dtype="int32")
        if do_shuffle:
            self.rng.shuffle(self.indices)
        self.current_position = 0
        self.current_batch_size = self.minibatch_size if self.current_position \
                                                         + self.minibatch_size <= self.total() else self.total() - self.current_position
        self.current_batch_indices = self.indices[self.current_position:self.current_position + self.current_batch_size]

    def next(self):
        self.current_position += self.current_batch_size
        if self.no_batch_left():
            return None
        self.current_batch_size = self.minibatch_size if self.current_position \
                                                         + self.minibatch_size <= self.total() else self.total() - self.current_position
        self.current_batch_indices = self.indices[self.current_position:self.current_position + self.current_batch_size]

    def no_batch_left(self):
        if self.current_position >= self.total():
            return True
        else:
            return False

    def get_batch(self):
        if self.no_batch_left():
            # TODO Use Log!
            logger.error(
                "There is no batch left in " + self.name + ". Consider to use iterators.begin() to rescan from " \
                                                           "the beginning of the iterators")
            return None
        input_batch = numpy.zeros(
            (self.seq_length, self.minibatch_size) + tuple(self.data_dims)).astype(
             self.input_data_type)
        mask = numpy.zeros((self.seq_length, self.minibatch_size)).astype(
                            theano.config.floatX) if self.use_mask else None
        
        if self.is_output_multilabel:
            output_batch = numpy.zeros((self.seq_length, self.minibatch_size) + tuple(self.label_dims)).astype(
                                        self.output_data_type)
        elif self.one_hot_label:
            output_batch = numpy.zeros((self.seq_length, self.minibatch_size)).astype(
                                        self.output_data_type)
        else:
            output_batch = numpy.zeros((self.seq_length, self.minibatch_size) + tuple(self.label_dims)).astype(
                                        self.output_data_type)

        data = None
        vid_ind_prev = -1
        for i in range(self.current_batch_size):
            batch_ind = self.current_batch_indices[i]

            start = self.frame_local_indices[batch_ind]
            frame_ind = self.frame_indices[batch_ind]
            vid_ind = self.video_indices[batch_ind]
            label = self.labels[batch_ind]
            length= self.lengths[batch_ind]
            end = start + self.seq_length * self.seq_skip

            # load data for current video
            if vid_ind != vid_ind_prev:
                data = h5py.File('%s/%s.h5' % (self.data,self.video_names[vid_ind]), 'r')[self.dataset_name]

            if length >= self.seq_length*self.seq_skip:
                input_batch[:, i, :] = data[start:end:self.seq_skip, :]
            else:
                n = 1 + int((length-1)/self.seq_skip)
                input_batch[:n, i, :] = data[start:start+length:self.seq_skip, :]
                input_batch[n:, i, :] = numpy.tile(input_batch[n-1, i, :], (self.seq_length-n,) + ((1,) * len(self.data_dims)))

            if self.is_output_multilabel:
                output_batch[:, i, :] = numpy.tile(label, (self.seq_length,1))
            elif self.one_hot_label:
                output_batch[:, i] = numpy.tile(label, (1,self.seq_length))
            else:
                output_batch[:, i, label] = 1.

            vid_ind_prev = vid_ind

        # only for testing, will change in the future
        if self.reshape:
            input_batch = input_batch.reshape([input_batch.shape[0], input_batch.shape[1],
                                               input_batch.shape[2], input_batch.shape[3]*input_batch.shape[4]])
        # input_batch = input_batch.reshape([input_batch.shape[0], input_batch.shape[1], 49, 1024])
        # input_batch = input_batch.transpose((0,1,3,2))

        if self.use_mask:
            mask[:, :self.current_batch_size] = 1.
        input_batch = input_batch.astype(self.input_data_type)
        output_batch = output_batch.astype(self.output_data_type)

        if self.use_mask:
            return [input_batch, mask, output_batch]
        else:
            return [input_batch, output_batch]

    def print_stat(self):
        logger.info("Iterator Name: " + self.name)
        logger.info("   Dataset: " + self.dataset)
        logger.info("   Minibatch Size: " + str(self.minibatch_size))
        logger.info("   Use Mask: " + str(self.use_mask))
        logger.info("   Input Data Type: " + str(self.input_data_type))
        logger.info("   Output Data Type: " + str(self.output_data_type))
        logger.info("   Is Output Multi Label: " + str(self.is_output_multilabel))

def main():
    exit()

if __name__ == '__main__':
    main()
