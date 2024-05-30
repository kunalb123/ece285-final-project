import torch
from torchvision.datasets import CocoDetection
import numpy as np
import json
import cv2

class LineMODCocoDataset(CocoDetection):
    def __init__(self, root, annFile, modelsPath, transform=None, target_transform=None):
        super(LineMODCocoDataset, self).__init__(root, annFile, transform, target_transform)
        self.models = json.load(open(modelsPath, 'r'))

    def apply_augmentations(self, image):
        # Add Gaussian noise
        noise = np.random.normal(0, 2.0, image.shape).astype(np.float32)
        image = image + noise

        # Random contrast and brightness
        alpha = 1.0 + np.random.uniform(-0.2, 0.2)  # contrast control
        beta = np.random.uniform(-0.2, 0.2) * 255  # brightness control
        image = alpha * image + beta

        # Clip to valid range
        image = np.clip(image, 0, 255).astype(np.uint8)
        return image

    def create_3D_vertices(self, model):
        vertices = np.empty((3, 0))
        x, y, z = model['min_x'], model['min_y'], model['min_z']
        size_x, size_y, size_z = np.array([model['size_x'], 0, 0]).reshape(3, -1),\
                                 np.array([0, model['size_y'], 0]).reshape(3, -1),\
                                 np.array([0, 0, model['size_z']]).reshape(3, -1)
        v1 = np.array([x, y, z]).reshape(3, -1)
        v2 = v1 + size_x
        v3 = v1 + size_y
        v4 = v1 + size_z
        v5 = v1 + size_x + size_y
        v6 = v1 + size_x + size_z
        v7 = v1 + size_y + size_z
        v8 = v1 + size_x + size_y + size_z
        vertices = np.concatenate([v1, v2, v3, v4, v5, v6, v7, v8], axis=1)
        #vertices = np.concatenate([eval(f'v{i}') for i in np.arange(1, 9)], axis=1)
        return vertices

    def project_3D_vertices(self, vertices, target):
        pose = self.extract_pose(target) # 3 x 4
        P_m2c = np.array(target['cam_K']).reshape(3, 3) @ pose
        vertices = np.vstack((vertices, np.ones((1, vertices.shape[1]))))
        homogeneous_2d = P_m2c @ vertices
        pixel_coordinates = homogeneous_2d[:2, :] / homogeneous_2d[2, :]
        return pixel_coordinates
    
    def gaussian_heatmaps(self, center, sigma, size):
        # Define the center and sigma
        center_x, center_y = center[0], center[1]

        # Create a grid of (x, y) coordinates
        y = np.linspace(0, size[0] - 1, size[0])
        x = np.linspace(0, size[1] - 1, size[1])
        x, y = np.meshgrid(x, y)

        # Compute the Gaussian function
        gaussian = np.exp(-((x - center_x)**2 + (y - center_y)**2) / (2 * sigma**2))
        return gaussian
    
    def vector_field(self, vertex, centroid, size):
        grid_height = size[0]
        grid_width = size[1]

        # Create a grid of (x, y) coordinates
        x = np.arange(grid_width)
        y = np.arange(grid_height)
        x, y = np.meshgrid(x, y)

        # Initialize the vector field with zeros (using float type)
        vector_field_x = np.zeros((grid_height, grid_width), dtype=float)
        vector_field_y = np.zeros((grid_height, grid_width), dtype=float)

        # Compute the distance from each pixel to the vertex
        distance = np.sqrt((x - vertex[0])**2 + (y - vertex[1])**2)

        # Set the radius within which the vector components will be computed
        radius = 3

        # Find the indices of pixels within the specified radius
        within_radius = distance <= radius

        # Compute the vector components pointing toward the centroid
        vector_x = centroid[0] - x.astype(float)
        vector_y = centroid[1] - y.astype(float)

        # Normalize the vectors
        magnitude = np.sqrt(vector_x**2 + vector_y**2)
        non_zero = magnitude > 0
        vector_x[non_zero] /= magnitude[non_zero]
        vector_y[non_zero] /= magnitude[non_zero]

        # Set the vector components within the radius
        vector_field_x[within_radius] = vector_x[within_radius]
        vector_field_y[within_radius] = vector_y[within_radius]
        return vector_field_x, vector_field_y

    def generate_ground_truth(self, image, target):
        h, w = image.shape[0] // 8, image.shape[1] // 8
        belief_map = np.zeros((9, h, w), dtype=np.float32)
        vector_field = np.zeros((16, h, w), dtype=np.float32)
        cat = target['category_id']
        model = self.models[str(cat)]
        vertices = self.create_3D_vertices(model)
        projected_vertices = self.project_3D_vertices(vertices, target)

        # Implement the actual logic for generating belief maps and vector fields
        # using 2D Gaussians and normalized vectors as described in the provided info.

        centroid = np.mean(projected_vertices, axis=1)

        for i, (px, py) in enumerate(projected_vertices.T):
            belief_map[i] = self.gaussian_heatmaps((px//8, py//8), 2, (h, w))
            vector_field[2*i], vector_field[2*i+1] = self.vector_field((px//8, py//8), centroid//8, (h, w))
        
        belief_map[8] = self.gaussian_heatmaps(centroid//8, 2, (h, w))

        return belief_map, vector_field, projected_vertices

    def __getitem__(self, index):
        out = super(LineMODCocoDataset, self).__getitem__(index)
        img, target = out[0], out[1][0]
        img = np.array(img)

        if self.transform:
            img = self.transform(img)
        else:
            img = self.apply_augmentations(img)

        belief_map, vector_field, projected_vertices = self.generate_ground_truth(img, target)
        gt_maps = np.concatenate((belief_map, vector_field), axis=0)

        img = torch.from_numpy(img.transpose((2, 0, 1))).float() / 255.0
        gt_maps = torch.from_numpy(gt_maps).float()

        return img, gt_maps

    def extract_pose(self, target):
        # Placeholder for extracting pose information from the target
        # This should be implemented according to the specifics of your dataset
        R, t = np.array(target['cam_R_m2c']), np.array(target['cam_t_m2c'])
        pose = np.hstack([R.reshape(3, 3), t.reshape(3, -1)])
        return pose

if __name__ == '__main__':
    from torch.utils.data import DataLoader
    from torchvision.transforms import ToTensor

    # Paths to your dataset
    root = '/path/to/linemod/test_data'
    annFile = '/path/to/linemod/annotations.json' 

    dataset = LineMODCocoDataset(root, annFile, transform=ToTensor())

    # Create DataLoader
    dataloader = DataLoader(dataset, batch_size=128, shuffle=True, num_workers=4)
