import os
import math
import random
import functools
import numpy as np
import paddle
import cv2
import io

random.seed(0)
np.random.seed(0)

DATA_DIM = 224

THREAD = 8
BUF_SIZE = 102400

DATA_DIR = './data/ILSVRC2012'

img_mean = np.array([0.485, 0.456, 0.406]).reshape((3, 1, 1))
img_std = np.array([0.229, 0.224, 0.225]).reshape((3, 1, 1))

def rotate_image(img):
    """ rotate_image """
    (h, w) = img.shape[:2]
    center = (w / 2, h / 2)
    angle = np.random.randint(-10, 11)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(img, M, (w, h))
    return rotated

def random_crop(img, size, settings, scale=None, ratio=None):
    """ random_crop """
    lower_scale = settings.lower_scale
    lower_ratio = settings.lower_ratio
    upper_ratio = settings.upper_ratio
    scale = [lower_scale, 1.0] if scale is None else scale
    ratio = [lower_ratio, upper_ratio] if ratio is None else ratio


    aspect_ratio = math.sqrt(np.random.uniform(*ratio))
    w = 1. * aspect_ratio
    h = 1. / aspect_ratio

    bound = min((float(img.shape[0]) / img.shape[1]) / (h**2),
                (float(img.shape[1]) / img.shape[0]) / (w**2))

    scale_max = min(scale[1], bound)
    scale_min = min(scale[0], bound)

    target_area = img.shape[0] * img.shape[1] * np.random.uniform(scale_min,
                                                                  scale_max)
    target_size = math.sqrt(target_area)
    w = int(target_size * w)
    h = int(target_size * h)
    i = np.random.randint(0, img.shape[0] - h + 1)
    j = np.random.randint(0, img.shape[1] - w + 1)

    img = img[i:i + h, j:j + w, :]

    resized = cv2.resize(img, (size, size)
            #, interpolation=cv2.INTER_LANCZOS4
            )
    return resized

def distort_color(img):
    return img

def resize_short(img, target_size):
    """ resize_short """
    percent = float(target_size) / min(img.shape[0], img.shape[1])
    resized_width = int(round(img.shape[1] * percent))
    resized_height = int(round(img.shape[0] * percent))
    resized = cv2.resize(img, (resized_width, resized_height), 
            #interpolation=cv2.INTER_LANCZOS4
            )
    return resized

def crop_image(img, target_size, center):
    """ crop_image """
    height, width = img.shape[:2]
    size = target_size
    if center == True:
        w_start = (width - size) // 2
        h_start = (height - size) // 2
    else:
        w_start = np.random.randint(0, width - size + 1)
        h_start = np.random.randint(0, height - size + 1)
    w_end = w_start + size
    h_end = h_start + size
    img = img[h_start:h_end, w_start:w_end, :]
    return img

def create_mixup_reader(settings, rd): 
    class context:
        tmp_mix = []
        tmp_l1 = []
        tmp_l2 = []
        tmp_lam = []
    
    batch_size = settings.batch_size
    alpha = settings.mixup_alpha
    def fetch_data():
        
        data_list = []
        for i, item in enumerate(rd()):
            data_list.append(item)
            if i % batch_size == batch_size - 1:         
                yield data_list
                data_list =[]
                   
    def mixup_data():
        
        for data_list in fetch_data():
            if alpha > 0.:
                lam = np.random.beta(alpha, alpha)
            else:
                lam = 1.
            l1 = np.array(data_list)
            l2 = np.random.permutation(l1)
            mixed_l = [l1[i][0] * lam + (1 - lam) * l2[i][0] for i in range(len(l1))]
            yield mixed_l, l1, l2, lam
     
    def mixup_reader():
        
        for context.tmp_mix, context.tmp_l1, context.tmp_l2, context.tmp_lam in mixup_data():
            for i in range(len(context.tmp_mix)):
                mixed_l = context.tmp_mix[i]
                l1 = context.tmp_l1[i]
                l2 = context.tmp_l2[i]
                lam = context.tmp_lam
                yield mixed_l, l1[1], l2[1], lam
                
    return mixup_reader

def process_image(
                  sample,
                  settings,
                  mode,
                  color_jitter,
                  rotate,
                  crop_size=224,
                  mean=None,
                  std=None):
    """ process_image """

    mean = [0.485, 0.456, 0.406] if mean is None else mean
    std = [0.229, 0.224, 0.225] if std is None else std

    img_path = sample[0]
    img = cv2.imread(img_path)

    if mode == 'train':
        if rotate:
            img = rotate_image(img)
        if crop_size > 0:
            img = random_crop(img, crop_size,settings)
        if color_jitter:
            img = distort_color(img)
        if np.random.randint(0, 2) == 1:
            img = img[:, ::-1, :]
    else:
        if crop_size > 0:
            target_size = settings.resize_short_size
            img = resize_short(img, target_size)

            img = crop_image(img, target_size=crop_size, center=True)

    img = img[:, :, ::-1].astype('float32').transpose((2, 0, 1)) / 255
    img_mean = np.array(mean).reshape((3, 1, 1))
    img_std = np.array(std).reshape((3, 1, 1))
    img -= img_mean
    img /= img_std

    if mode == 'train' or mode == 'val':
        return (img, sample[1])
    elif mode == 'test':
        return (img, )


def image_mapper(**kwargs):
    """ image_mapper """
    return functools.partial(process_image, **kwargs)


def _reader_creator(settings,
                    file_list,
                    mode,
                    shuffle=False,
                    color_jitter=False,
                    rotate=False,
                    data_dir=DATA_DIR,
                    pass_id_as_seed=0):
    def reader():
        with open(file_list) as flist:
            full_lines = [line.strip() for line in flist]
            if shuffle:
                if pass_id_as_seed:
                    np.random.seed(pass_id_as_seed)
                np.random.shuffle(full_lines)
            if mode == 'train' and os.getenv('PADDLE_TRAINING_ROLE'):
                # distributed mode if the env var `PADDLE_TRAINING_ROLE` exits
                trainer_id = int(os.getenv("PADDLE_TRAINER_ID", "0"))
                trainer_count = int(os.getenv("PADDLE_TRAINERS_NUM", "1"))
                per_node_lines = len(full_lines) // trainer_count
                lines = full_lines[trainer_id * per_node_lines:(trainer_id + 1)
                                   * per_node_lines]
                print(
                    "read images from %d, length: %d, lines length: %d, total: %d"
                    % (trainer_id * per_node_lines, per_node_lines, len(lines),
                       len(full_lines)))
            else:
                lines = full_lines

            for line in lines:
                if mode == 'train' or mode == 'val':
                    img_path, label = line.split()
                    img_path = os.path.join(data_dir, img_path)
                    yield img_path, int(label)
                elif mode == 'test':
                    img_path, label = line.split()
                    img_path = os.path.join(data_dir, img_path)
 
                    yield [img_path]
    crop_size = int(settings.image_shape.split(",")[2])
    image_mapper = functools.partial(
        process_image,
        settings=settings,
        mode=mode,
        color_jitter=color_jitter,
        rotate=rotate,
        crop_size=crop_size)
    reader = paddle.reader.xmap_readers(
        image_mapper, reader, THREAD, BUF_SIZE, order=False)
    return reader

def train(settings, data_dir=DATA_DIR, pass_id_as_seed=0):
    file_list = os.path.join(data_dir, 'train_list.txt')
    reader =  _reader_creator(
        settings,
        file_list,
        'train',
        shuffle=True,
        color_jitter=False,
        rotate=False,
        data_dir=data_dir,
        pass_id_as_seed=pass_id_as_seed,
        )
    if settings.use_mixup == True:
        reader = create_mixup_reader(settings, reader)
    return reader

def val(settings,data_dir=DATA_DIR):
    file_list = os.path.join(data_dir, 'val_list.txt')
    return _reader_creator(settings ,file_list, 'val', shuffle=False, 
            data_dir=data_dir)


def test(settings,data_dir=DATA_DIR):
    file_list = os.path.join(data_dir, 'val_list.txt')
    return _reader_creator(settings, file_list, 'test', shuffle=False,
            data_dir=data_dir)
