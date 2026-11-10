"""Project existing object IDs into an orthographic GS preview; no semantic relabeling."""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('render_dir',type=Path)
    parser.add_argument('scene',type=Path)
    args=parser.parse_args()
    meta=json.loads((args.render_dir/'render.json').read_text())
    scene=json.loads(args.scene.read_text())
    base=Image.open(args.render_dir/'topdown_cut_0.2.png').convert('RGB')
    image=Image.new('RGB',(base.width,base.height+150),'#fafafa')
    image.paste(base,(0,70))
    draw=ImageDraw.Draw(image)
    font=ImageFont.truetype('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',22)
    small=ImageFont.truetype('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',17)
    draw.text((20,10),'3DGS topdown | R01 区域 | 桌子、椅子、门：已有标注记录',font=font,fill='#222222')
    draw.text((20,43),'正交投影：右 +X / 上 -Z；去顶高度仅影响显示，不修改源地图',font=small,fill='#555555')
    matrix=np.array(meta['world_xy_to_pixel'])
    colors={'桌子':'#c95e08','椅子':'#1477c9','门':'#8c45ad'}
    records=[]
    for item in scene['objects']:
        if 'R01' not in item['room_ids'] or item['label'] not in colors:
            continue
        x,y,z=item['center_xyz']
        uv=matrix@np.array([x,-z,1.])
        u,v=float(uv[0]),float(uv[1])+70
        if not (0<=u<base.width and 70<=v<70+base.height):
            continue
        color=colors[item['label']]
        text=item['object_id']
        width=draw.textbbox((0,0),text,font=small)[2]+16
        # Short local labels, not cross-scene leader lines.
        draw.ellipse((u-5,v-5,u+5,v+5),fill=color,outline='white',width=2)
        draw.rounded_rectangle((u-width/2,v-35,u+width/2,v-8),radius=4,fill=color,outline='white',width=1)
        draw.text((u-width/2+8,v-34),text,font=small,fill='white')
        records.append(dict(object_id=text,label=item['label'],center_yup=item['center_xyz'],
                            image_center=[u,v],source_label_verified=False))
    baseline=70+base.height
    for i,(label,color) in enumerate(colors.items()):
        draw.rectangle((20+i*160,baseline+18,40+i*160,baseline+38),fill=color)
        draw.text((50+i*160,baseline+13),label,font=font,fill='#222222')
    draw.text((20,baseline+48),'ID 为源记录；框/类别未独立核验。边缘模糊与拉伸为原重建的新视角伪影。',font=small,fill='#555555')
    s=matrix[0,0]
    draw.line([(base.width-230,baseline+27),(base.width-230+s,baseline+27)],fill='#222222',width=3)
    draw.text((base.width-230,baseline+35),'1 m',font=small,fill='#222222')
    image.save(args.render_dir/'topdown_annotated.png')
    (args.render_dir/'overlay.json').write_text(json.dumps(dict(scene_id=scene['scene_id'],
        snapshot_id=scene['snapshot_id'],image='topdown_annotated.png',source_image='topdown_cut_0.2.png',
        header_offset_px=70,objects=records),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
