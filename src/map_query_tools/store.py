"""Read and validate a portable, immutable scene package."""
import hashlib
import json
from pathlib import Path

import numpy as np
from jsonschema import Draft202012Validator
from PIL import Image

SCHEMAS = Path(__file__).resolve().parents[2] / 'schemas'


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def index(rows, key):
    result = {r[key]: r for r in rows}
    require(len(result) == len(rows), f'duplicate {key}')
    return result


def package_path(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    require(not Path(relative).is_absolute() and path.is_relative_to(root),
            f'asset outside package: {relative}')
    return path


class SceneStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.scene = read_json(self.root / 'scene.json')
        Draft202012Validator(read_json(SCHEMAS / 'scene.schema.json')).validate(self.scene)
        for name, key in [('objects', 'object_id'), ('rooms', 'room_id'),
                          ('types', 'type_id'), ('observations', 'observation_id'),
                          ('assets', 'asset_id'), ('feature_spaces', 'space_id')]:
            setattr(self, name, index(self.scene[name], key))
        self.arrays = {}
        self._validate()

    def path(self, asset_id):
        return package_path(self.root, self.assets[asset_id]['path'])

    def _validate(self):
        for aid, asset in self.assets.items():
            path = self.path(aid)
            require(digest(path) == asset['sha256'], f'hash mismatch: {aid}')
            meta = asset['metadata']
            if asset['kind'] == 'image':
                with Image.open(path) as image:
                    require(image.size == (meta['width'], meta['height']), f'image size: {aid}')
                    image.verify()
            elif asset['kind'] == 'pointcloud':
                with np.load(path, allow_pickle=False) as data:
                    points = data['points']
                    require(points.ndim == 2 and points.shape[1] == 3 and
                            np.issubdtype(points.dtype, np.floating) and np.isfinite(points).all(), f'point shape/values: {aid}')
                    require(meta['frame_id'] == self.scene['coordinate_frame']['frame_id'],
                            f'point frame: {aid}')
                    require(len(points) == meta['point_count'] and str(points.dtype) == meta['points_dtype'],
                            f'point metadata: {aid}')
                    if 'colors' in data:
                        require(data['colors'].shape == points.shape and
                                data['colors'].dtype == np.uint8, f'point colors: {aid}')
            else:
                values = np.load(path, allow_pickle=False)
                require(values.ndim == 2 and values.dtype in (np.float16, np.float32)
                        and np.isfinite(values).all(), f'feature values: {aid}')
                require(list(values.shape) == meta['shape'] and str(values.dtype) == meta['dtype'],
                        f'feature metadata: {aid}')
                self.arrays[aid] = values
        background = self.scene['background_asset_id']
        if background is not None:
            self._asset(background, 'pointcloud')
        self._box(self.scene['bounds_xyz'])
        for room in self.rooms.values():
            bounds = room['bounds_xz']
            if bounds is not None:
                require(np.isfinite(bounds).all() and bounds[0] < bounds[2] and
                        bounds[1] < bounds[3], 'invalid room bounds')
            polygon = room['polygon_xz']
            if polygon is not None:
                require(np.isfinite(polygon).all() and polygon[0] == polygon[-1],
                        'invalid room polygon')
        for space in self.feature_spaces.values():
            aid = space['matrix_asset_id']
            self._asset(aid, 'feature_matrix')
            require(self.arrays[aid].shape[1] == space['dim'], 'feature dimension')
            require(self.assets[aid]['metadata']['feature_space_id'] == space['space_id'],
                    'feature space metadata')
        for observation in self.observations.values():
            self._asset(observation['image_asset_id'], 'image')
            if observation['source_kind'] == 'gs_render':
                require(observation['reconstruction_id'] is not None, 'GS reconstruction ID missing')
            meta = self.assets[observation['image_asset_id']]['metadata']
            require((observation['width'], observation['height']) ==
                    (meta['width'], meta['height']), 'observation dimensions')
            if observation['K'] is not None:
                k = np.asarray(observation['K'])
                require(np.isfinite(k).all() and k[0, 0] > 0 and k[1, 1] > 0
                        and np.allclose(k[2], [0, 0, 1]), 'invalid K')
            if observation['camera_to_world'] is not None:
                t = np.asarray(observation['camera_to_world'])
                require(np.isfinite(t).all() and np.allclose(t[3], [0, 0, 0, 1]) and
                        np.allclose(t[:3, :3].T @ t[:3, :3], np.eye(3), atol=1e-5) and
                        np.isclose(np.linalg.det(t[:3, :3]), 1, atol=1e-5) and
                        observation['camera_convention'] == 'opencv_xright_ydown_zforward',
                        'invalid camera pose')
        rows = set()
        for item in self.objects.values():
            self._box(item['bbox'])
            center = (np.array(item['bbox']['min']) + item['bbox']['max']) / 2
            require(np.allclose(center, item['center_xyz']), 'bbox center mismatch')
            require(set(item['room_ids']) <= self.rooms.keys(), 'unknown object room')
            require(set(item['type_ids']) <= self.types.keys(), 'unknown object type')
            require(bool(item['type_ids']), 'object needs explicit type')
            if item['identity_status'] == 'unresolved':
                require(item['entity_id'] is None, 'unreviewed entity binding')
            if item['geometry_asset_id'] is not None:
                self._asset(item['geometry_asset_id'], 'pointcloud')
            for feature in item['feature_refs']:
                space = self.feature_spaces[feature['space_id']]
                key = (feature['space_id'], feature['row'])
                require(key not in rows, 'feature row reused')
                rows.add(key)
                matrix = self.arrays[space['matrix_asset_id']]
                require(feature['row'] < len(matrix), 'feature row out of range')
                if feature['valid']:
                    require(np.linalg.norm(matrix[feature['row']]) > 0, 'zero feature')
            for link in item['observation_links']:
                observation = self.observations[link['observation_id']]
                box = link['crop_xyxy']
                if box is not None:
                    require(0 <= box[0] < box[2] <= observation['width'] and
                            0 <= box[1] < box[3] <= observation['height'], 'crop outside image')
            for link in item['source_links']:
                require(link['target_object_id'] in self.objects, 'unknown source link')

    def _asset(self, asset_id, kind):
        require(asset_id in self.assets and self.assets[asset_id]['kind'] == kind,
                f'unknown/wrong asset: {asset_id}')

    @staticmethod
    def _box(box):
        lo, hi = np.array(box['min']), np.array(box['max'])
        require(np.isfinite([lo, hi]).all() and (lo <= hi).all(), 'invalid bbox')

    def scoped(self, room_ids):
        return [o for o in self.objects.values()
                if not room_ids or set(room_ids).intersection(o['room_ids'])]

    def pointcloud(self, asset_id):
        with np.load(self.path(asset_id), allow_pickle=False) as data:
            return data['points'], data['colors'] if 'colors' in data else None
