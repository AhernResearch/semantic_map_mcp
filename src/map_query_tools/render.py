"""Deterministic topdown pages and object evidence panels."""
import hashlib
import io

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .ports import MediaPayload

FONT = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
COLORS = ['#e64b35', '#2878b5', '#30964b', '#9957b5']


class Renderer:
    def __init__(self, store, output, font=FONT):
        from pathlib import Path
        self.store, self.output = store, Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.font = ImageFont.truetype(font, 17)
        self.small = ImageFont.truetype(font, 13)

    def save(self, image, kind, object_ids, observation_ids):
        stream = io.BytesIO()
        image.save(stream, format='PNG')
        content = stream.getvalue()
        mid = hashlib.sha256(content).hexdigest()[:24]
        path = self.output / (mid + '.png')
        if not path.exists():
            path.write_bytes(content)
        meta = dict(media_id=mid, uri=str(path.resolve()), mime_type='image/png',
                    width=image.width, height=image.height, source_kind=kind,
                    object_ids=list(object_ids), observation_ids=list(observation_ids))
        return MediaPayload(meta, content)

    def maps(self, objects, room_ids, groups):
        # Every page uses the same room extent, so positions stay comparable.
        ordered = sorted(objects, key=lambda o: (o['center_xyz'][2], o['center_xyz'][0], o['object_id']))
        pages = [ordered[i:i + 24] for i in range(0, len(ordered), 24)] or [[]]
        rooms = [r for rid, r in self.store.rooms.items() if not room_ids or rid in room_ids]
        extents = [] if objects else [r['bounds_xz'] for r in rooms if r['bounds_xz']]
        extents += [[o['center_xyz'][0], o['center_xyz'][2],
                     o['center_xyz'][0], o['center_xyz'][2]] for o in ordered]
        if extents:
            lo, hi = np.min(extents, axis=0)[:2], np.max(extents, axis=0)[2:]
        else:
            lo = np.array(self.store.scene['bounds_xyz']['min'])[[0, 2]]
            hi = np.array(self.store.scene['bounds_xyz']['max'])[[0, 2]]
        padding = np.maximum((hi - lo) * .18, 2.0)
        lo, hi = lo - padding, hi + padding
        span = hi - lo
        scale = min(1080 / span[0], 700 / span[1])
        ox, oy = 60 + (1080 - scale * span[0]) / 2, 110 + (700 - scale * span[1]) / 2
        matrix = np.array([[scale, 0, ox - scale * lo[0]],
                           [0, scale, oy - scale * lo[1]], [0, 0, 1]])

        def project(xz):
            return np.asarray(xz) * scale + [matrix[0, 2], matrix[1, 2]]

        # Shared, candidate-local context raster. Height quantiles discard floor/ceiling tails.
        background = Image.new('RGB', (1200, 900), '#f4f7fa')
        bg_id = self.store.scene['background_asset_id']
        if bg_id:
            points, _ = self.store.pointcloud(bg_id)
            mask = ((points[:, 0] >= lo[0]) & (points[:, 0] <= hi[0]) &
                    (points[:, 2] >= lo[1]) & (points[:, 2] <= hi[1]))
            local = points[mask]
            if len(local):
                low, high = np.percentile(local[:, 1], [15, 85])
                local = local[(local[:, 1] >= low) & (local[:, 1] <= high)]
                pixels = project(local[:, [0, 2]]).astype(int)
                density = np.zeros((900, 1200), dtype=np.float32)
                np.add.at(density, (pixels[:, 1], pixels[:, 0]), 1)
                from PIL import ImageFilter
                density = np.asarray(Image.fromarray(np.minimum(density * 35, 255).astype('uint8')).filter(
                    ImageFilter.GaussianBlur(.7)), dtype=float) / 255
                rgb = np.array(background, dtype=float)
                rgb = rgb * (1 - density[..., None] * .65) + np.array([100, 124, 142]) * density[..., None] * .65
                background = Image.fromarray(rgb.astype('uint8'))
        # Quiet context footprints make nearby furniture legible without adding labels.
        bd = ImageDraw.Draw(background)
        for item in self.store.objects.values():
            a = np.array(item['bbox']['min'])[[0, 2]]
            b = np.array(item['bbox']['max'])[[0, 2]]
            if np.all(a >= lo) and np.all(b <= hi):
                pa, pb = project(a), project(b)
                bd.rectangle((*pa, *pb), outline='#d0d9e2', width=1)

        maps, media = [], []
        for page_index, page in enumerate(pages[:8]):
            image = background.copy()
            draw = ImageDraw.Draw(image)
            legend = [dict(query=g['query'], color=COLORS[i], meaning='候选物体')
                      for i, g in enumerate(groups)]
            entries = [(entry['query'], entry['color'],
                        sum(o['object_id'] in groups[i]['candidate_ids'] for o in page))
                       for i, entry in enumerate(legend)]
            if not entries:
                entries = [('物体', '#334155', len(page))]
            for i, (name, color, count) in enumerate(entries):
                x, y = 30 + i * 285, 26
                title = name
                while draw.textbbox((0, 0), f'{title}  ·  {count}', font=self.font)[2] > 220:
                    title = title[:-2] + '…'
                draw.rounded_rectangle((x, y, x + 269, y + 34), radius=8,
                                       fill='white', outline='#dce3eb', width=1)
                draw.ellipse((x + 12, y + 11, x + 24, y + 23), fill=color)
                draw.text((x + 34, y + 3), f'{title}  ·  {count}', fill='#263747', font=self.font)
            centers = [project(np.array(o['center_xyz'])[[0, 2]]) for o in page]
            occupied = [(x - 6, y - 6, x + 6, y + 6) for x, y in centers]
            for item, (x, y) in zip(page, centers):
                oid = item['object_id']
                memberships = [j for j, g in enumerate(groups) if oid in g['candidate_ids']]
                color = COLORS[memberships[0]] if memberships else '#334155'
                label = oid + ' · ' + item['label'][:12]
                w = draw.textbbox((0, 0), label, font=self.font)[2] + 16
                positions = []
                for gap in (10, 28, 46, 64):
                    positions.extend([(x + gap, y - 13), (x - w - gap, y - 13),
                                      (x - w / 2, y - 26 - gap), (x - w / 2, y + gap)])
                boxes = [(px, py, px + w, py + 26) for px, py in positions
                         if 12 <= px and px + w < 1188 and 102 <= py and py + 26 < 830]
                def overlap(box):
                    return sum(max(0, min(box[2], r[2]) - max(box[0], r[0])) *
                               max(0, min(box[3], r[3]) - max(box[1], r[1])) for r in occupied)
                box = min(boxes, key=overlap)
                occupied.append(box)
                endpoint = (np.clip(x, box[0], box[2]), np.clip(y, box[1], box[3]))
                draw.line([(x, y), endpoint], fill=color, width=1)
                draw.rounded_rectangle(box, radius=5, fill='white', outline=color, width=2)
                draw.text((box[0] + 8, box[1]), label, font=self.font, fill=color)
                draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=color, outline='white', width=2)
                for j, group in enumerate(memberships[1:]):
                    draw.ellipse((box[0] + 3 + j * 7, box[3] - 4,
                                  box[0] + 7 + j * 7, box[3]), fill=COLORS[group])
            bar = min(1.0, float(span.min()) / 4)
            draw.line([(30, 850), (30 + bar * scale, 850)], fill='#333333', width=2)
            draw.text((30, 859), f'{bar:.2f} m', fill='#555555', font=self.small)
            # Coordinate indicator stays outside the map viewport; up is world -Z.
            draw.rounded_rectangle((1020, 815, 1180, 889), radius=12,
                                   fill='white', outline='#dce3eb', width=1)
            cx, cy = 1061, 867
            draw.line((cx, cy, 1133, cy), fill='#c9664f', width=3)
            draw.polygon([(1139, cy), (1129, cy - 5), (1129, cy + 5)], fill='#c9664f')
            draw.line((cx, cy, cx, 834), fill='#487f9f', width=3)
            draw.polygon([(cx, 826), (cx - 5, 837), (cx + 5, 837)], fill='#487f9f')
            draw.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill='#607587')
            draw.text((1141, 853), '+X', fill='#a54f3d', font=self.font)
            draw.text((1030, 820), '−Z', fill='#3b6e8b', font=self.font)
            if len(pages) > 1:
                draw.text((575, 859), f'{page_index + 1} / {len(pages)}',
                          fill='#718096', font=self.small)
            ids = [o['object_id'] for o in page]
            payload = self.save(image, 'topdown', ids, [])
            media.append(payload)
            maps.append(dict(map_view_id='map-' + payload.metadata['media_id'],
                             media_id=payload.metadata['media_id'], room_ids=list(room_ids),
                             object_ids=ids, bounds_xz=[*lo.tolist(), *hi.tolist()],
                             world_to_pixel=matrix.tolist(), legend=legend, page_index=page_index))
        return maps, media, [o['object_id'] for o in ordered[192:]]

    def evidence(self, item, link):
        obs = self.store.observations[link['observation_id']]
        with Image.open(self.store.path(obs['image_asset_id'])) as source:
            original = source.convert('RGB')
        cuts = [('full', [0, 0, original.width, original.height])]
        if link['crop_xyxy']:
            cuts.append(('crop', link['crop_xyxy']))
        canvas = Image.new('RGB', (1200 if len(cuts) == 2 else 600, 680), 'white')
        draw = ImageDraw.Draw(canvas)
        draw.text((15, 10), f"{item['object_id']} | {item['label']} | {obs['observation_id']} | 物体图片",
                  fill='black', font=self.font)
        draw.text((15, 40), '左：完整视角 | 右：物体细节' if len(cuts) == 2 else '完整视角',
                  fill='#555555', font=self.small)
        panels = []
        for i, (kind, box) in enumerate(cuts):
            part = original.crop(box)
            part.thumbnail((580, 580))
            x, y = i * 600 + 10, 80
            canvas.paste(part, (x, y))
            panels.append(dict(kind=kind, panel_xyxy=[x, y, x + part.width, y + part.height],
                               original_crop_xyxy=box, original_size=list(original.size)))
        media = self.save(canvas, 'evidence_panel', [item['object_id']], [obs['observation_id']])
        position = np.array(obs['camera_to_world'])[:3, 3].tolist() if obs['camera_to_world'] else None
        evidence = dict(observation_id=obs['observation_id'], media_id=media.metadata['media_id'],
                        source_kind=obs['source_kind'], camera_position_xyz=position, panels=panels)
        return evidence, media
