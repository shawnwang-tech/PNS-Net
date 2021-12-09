import os
import logging
from datetime import datetime
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.backends.cudnn as cudnn
from torch.utils import data
from config import config
from lib.PNS_Network import PNSNet as Network
from utils.dataloader import get_video_dataset
from utils.utils import clip_gradient, adjust_lr


class CrossEntropyLoss(nn.Module):
    def __init__(self):
        super(CrossEntropyLoss, self).__init__()

    def forward(self, *inputs):
        pred, target = tuple(inputs)
        total_loss = F.binary_cross_entropy(pred.squeeze(), target.squeeze().float())
        return total_loss


def train(train_loader, model, optimizer, epoch, save_path):
    global step
    model.cuda().train()
    loss_all = 0
    epoch_step = 0
    device = torch.device("cuda")
    try:
        for i, (images, gts) in enumerate(train_loader, start=1):
            optimizer.zero_grad()

            images = images.to(device)
            gts = gts.to(device)

            preds = model(images)
            loss = CrossEntropyLoss().to(device)(preds.squeeze().contiguous(), gts.contiguous().view(-1, *(gts.shape[2:])))
            loss.backward()

            clip_gradient(optimizer, config.clip)
            optimizer.step()

            step += 1
            epoch_step += 1
            loss_all += loss.data

            if i % 20 == 0 or i == total_step or i == 1:
                print('{} Epoch [{:03d}/{:03d}], Step [{:04d}/{:04d}], Total_loss: {:.4f}'.
                      format(datetime.now(), epoch, config.finetune_epoches, i, total_step, loss.data))
                logging.info(
                    '[Train Info]:Epoch [{:03d}/{:03d}], Step [{:04d}/{:04d}], Total_loss: {:.4f}'.
                    format(epoch, config.finetune_epoches, i, total_step, loss.data))

        loss_all /= epoch_step
        logging.info('[Train Info]: Epoch [{:03d}/{:03d}], Loss_AVG: {:.4f}'.format(epoch, config.finetune_epoches, loss_all))

        os.makedirs(os.path.join(save_path, "epoch_%d" % (epoch + 1)), exist_ok=True)
        save_root = os.path.join(save_path, "epoch_%d" % (epoch + 1))
        torch.save(model.state_dict(), os.path.join(save_root, "PNS_Finetune.pth"))

    except KeyboardInterrupt:
        print('Keyboard Interrupt: save model and exit.')
        if not os.path.exists(save_path):
            os.makedirs(save_path)
        torch.save(model.state_dict(), save_path + 'Net_epoch_{}.pth'.format(epoch + 1))
        print('Save checkpoints successfully!')
        raise


if __name__ == '__main__':

    model = Network().cuda()

    if config.gpu_id == '0':
        os.environ["CUDA_VISIBLE_DEVICES"] = "0"
        print('USE GPU 0')
    elif config.gpu_id == '1':
        os.environ["CUDA_VISIBLE_DEVICES"] = "1"
        print('USE GPU 1')
    elif config.gpu_id == '0, 1':
        model = nn.DataParallel(model)
        os.environ["CUDA_VISIBLE_DEVICES"] = "0, 1"
        print('USE GPU 0 and 1')

    cudnn.benchmark = True

    if config.pretrain_state_dict is not None:
        model.load_backbone(torch.load(config.pretrain_state_dict, map_location=torch.device('cpu')), logging)
        print('load model from ', config.pretrain_state_dict)

    base_params = [params for name, params in model.named_parameters() if ("temporal_high" in name)]
    finetune_params = [params for name, params in model.named_parameters() if ("temporal_high" not in name)]

    optimizer = torch.optim.Adam([
        {'params': base_params, 'lr': config.base_lr, 'weight_decay': 1e-4, 'name': "base_params"},
        {'params': finetune_params, 'lr': config.finetune_lr, 'weight_decay': 1e-4, 'name': 'finetune_params'}])

    save_path = config.save_path
    if not os.path.exists(save_path):
        os.makedirs(save_path)

    # load data
    print('load data...')
    train_loader =get_video_dataset()
    train_loader = data.DataLoader(dataset=train_loader,
                                   batch_size=config.video_batchsize,
                                   shuffle=True,
                                   num_workers=2,
                                   pin_memory=False)

    total_step = len(train_loader)

    # logging
    logging.basicConfig(filename=save_path + 'log.log',
                        format='[%(asctime)s-%(filename)s-%(levelname)s:%(message)s]',
                        level=logging.INFO, filemode='a', datefmt='%Y-%m-%d %I:%M:%S %p')
    logging.info("Network-Train")
    logging.info('Config: epoch: {}; lr: {}; batchsize: {}; trainsize: {}; clip: {}; decay_rate: {}; '
                 'save_path: {}; decay_epoch: {}'.format(config.finetune_epoches, config.base_lr, config.video_batchsize, config.size, config.clip,
                                                         config.decay_rate, config.save_path, config.decay_epoch))

    step = 0
    best_mae = 1
    best_epoch = 0

    print("Start train...")
    for epoch in range(config.finetune_epoches):
        cur_lr = adjust_lr(optimizer, config.base_lr, epoch, config.decay_rate, config.decay_epoch)
        train(train_loader, model, optimizer, epoch, save_path)
