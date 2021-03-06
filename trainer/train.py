"""
Train our RNN on bottlecap or prediction files generated from our CNN.
"""
import numpy as np
import tensorflow as tf
import cPickle as pickle
import time, csv, sys, argparse, random, os, glob, re
from tensorflow.python.lib.io import file_io
from subprocess import call, Popen
from keras.layers import Dense, Flatten, Dropout
from keras.layers.recurrent import LSTM, GRU
from keras.models import Sequential, load_model
from keras.optimizers import Adam
import keras.callbacks
from keras.callbacks import TensorBoard, ModelCheckpoint, EarlyStopping, CSVLogger
from keras.utils import np_utils
from datetime import datetime

class CloudCheckpoint(keras.callbacks.Callback):
    def __init__(self, checkpoint_path, output_path, log_name):
        self.checkpoint_path = checkpoint_path
        self.log_name = log_name
        self.output_path = output_path

    def on_epoch_end(self, epoch, logs={}):
        print self.output_path
        print self.log_name
        Popen(['gsutil', '--quiet', 'cp', self.log_name,
            os.path.join(self.output_path, os.path.basename(self.log_name))],
            stdin=None, stdout=None,
            stderr=None, close_fds=True)
        #Popen(['gsutil', 'mv',
        #    os.path.join(self.checkpoint_path, str(epoch) + '.hdf5'),
        #    os.path.join(self.output_path, 'checkpoint.hdf5')],
        #    stdin=None, stdout=None,
        #    stderr=None, close_fds=True)

class Trainer():
    def __init__(self, **kwargs):
        self.job_type = kwargs['job_type']
        self.seq_length = kwargs['seq_length']
        self.batch_size = kwargs['batch_size']
        self.job_dir = kwargs['job_dir']
        if kwargs['unit_forget_bias'] == 'True':
            self.unit_forget_bias = True
        else:
            self.unit_forget_bias = False
        self.dropout = kwargs['dropout']
        self.recurrent_dropout = kwargs['recurrent_dropout']
        self.checkpoint_path = os.path.join('/tmp', 'checkpoints')
        self.log_path = os.path.join('/tmp', 'logs')
        if kwargs['job_type'] == 'cloud':
            self.data_dir = os.path.join('/tmp/', kwargs['data_dir'])
            self.output_path = os.path.join(kwargs['job_dir'],
                    kwargs['job_name'])
            self.job_name = kwargs['job_name']
        else:
            self.data_dir = os.path.join(kwargs['job_dir'],
                    'sequences', kwargs['data_dir'])
            self.output_path = os.path.join(self.job_dir,
                os.environ['JOB_NAME'])
            self.job_name = os.environ['JOB_NAME']
        self.model_structure = kwargs['model_structure']
        self.classes = []
        self.train_set = []
        self.test_set = []

    def train(self):

        nb_epoch = 1000
        timestamp = time.time()
        model_name = "trained-" + self.job_name + ".hdf5"
        early_stopper = EarlyStopping(patience=10)

        # Open files from cloud or locally for data file and training set
        if self.job_type == 'cloud':
            print str(datetime.now()) + " Beginning dataset transfer."
            call(['gsutil', '--quiet', '-m', 'cp', '-r',
                os.path.join(self.job_dir, 'sequences',
                    os.path.basename(self.data_dir)), '/tmp'])
            print str(datetime.now()) + " Dataset transfer complete."
            try:
                os.mkdir(self.checkpoint_path)
                os.mkdir(self.log_path)
            except OSError:
                pass
            log_name = os.path.join(self.log_path,
                    self.job_name + '.log')
            checkpointer = ModelCheckpoint(
                    filepath=os.path.join(self.checkpoint_path,
                    '{epoch}.hdf5'),
                    verbose=1, save_best_only=True)
            saver = CloudCheckpoint(self.checkpoint_path,
                    self.output_path, log_name)
            tb = TensorBoard(log_dir=self.output_path)
            logger = CSVLogger(log_name)
            callbacks = [early_stopper, tb, logger, saver]
            df = file_io.FileIO(os.path.join(self.job_dir,
                'class_list_' + os.path.basename(self.data_dir) + '.csv'), 'r')
        else:
            os.mkdir(self.output_path)
            checkpointer = ModelCheckpoint(
                    filepath=os.path.join(self.output_path,
                    'checkpoint.hdf5'),
                    verbose=1,
                    save_best_only=True)
            tb = TensorBoard(log_dir=self.output_path)
            csv_log_name = os.path.join(self.output_path,
                    self.job_name + '.log')
            csv_logger = CSVLogger(csv_log_name)
            callbacks = [tb, early_stopper, csv_logger]
            df = open(os.path.join(self.job_dir,
                'class_list_' + os.path.basename(self.data_dir) + '.csv'),'r')

        self.classes = df.read().strip('\r\n').lower().split(',')

        print str(len(glob.glob(os.path.join(self.data_dir, '*.npy')))) \
                + " vectors listed in " + self.data_dir + "."

        print str(datetime.now()) + " Reading dataset."
        self.separate_classes()
        steps_per_epoch = (len(self.train_set) + len(self.test_set)) // self.batch_size

        print str(datetime.now()) + " " + str(len(self.classes)) \
                + " classes listed."
        print str(datetime.now()) + " " + str(len(self.train_set)) \
                + " training vectors produced."
        print str(datetime.now()) + " " + str(len(self.test_set)) \
                + " validation vectors produced."

        random.seed(time.time())

        training_gen = self.sequence_generator(self.train_set)
        validation_gen = self.sequence_generator(self.test_set)

        print str(datetime.now()) + " Building model..."

        if self.model_structure == 'lstm':
            rm = self.build_lstm()
        elif self.model_structure == 'gru':
            rm = self.build_gru()

        print str(datetime.now()) + " Starting up fit_generator..."

        hist = rm.fit_generator(
                generator=training_gen,
                steps_per_epoch=steps_per_epoch,
                epochs=nb_epoch,
                verbose=1,
                callbacks=callbacks,
                validation_data=validation_gen,
                validation_steps=10,
                max_queue_size=10)

    
        if self.job_type == 'cloud':
            model_path = os.path.join('/tmp', model_name)
            rm.save(model_path)
            call(['gsutil', 'cp', model_path, self.output_path])
        elif self.job_type == 'local':
            rm.save(os.path.join(self.output_path, model_name))

    def get_class_one_hot(self, class_str):
        label_encoded = self.classes.index(class_str.lower())
        label_hot = np_utils.to_categorical(label_encoded, len(self.classes))
        label_hot = label_hot[0]
        return label_hot

    def separate_classes(self):
        self.train_set = pickle.load(file_io.FileIO(
            os.path.join(self.job_dir,
                self.job_name + '_train.pkl'), 'rb'))
        self.test_set = pickle.load(file_io.FileIO(
            os.path.join(self.job_dir,
                self.job_name + '_test.pkl'), 'rb'))

    def sequence_generator(self, set_list):
        while True:
            X, y = [], []
            for _ in range(self.batch_size):
                sample = random.choice(set_list)
                name = os.path.join(self.data_dir, sample)
                class_name = re.split("^(.*?)(_|[0-9])", sample)[1]
                vector = np.load(name)
                X.append(vector)
                y.append(self.get_class_one_hot(class_name))

            yield np.array(X), np.array(y)

    def build_lstm(self):
        model = Sequential()
        model.add(LSTM(2048,
            return_sequences=True,
            input_shape=(self.seq_length, 2048),
            dropout=self.dropout,
            recurrent_dropout=self.recurrent_dropout,
            unit_forget_bias=self.unit_forget_bias))
        model.add(Flatten())
        model.add(Dense(512, activation='relu'))
        model.add(Dropout(0.5))
        model.add(Dense(len(self.classes), activation='softmax'))
        model.summary()
        optimizer = Adam(lr=1e-6)
        model.compile(loss='categorical_crossentropy',
                optimizer=optimizer,
                metrics=['accuracy','top_k_categorical_accuracy'])
        return model

    def build_gru(self):
        model = Sequential()
        model.add(GRU(2048,
            return_sequences=True,
            input_shape=(self.seq_length, 2048),
            dropout=self.dropout,
            recurrent_dropout=self.recurrent_dropout))
        model.add(Flatten())
        model.add(Dense(512, activation='relu'))
        model.add(Dropout(0.5))
        model.add(Dense(len(self.classes), activation='softmax'))
        model.summary()
        optimizer = Adam(lr=1e-6)
        model.compile(loss='categorical_crossentropy',
                optimizer=optimizer,
                metrics=['accuracy','top_k_categorical_accuracy'])
        return model

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
            description='Run a training job for the lstm')
    parser.add_argument('--job_type',
            help='cloud or local job')
    parser.add_argument('--job_dir',
            help='local or cloud location to get resources and write outputs',
            required=True)
    parser.add_argument('--seq_length',
            type=int,
            help='length of a sequence in frames',
            required=True)
    parser.add_argument('--batch_size',
            default=32,
            type=int,
            help='batch size per step')
    parser.add_argument('--job_name',
            default='',
            help='name of the job')
    parser.add_argument('--dropout',
            type=float,
            default=0.5,
            help='fraction of units to drop in linear state')
    parser.add_argument('--recurrent_dropout',
            type=float,
            default=0.0,
            help='fraction of units to drop in recurrent state')
    parser.add_argument('--unit_forget_bias',
            type=str,
            default='False',
            help='forget gate initialization bias')
    parser.add_argument('--data_dir',
            help='dataset location in directory in `job_dir`/sequences')
    parser.add_argument('--model_structure',
            default='lstm',
            help='use lstm or gru architecture')
    args, unknown = parser.parse_known_args()
    args = args.__dict__

    print "Starting with parameters:"
    print args

    t = Trainer(**args)
    t.train()
