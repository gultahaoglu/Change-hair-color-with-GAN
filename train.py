# -*- coding: utf-8 -*-
"""
Created on Sun Feb 12 22:05:33 2023

@author: Omen
"""

import shutil ## kopyalama için kullanılan 
import os
import matplotlib.pyplot as plt
import pandas as pd
from IPython.display import Image as ImageDisplay
from sklearn.model_selection import train_test_split

import glob
import random

from PIL import Image 
from torch.utils.data import Dataset, DataLoader
import torchvision.transforms as transforms
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
import torch

import time
import datetime
import sys

import numpy as np
import itertools

from tqdm import tqdm_notebook as tqdm
import torchvision.utils as vutils
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from IPython.display import HTML

from torchvision.utils import save_image
import pandas as pd
from IPython.display import Image as ImageDisplay

from datasets import ImageDataset
from models import Generator
from models import Discriminator

from utils import weights_init_normal
from utils import ReplayBuffer
from utils import LambdaLR

from utils import Logger
import warnings
warnings.filterwarnings("ignore")

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

image_dir='./dataset/celeba/images/'
attributes_file='./dataset/celeba/list_attr_celeba.csv'
output_dir='./dataset/preprocessed_dataset_celeba'

data=pd.read_csv(attributes_file)

df_blackHair=data.loc[data['Black_Hair']==1 & (data ['Male']==-1)].sample(n=1000)
df_blond=data.loc[data['Blond_Hair']==1 & (data ['Male']==-1)].sample(n=1000)
                 

domain_A,domain_B=[],[]
for index,row in df_blackHair.iterrows():
    domain_A.append(row['image_id'])
    
for index,row in df_blond.iterrows():
    domain_B.append(row['image_id'])
    
   
    
A_train,A_test=train_test_split(domain_A,test_size=0.01,random_state=42)
B_train,B_test=train_test_split(domain_B,test_size=0.01,random_state=42)


##train dir creation
A_train_dir=os.path.join(output_dir, 'train/A')
B_train_dir=os.path.join(output_dir, 'train/B')

os.makedirs(A_train_dir,exist_ok=True)
os.makedirs(B_train_dir,exist_ok=True)

for imageA, imageB in zip (A_train,B_train):
    shutil.copy(os.path.join(image_dir, imageA), os.path.join(A_train_dir, imageA))
    shutil.copy(os.path.join(image_dir, imageB), os.path.join(B_train_dir, imageB))
    

##test dir creation
A_test_dir=os.path.join(output_dir, 'test/A')
B_test_dir=os.path.join(output_dir, 'test/B')

os.makedirs(A_test_dir,exist_ok=True)
os.makedirs(B_test_dir,exist_ok=True)

for imageA, imageB in zip (A_test,B_test):
    shutil.copy(os.path.join(image_dir, imageA), os.path.join(A_test_dir, imageA))
    shutil.copy(os.path.join(image_dir, imageB), os.path.join(B_test_dir, imageB))    
    

epoch=0
n_epochs=50
batchSize=1
dataroot='./dataset/preprocessed_dataset_celeba/'
lr=0.0002
decay_epoch=3
size=256
input_nc=3
output_nc=3
cuda=True
n_cpu=8


if torch.cuda.is_available() and not cuda:
    print("WARNING: You have a CUDA device, so you should probably run with --cuda")

###### Definition of variables ######
# Networks
netG_A2B = Generator(input_nc, output_nc)
netG_B2A = Generator(output_nc, input_nc)
netD_A = Discriminator(input_nc)
netD_B = Discriminator(output_nc)

if cuda:
    
    netG_A2B.cuda()
    netG_B2A.cuda()
    netD_A.cuda()
    netD_B.cuda()

netG_A2B.apply(weights_init_normal)
netG_B2A.apply(weights_init_normal)
netD_A.apply(weights_init_normal)
netD_B.apply(weights_init_normal)

# Lossess
criterion_GAN = torch.nn.MSELoss()
criterion_cycle = torch.nn.L1Loss()
criterion_identity = torch.nn.L1Loss()

# Optimizers & LR schedulers
optimizer_G = torch.optim.Adam(itertools.chain(netG_A2B.parameters(), netG_B2A.parameters()),
                                lr=lr, betas=(0.5, 0.999))
optimizer_D_A = torch.optim.Adam(netD_A.parameters(), lr=lr, betas=(0.5, 0.999))
optimizer_D_B = torch.optim.Adam(netD_B.parameters(), lr=lr, betas=(0.5, 0.999))

lr_scheduler_G = torch.optim.lr_scheduler.LambdaLR(optimizer_G, lr_lambda=LambdaLR(n_epochs, epoch, decay_epoch).step)
lr_scheduler_D_A = torch.optim.lr_scheduler.LambdaLR(optimizer_D_A, lr_lambda=LambdaLR(n_epochs, epoch, decay_epoch).step)
lr_scheduler_D_B = torch.optim.lr_scheduler.LambdaLR(optimizer_D_B, lr_lambda=LambdaLR(n_epochs, epoch, decay_epoch).step)

# Inputs & targets memory allocation
Tensor = torch.cuda.FloatTensor if cuda else torch.Tensor
input_A = Tensor(batchSize, input_nc, size, size)
input_B = Tensor(batchSize, output_nc, size, size)
target_real = Variable(Tensor(batchSize).fill_(1.0), requires_grad=False)
target_fake = Variable(Tensor(batchSize).fill_(0.0), requires_grad=False)

fake_A_buffer = ReplayBuffer()
fake_B_buffer = ReplayBuffer()

# Dataset loader
transforms_ = [ transforms.Resize(int(size*1.12), Image.BICUBIC), 
                transforms.RandomCrop(size), 
                transforms.RandomHorizontalFlip(),
                transforms.ToTensor(),
                transforms.Normalize((0.5,0.5,0.5), (0.5,0.5,0.5)) ]
dataloader = DataLoader(ImageDataset(dataroot, transforms_=transforms_, unaligned=True), 
                        batch_size=batchSize, shuffle=True, num_workers=n_cpu)

# Loss plot
logger = Logger(n_epochs, len(dataloader))
###################################
G_loss=[]
G_identity_loss=[]
G_gan_loss=[]
G_cycle_loss=[]
D_loss=[]

###### Training ######
pbar=tqdm(range(epoch,n_epochs))
for epoch in pbar:
    for i, batch in enumerate(dataloader):
        # Set model input
        real_A = Variable(input_A.copy_(batch['A']))
        real_B = Variable(input_B.copy_(batch['B']))

        ###### Generators A2B and B2A ######
        optimizer_G.zero_grad()

        # Identity loss
        # G_A2B(B) should equal B if real B is fed
        same_B = netG_A2B(real_B)
        loss_identity_B = criterion_identity(same_B, real_B)*5.0
        # G_B2A(A) should equal A if real A is fed
        same_A = netG_B2A(real_A)
        loss_identity_A = criterion_identity(same_A, real_A)*5.0

        # GAN loss
        fake_B = netG_A2B(real_A)
        pred_fake = netD_B(fake_B)
        loss_GAN_A2B = criterion_GAN(pred_fake, target_real)

        fake_A = netG_B2A(real_B)
        pred_fake = netD_A(fake_A)
        loss_GAN_B2A = criterion_GAN(pred_fake, target_real)

        # Cycle loss
        recovered_A = netG_B2A(fake_B)
        loss_cycle_ABA = criterion_cycle(recovered_A, real_A)*10.0

        recovered_B = netG_A2B(fake_A)
        loss_cycle_BAB = criterion_cycle(recovered_B, real_B)*10.0

        # Total loss
        loss_G = loss_identity_A + loss_identity_B + loss_GAN_A2B + loss_GAN_B2A + loss_cycle_ABA + loss_cycle_BAB
        loss_G.backward()
        
        optimizer_G.step()
        ###################################

        ###### Discriminator A ######
        optimizer_D_A.zero_grad()

        # Real loss
        pred_real = netD_A(real_A)
        loss_D_real = criterion_GAN(pred_real, target_real)

        # Fake loss
        fake_A = fake_A_buffer.push_and_pop(fake_A)
        pred_fake = netD_A(fake_A.detach())
        loss_D_fake = criterion_GAN(pred_fake, target_fake)

        # Total loss
        loss_D_A = (loss_D_real + loss_D_fake)*0.5
        loss_D_A.backward()

        optimizer_D_A.step()
        ###################################

        ###### Discriminator B ######
        optimizer_D_B.zero_grad()

        # Real loss
        pred_real = netD_B(real_B)
        loss_D_real = criterion_GAN(pred_real, target_real)
        
        # Fake loss
        fake_B = fake_B_buffer.push_and_pop(fake_B)
        pred_fake = netD_B(fake_B.detach())
        loss_D_fake = criterion_GAN(pred_fake, target_fake)

        # Total loss
        loss_D_B = (loss_D_real + loss_D_fake)*0.5
        loss_D_B.backward()

        optimizer_D_B.step()
        ###################################

        # Progress report (http://localhost:8097)
        pbar.set_postfix({'loss_G': loss_G, 'loss_G_identity': (loss_identity_A + loss_identity_B), 'loss_G_GAN': (loss_GAN_A2B + loss_GAN_B2A),
                    'loss_G_cycle': (loss_cycle_ABA + loss_cycle_BAB), 'loss_D': (loss_D_A + loss_D_B)}, 
                    images={'real_A': real_A, 'real_B': real_B, 'fake_A': fake_A, 'fake_B': fake_B})
        G_loss.append(loss_G.item())
        G_identity_loss.append(loss_identity_A.item()+loss_identity_B.item())
        G_gan_loss.append(loss_cycle_ABA.item()+loss_cycle_BAB.item())
        D_loss.append(loss_D_A.item()+loss_D_B.item())
        
    # Update learning rates
    lr_scheduler_G.step()
    lr_scheduler_D_A.step()
    lr_scheduler_D_B.step()

    # Save models checkpoints
    torch.save(netG_A2B.state_dict(), 'output/netG_A2B.pth')
    torch.save(netG_B2A.state_dict(), 'output/netG_B2A.pth')
    torch.save(netD_A.state_dict(), 'output/netD_A.pth')
    torch.save(netD_B.state_dict(), 'output/netD_B.pth')
###################################

plt.figure(figsize=(10,5))
plt.title("Generator and Discriminator Losses During Training")
plt.plot(G_loss,label="G")
plt.plot(G_identity_loss,label="G_identity")
plt.plot(G_gan_loss,label="G_GAN")
plt.plot(G_cycle_loss,label="G_cycle")
plt.plot(D_loss,label="D")
plt.xlabel("iteration")
plt.ylabel("Loss")
plt.legend()
plt.show()

         
           
