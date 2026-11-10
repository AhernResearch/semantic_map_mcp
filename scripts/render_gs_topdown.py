"""Isolated GS topdown preview, using the original Gaussian parameters (no training)."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import torch
from gsplat import rasterization
from PIL import Image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ply', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--bounds', type=float, nargs=4, required=True, metavar=('X0', 'Y0', 'X1', 'Y1'))
    parser.add_argument('--cuts', type=float, nargs='+', default=[0.2, 0.8, 4.0])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    with args.ply.open('rb') as stream:
        header = []
        while True:
            line = stream.readline().decode('ascii').strip()
            if not line:
                raise ValueError('Incomplete PLY header')
            header.append(line)
            if line == 'end_header':
                break
        offset = stream.tell()
    assert 'format binary_little_endian 1.0' in header
    count = int(next(line for line in header if line.startswith('element vertex')).split()[-1])
    properties = [line.split()[-1] for line in header if line.startswith('property float ')]
    assert properties == ['x','y','z','nx','ny','nz','f_dc_0','f_dc_1','f_dc_2','opacity',
                          'scale_0','scale_1','scale_2','rot_0','rot_1','rot_2','rot_3']
    source = np.memmap(args.ply, mode='r', offset=offset, dtype='<f4', shape=(count,17))
    x0,y0,x1,y1 = args.bounds
    keep = ((source[:,0] >= x0-.5) & (source[:,0] <= x1+.5) &
            (source[:,1] >= y0-.5) & (source[:,1] <= y1+.5) & (source[:,2] >= -1.6))
    roi = np.asarray(source[keep]).copy()
    torch.set_num_threads(4)
    width = 1400
    scale = width/(x1-x0)
    height = int(round(scale*(y1-y0)))
    cx,cy = (x0+x1)/2,(y0+y1)/2
    eye = np.array([cx,cy,8.0])
    view = np.eye(4)
    view[:3,:3] = np.diag([1.,-1.,-1.])
    view[:3,3] = -view[:3,:3]@eye
    K = np.array([[scale,0,width/2],[0,scale,height/2],[0,0,1.]])
    records=[]
    for cut in args.cuts:
        selected=roi[roi[:,2] <= cut]
        tensor=torch.tensor(selected,device='cuda')
        means=tensor[:,:3].contiguous()
        quats=torch.nn.functional.normalize(tensor[:,13:17],dim=-1).contiguous()
        scales=tensor[:,10:13].exp().clamp(max=20).contiguous()
        opacity=tensor[:,9].sigmoid().contiguous()
        colors=(.5+.28209479177387814*tensor[:,6:9]).clamp(0,1).contiguous()
        with torch.inference_mode():
            rgb,alpha,_=rasterization(means,quats,scales,opacity,colors,
                torch.tensor(view[None],dtype=torch.float32,device='cuda'),
                torch.tensor(K[None],dtype=torch.float32,device='cuda'),width,height,
                camera_model='ortho',packed=True,near_plane=.1,far_plane=30,
                backgrounds=torch.tensor([[.96,.96,.96]],device='cuda'),render_mode='RGB')
        image=Image.fromarray((rgb[0].clamp(0,1).cpu().numpy()*255).astype('uint8'))
        name=f'topdown_cut_{cut:.1f}.png'
        image.save(args.output/name)
        rec=dict(image=name,cut_raw_z_max=cut,cut_raw_z_min=-1.6,
                 gaussians=len(selected),coverage_alpha_gt_half=float((alpha>.5).float().mean()),
                 sha256=hashlib.sha256((args.output/name).read_bytes()).hexdigest())
        records.append(rec)
        print(json.dumps(rec),flush=True)
        del tensor,means,quats,scales,opacity,colors,rgb,alpha
        torch.cuda.empty_cache()
    metadata=dict(source=str(args.ply),source_gaussian_count=count,roi_count=len(roi),
                  bounds_raw_xy=args.bounds,width=width,height=height,camera_model='ortho',
                  K=K.tolist(),world_to_camera_raw_zup=view.tolist(),
                  world_xy_to_pixel=[[scale,0,width/2-scale*cx],[0,-scale,height/2+scale*cy],[0,0,1]],
                  source_to_map='raw(x,y,z)->map(x,z,-y)',gsplat_version=__import__('gsplat').__version__,
                  records=records,limitations=['Height clipping is a visualization only; source unchanged',
                  'SH degree 0 matches source PLY; no synthesized geometry',
                  'Clipped surfaces/new-view holes do not imply free space'])
    (args.output/'render.json').write_text(json.dumps(metadata,indent=2))


if __name__=='__main__':
    main()
