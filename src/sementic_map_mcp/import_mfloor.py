"""Convert the inspected M-floor portable export; never deserialize pickle."""
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from .store import SceneStore, digest, package_path, read_json, require, write_json


def import_mfloor(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    require(not output.exists(), 'output must be a new directory')
    require(not output.is_relative_to(source), 'output must be outside source')
    cg = source / 'CG_portable'
    portable = read_json(cg / 'map.semantic.json')
    require(portable['coordinates'] ==
            {'frame': 'changcheng_originalK2_Yup', 'up': 'y', 'unit': 'm'},
            'unsupported source frame')
    entries = portable['document']['objects']
    source_index = {o['id']: o for o in read_json(source / 'INSTANCE_INDEX.json')}
    prompts = {r['zh']: r['en'] for r in read_json(source / 'CLASS_PROMPTS.json')}
    output.mkdir(parents=True)
    (output / 'assets').mkdir()
    frame = portable['coordinates']['frame']
    assets, objects, observations, vectors = [], [], [], []

    def register(path, kind, metadata):
        asset_id = path.stem
        assets.append(dict(asset_id=asset_id, path=str(path.relative_to(output)),
                           sha256=digest(path), kind=kind, metadata=metadata))
        return asset_id

    def cloud(name, points, colors):
        path = output / 'assets' / (name + '.npz')
        np.savez_compressed(path, points=np.asarray(points, dtype=np.float32),
                            colors=np.round(np.clip(colors, 0, 1) * 255).astype(np.uint8))
        return register(path, 'pointcloud', dict(frame_id=frame, point_count=len(points),
                                                points_dtype='float32', colors_dtype='uint8'))

    def array(item, key):
        spec = item[key]
        relative = spec['$array']
        path = package_path(cg, relative)
        require(digest(path) == portable['files'][relative]['sha256'],
                f'source hash mismatch: {relative}')
        value = np.load(path, allow_pickle=False)
        require(list(value.shape) == spec['shape'] and value.dtype.str == spec['dtype'],
                f'source array mismatch: {relative}')
        return value

    with np.load(package_path(cg, portable['background']['path']), allow_pickle=False) as data:
        require(portable['background']['up'] == 'z', 'unsupported background frame')
        xyz = data['xyz'][:, [0, 2, 1]].copy()
        xyz[:, 2] *= -1
        background = cloud('background', xyz, data['rgb'])
    labels = sorted({o['class_name'][0] for o in entries})
    types = [dict(type_id=f'type-{i:03d}', name=label,
                  aliases=[prompts[label]] if label in prompts else [])
             for i, label in enumerate(labels)]
    type_id = {t['name']: t['type_id'] for t in types}
    rooms = []
    for path in sorted((source / 'HMSG/rooms').glob('*.json')):
        raw = read_json(path)
        vertices = np.asarray(raw['vertices'])
        bounds = None
        if vertices.size and np.ptp(vertices[:, 0]) > 0 and np.ptp(vertices[:, 1]) > 0:
            bounds = [*vertices.min(0).tolist(), *vertices.max(0).tolist()]
        rooms.append(dict(room_id=raw['room_id'], name=raw['name'],
                          kind='unassigned' if raw['room_id'] == 'R00' else 'area',
                          bounds_xz=bounds, polygon_xz=None,
                          boundary_source='HMSG bounds, not independently verified'))
    for i, raw in enumerate(entries):
        oid = raw['id']
        bbox = array(raw, 'bbox_np')
        lo, hi = bbox.min(0), bbox.max(0)
        geom = cloud(f'object-{i:03d}', array(raw, 'pcd_np'), array(raw, 'pcd_color_np'))
        vectors.append(array(raw, 'clip_ft'))
        links = []
        for j, view in enumerate(source_index[oid]['views']):
            obsid = f'obs-{i:03d}-{j}'
            image_source = package_path(source, view['image'])
            path = output / 'assets' / (obsid + image_source.suffix)
            shutil.copyfile(image_source, path)
            with Image.open(path) as image:
                width, height = image.size
            aid = register(path, 'image', dict(width=width, height=height, mime_type='image/jpeg'))
            observations.append(dict(observation_id=obsid, image_asset_id=aid, width=width,
                                     height=height, source_kind='gs_render',
                                     reconstruction_id='mfloor-originalK2', captured_at=None,
                                     rendered_at=None, K=view['K'],
                                     camera_to_world=view['camera_to_world_yup'],
                                     camera_convention='opencv_xright_ydown_zforward',
                                     provenance=[view['source'], 'annotation_conditioned=true']))
            links.append(dict(observation_id=obsid, crop_xyxy=view['crop_box_xyxy'],
                              target_support='valid' if view['ED_in_AABB_pixels'] > 0 else 'invalid'))
        objects.append(dict(object_id=oid, source_id=raw['device_id'],
                            type_ids=[type_id[raw['class_name'][0]]], label=raw['class_name'][0],
                            aliases=raw['aliases'], description=raw['source_text'] or None,
                            room_ids=[raw['region_id']],
                            region_assignment_source=raw['region_assignment_source'],
                            bbox=dict(min=lo.tolist(), max=hi.tolist()),
                            center_xyz=((lo + hi) / 2).tolist(), geometry_asset_id=geom,
                            feature_refs=[dict(space_id='clip-h14', row=i,
                                               valid=raw['visual_feature_valid'])],
                            observation_links=links, attributes=[], identity_status='unresolved',
                            entity_id=None, source_links=[],
                            provenance=[raw['annotation_origin'],
                                        'source_inspectRefs=' + str(raw['source_inspectRefs']),
                                        'source label and physical identity not verified']))
    # Original references remain in provenance if their syntax is not an object ID.
    ids = {o['object_id'] for o in objects}
    for raw, item in zip(entries, objects):
        item['source_links'] = [dict(target_object_id=r, kind='source_inspect_reference',
                                     source='CG portable')
                                for r in raw['source_inspectRefs'] if isinstance(r, str) and r in ids]
    matrix_path = output / 'assets/features.npy'
    matrix = np.asarray(vectors, dtype=np.float32)
    np.save(matrix_path, matrix)
    aid = register(matrix_path, 'feature_matrix', dict(shape=list(matrix.shape),
                   dtype='float32', feature_space_id='clip-h14'))
    scene = dict(schema_version='1.0.0', scene_id='mfloor',
                 snapshot_id='mfloor-20261110-' + digest(cg / 'map.semantic.json')[:12],
                 name='M-floor annotated', coordinate_frame=dict(frame_id=frame, unit='m', up_axis='y'),
                 bounds_xyz=dict(min=np.minimum(xyz.min(0), np.min([o['bbox']['min'] for o in objects], 0)).tolist(),
                                 max=np.maximum(xyz.max(0), np.max([o['bbox']['max'] for o in objects], 0)).tolist()),
                 types=types, rooms=rooms, objects=objects, observations=observations, assets=assets,
                 feature_spaces=[dict(space_id='clip-h14', encoder='ViT-H-14',
                                      weights_fingerprint='laion2b_s32b_b79k',
                                      preprocess_fingerprint='source-context25-squarepad', dim=1024,
                                      normalized=False, matrix_asset_id=aid)],
                 background_asset_id=background,
                 provenance=['CG manifest sha256=' + digest(cg / 'map.semantic.json'),
                             'Source: ' + str(source),
                             'Background Z-up to Y-up: (x,y,z)->(x,z,-y)',
                             '191 source records are not 191 verified physical objects',
                             'Images are annotation-conditioned GS renders, not independent camera evidence'])
    write_json(output / 'scene.json', scene)
    SceneStore(output)
    return output
