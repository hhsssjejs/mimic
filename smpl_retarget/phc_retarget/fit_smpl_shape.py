import glob
import os
import sys
import pdb
import os.path as osp
sys.path.append(os.getcwd())

from smpl_sim.utils import torch_utils
from smpl_sim.poselib.skeleton.skeleton3d import SkeletonTree, SkeletonMotion, SkeletonState
from scipy.spatial.transform import Rotation as sRot
import numpy as np
import torch
from smpl_sim.smpllib.smpl_parser import (
    SMPL_Parser,
    SMPLH_Parser,
    SMPLX_Parser, 
)

import joblib
import torch
import torch.nn.functional as F
import math
from smpl_sim.utils.pytorch3d_transforms import axis_angle_to_matrix
from torch.autograd import Variable
from tqdm.notebook import tqdm
from smpl_sim.smpllib.smpl_joint_names import SMPL_MUJOCO_NAMES, SMPL_BONE_ORDER_NAMES, SMPLH_BONE_ORDER_NAMES, SMPLH_MUJOCO_NAMES
from motion_source.utils.torch_humanoid_batch import Humanoid_Batch
from easydict import EasyDict
import hydra
from omegaconf import DictConfig, OmegaConf
from tqdm import tqdm

@hydra.main(version_base=None, config_path="../../description/robots/cfg", config_name="config")
def main(cfg : DictConfig) -> None:
    
    robot_name = "h1"
    humanoid_fk = Humanoid_Batch(cfg.robot) # load forward kinematics model

    robot_joint_names = humanoid_fk.body_names

    #### Define corresonpdances between h1 and smpl joints
    robot_joint_names_augment = humanoid_fk.body_names_augment 
    robot_joint_pick = [i[0] for i in cfg.robot.joint_matches]
    smpl_joint_pick = [i[1] for i in cfg.robot.joint_matches]
    robot_joint_pick_idx = [ robot_joint_names_augment.index(j) for j in robot_joint_pick]
    smpl_joint_pick_idx = [SMPL_BONE_ORDER_NAMES.index(j) for j in smpl_joint_pick]

    #### Preparing fitting varialbes
    device = torch.device("cpu")
    pose_aa_robot = np.repeat(np.repeat(sRot.identity().as_rotvec()[None, None, None, ], humanoid_fk.num_bodies , axis = 2), 1, axis = 1)
    pose_aa_robot = torch.from_numpy(pose_aa_robot).float()
    
    ###### prepare SMPL default pause for H1
    pose_aa_stand = np.zeros((1, 72))
    pose_aa_stand = pose_aa_stand.reshape(-1, 24, 3)
    
    for modifiers in cfg.robot.smpl_pose_modifier:
        modifier_key = list(modifiers.keys())[0]
        modifier_value = list(modifiers.values())[0]
        pose_aa_stand[:, SMPL_BONE_ORDER_NAMES.index(modifier_key)] = sRot.from_euler("xyz", eval(modifier_value),  degrees = False).as_rotvec()

    pose_aa_stand = torch.from_numpy(pose_aa_stand.reshape(-1, 72))
    smpl_parser_n = SMPL_Parser(model_path="./smpl_model/smpl", gender="neutral")

    ###### Shape fitting
    trans = torch.zeros([1, 3])
    beta = torch.zeros([1, 10])
    verts, joints = smpl_parser_n.get_joints_verts(pose_aa_stand, beta , trans)
    offset = joints[:, 0] - trans
    root_trans_offset = trans + offset

    fk_return = humanoid_fk.fk_batch(pose_aa_robot[None, ], root_trans_offset[None, 0:1])
    

    shape_new = Variable(torch.zeros([1, 10]).to(device), requires_grad=True)
    scale = Variable(torch.ones([1]).to(device), requires_grad=True)
    optimizer_shape = torch.optim.Adam([shape_new, scale],lr=0.1)
    
    pbar = tqdm(range(1000))
    for iteration in pbar:
        verts, joints = smpl_parser_n.get_joints_verts(pose_aa_stand, shape_new, trans[0:1])
        root_pos = joints[:, 0]
        joints = (joints - joints[:, 0]) * scale + root_pos
        if len(cfg.robot.extend_config) > 0:
            diff = fk_return.global_translation_extend[:, :, robot_joint_pick_idx] - joints[:, smpl_joint_pick_idx]
        else:
            diff = fk_return.global_translation[:, :, robot_joint_pick_idx] - joints[:, smpl_joint_pick_idx]

        loss_g = diff.norm(dim = -1).mean() 
        loss = loss_g
        pbar.set_description_str(f"{iteration} - Loss: {loss.item() * 1000}")

        optimizer_shape.zero_grad()
        loss.backward()
        optimizer_shape.step()
    if cfg.get("vis", False):
        from mpl_toolkits.mplot3d import Axes3D  # noqa: F401 unused import
        import matplotlib.pyplot as plt
        
        j3d = fk_return.global_translation_extend[0, :, :, :].detach().numpy()
        j3d = j3d - j3d[:, 0:1]
        j3d_joints = joints.detach().numpy()
        j3d_joints = j3d_joints - j3d_joints[:, 0:1]
        idx = 0
        fig = plt.figure()
        ax = fig.add_subplot(111, projection='3d')
        ax.view_init(90, 0)
        ax.scatter(j3d[idx, :,0], j3d[idx, :,1], j3d[idx, :,2])
        ax.scatter(j3d_joints[idx, :,0], j3d_joints[idx, :,1], j3d_joints[idx, :,2])

        ax.set_xlabel('X Label')
        ax.set_ylabel('Y Label')
        ax.set_zlabel('Z Label')
        drange = 1
        ax.set_xlim(-drange, drange)
        ax.set_ylim(-drange, drange)
        ax.set_zlim(-drange, drange)
        plt.show()

    os.makedirs(f"./retargeted_motion_data/phc", exist_ok=True)
    joblib.dump((shape_new.detach(), scale), f"./retargeted_motion_data/phc/shape_optimized_v1.pkl") # V2 has hip joints


if __name__ == "__main__":
    main()
