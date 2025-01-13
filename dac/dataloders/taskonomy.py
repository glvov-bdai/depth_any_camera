import os

import numpy as np
import torch
import json
from PIL import Image

from .dataset import BaseDataset, resize_for_input

# TODO: tasknomy has different Fov for different images, need to consider convert to canonical camera for both training and testing

class TaskonomyDataset(BaseDataset):
    min_depth = 0.01
    max_depth = 80
    test_split = "taskonomy_tiny_test.txt"
    train_split = "taskonomy_tiny_train.txt"

    def __init__(
                    
        self,
        test_mode,
        base_path,
        depth_scale=512,
        crop=None,
        benchmark=False,
        augmentations_db={},
        normalize=True,
        tgt_f = 519, # focal length of perspective training data
        fwd_sz = (512, 512),
        visual_debug=False,
        **kwargs,
    ):
        super().__init__(test_mode, base_path, benchmark, normalize)
        self.test_mode = test_mode
        self.depth_scale = depth_scale
        self.crop = crop
        self.tgt_f = tgt_f
        self.fwd_sz = fwd_sz
        self.height = fwd_sz[0]
        self.width = fwd_sz[1]
        self.visual_debug = visual_debug

        # load annotations
        self.load_dataset()
        for k, v in augmentations_db.items():
            setattr(self, k, v)

    def load_dataset(self):
        self.invalid_depth_num = 0
        with open(os.path.join('splits/taskonomy', self.split_file)) as f:
            for line in f:
                img_info = dict()
                if not self.benchmark:  # benchmark test
                    depth_map = line.strip().split(" ")[1]
                    if depth_map == "None":
                        self.invalid_depth_num += 1
                        continue
                    img_info["annotation_filename_depth"] = os.path.join(
                        self.base_path, depth_map
                    )
                img_name = line.strip().split(" ")[0]
                img_info["image_filename"] = os.path.join(self.base_path, img_name)
                if os.path.exists(img_info["image_filename"]) and os.path.exists(img_info["annotation_filename_depth"]):
                    self.dataset.append(img_info)
        print(
            f"Loaded {len(self.dataset)} images. Totally {self.invalid_depth_num} invalid pairs are filtered"
        )

    def __getitem__(self, idx):
        """Get training/test data after pipeline.
        Args:
            idx (int): Index of data.
        Returns:
            dict: Training/test data (with annotation if `test_mode` is set
                False).
        """
        image = np.asarray(
            Image.open(self.dataset[idx]["image_filename"])
        )
        depth = (
            np.asarray(
                Image.open(self.dataset[idx]["annotation_filename_depth"])
            ).astype(np.float32)
            / self.depth_scale
        )
        point_info_path = self.dataset[idx]["image_filename"].replace('domain_rgb', 'domain_point_info').replace('/rgb/', '/point_info/').replace('.png', '.json')
        with open(point_info_path, 'r') as json_file:
            point_info = json.load(json_file)
        focal_length = point_info['resolution']/2/np.tan(point_info['field_of_view_rads']/2)
        
        info = self.dataset[idx].copy()
        # convert to canonical camera for both training and testing
        info["pred_scale_factor"] = focal_length / self.tgt_f
        # info["pred_scale_factor"] = 1.0
        info["camera_intrinsics"] = torch.tensor(
            [
                [self.tgt_f, 0, self.fwd_sz[1] / 2],
                [0, self.tgt_f, self.fwd_sz[0] / 2],
                [0, 0, 1],
            ])
        
        image, depth, pad, pred_scale_factor = resize_for_input(image, depth, self.fwd_sz, info["camera_intrinsics"], [image.shape[0], image.shape[1]], 1.0)
        
        info["pred_scale_factor"] *= pred_scale_factor
        info['pad'] = pad
        if not self.test_mode:
            depth /= info['pred_scale_factor']
        image, gts, info = self.transform(image=image, gts={"depth": depth}, info=info)
        
        if self.visual_debug:
            # visualize image, gts[gt], gts[attn_mask]
            import matplotlib.pyplot as plt
            plt.figure()
            plt.subplot(1, 3, 1)
            plt.imshow((image.permute(1, 2, 0) - image.min()) / (image.max() - image.min()))
            plt.title("Image")
            plt.subplot(1, 3, 2)
            plt.imshow(gts["gt"].squeeze())
            plt.title("Ground Truth")
            plt.subplot(1, 3, 3)
            plt.imshow(gts["mask"].squeeze())
            plt.title("valid Mask")
            plt.show()
        
        if self.test_mode:
            return {"image": image, "gt": gts["gt"], "mask": gts["mask"], "info": info}
        else:
            return {"image": image, "gt": gts["gt"], "mask": gts["mask"]}

    # def get_pointcloud_mask(self, shape):
    #     mask = np.zeros(shape)
    #     height_start, height_end = 45, self.height - 9
    #     width_start, width_end = 41, self.width - 39
    #     mask[height_start:height_end, width_start:width_end] = 1
    #     return mask

    def preprocess_crop(self, image, gts=None, info=None):
        height_start, height_end = 0, self.height
        width_start, width_end = 0, self.width
        image = image[height_start:height_end, width_start:width_end]
        info["camera_intrinsics"][0, 2] = info["camera_intrinsics"][0, 2] - width_start
        info["camera_intrinsics"][1, 2] = info["camera_intrinsics"][1, 2] - height_start

        new_gts = {}
        if "depth" in gts:
            depth = gts["depth"][height_start:height_end, width_start:width_end]
            mask = depth > self.min_depth
            # if self.test_mode:
            mask = np.logical_and(mask, depth < self.max_depth)
                # mask = self.eval_mask(mask)
            mask = mask.astype(np.uint8)
            new_gts["gt"] = depth
            new_gts["mask"] = mask
        return image, new_gts, info

    # def eval_mask(self, valid_mask):
    #     border_mask = np.zeros_like(valid_mask)
    #     border_mask[15:465, 20:620] = 1  # prepared center region
    #     return np.logical_and(valid_mask, border_mask)
