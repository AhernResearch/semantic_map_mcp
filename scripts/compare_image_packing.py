"""Fixed-pool, pixel-matched image packing experiment; no agent search loop."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
from time import perf_counter

from urllib.request import Request, urlopen
from urllib.error import URLError
import numpy as np
from PIL import Image
from jsonschema import validate, ValidationError

from sementic_map_mcp.preview import call_for_model
from sementic_map_mcp.render import Renderer
from sementic_map_mcp.retrieval import OpenClipEncoder
from sementic_map_mcp.service import MapTools
from sementic_map_mcp.store import SceneStore, write_json

PROMPT = '''根据用户描述和物体图片，在给定候选中判断目标。
图片中的对象ID与候选列表对应，图片可能独立展示或拼接展示。
名称供参考，根据图片与用户条件判断。有明确匹配就选择；多个候选难以区分时提出具体澄清问题。
没有明确匹配时返回证据不足，不把当前候选池当作整个场景。
只返回JSON：status（resolved/ambiguous/insufficient_evidence）、object_ids、reason、question。
resolved仅选择一个ID；ambiguous列出需要区分的ID并提问；无须提问时question=null。说明简短具体。'''
DECISION = dict(type='object', additionalProperties=False,
    required=['status', 'object_ids', 'reason', 'question'], properties=dict(
        status=dict(enum=['resolved', 'ambiguous', 'insufficient_evidence']),
        object_ids=dict(type='array', uniqueItems=True, items=dict(type='string')),
        reason=dict(type='string'), question=dict(type=['string', 'null'])))
CASES = [('printer', '去那个像打印机的设备那里', 'office printer'),
         ('cabinet', '去那个白色柜子那里', 'white cabinet')]


def png(image):
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def fixed_images(sheet_bytes, preview, object_ids):
    """Extract exactly the pixels shown for each ID; no second resize/re-render."""
    with Image.open(io.BytesIO(sheet_bytes)) as image:
        sheet = image.convert('RGB')
    if [c['object_id'] for c in preview['cells']] != object_ids or preview['missing_ids']:
        raise ValueError('Experiment requires one actual pictured cell for each candidate in order')
    singles = []
    for cell in preview['cells']:
        tile = sheet.crop(cell['cell_xyxy'])
        x0, y0, x1, y1 = cell['cell_xyxy']
        if not np.array_equal(np.asarray(tile), np.asarray(sheet)[y0:y1, x0:x1]):
            raise ValueError('Tile pixels differ from montage')
        singles.append(png(tile))
    return singles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', type=Path, required=True)
    parser.add_argument('--encoder-config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--comparison', choices=['packing', 'resolution', 'single-resolution'], default='packing')
    parser.add_argument('--max-tokens', type=int, default=512)
    args = parser.parse_args()
    order = ['single', 'montage', 'montage', 'single'] if args.comparison == 'packing' else ['native', 'reduced', 'reduced', 'native']
    token = os.environ['MAP_AGENT_API_KEY']
    args.output.mkdir(parents=True, exist_ok=False)
    store = SceneStore(args.scene)
    encoder = OpenClipEncoder.from_config(store, args.encoder_config)
    service = MapTools(store, args.output / 'media', encoder)
    write_json(args.output / 'protocol.json', dict(model=args.model, endpoint=args.endpoint,
        scene_id=store.scene['scene_id'], snapshot_id=store.scene['snapshot_id'],
        encoder_config=json.loads(args.encoder_config.read_text()), prompt=PROMPT,
        cases=CASES, temperature=0, max_tokens=args.max_tokens, request_socket_timeout_seconds=45,
        repeats=2, order=order, retries=0,
        scope='One decision on a frozen candidate pool; not full-agent task latency',
        decision_schema=DECISION, semantic_accuracy=None))
    sources = [Path(__file__), Path(__file__).parents[1] / 'src/sementic_map_mcp/render.py',
               Path(__file__).parents[1] / 'src/sementic_map_mcp/agent.py',
               Path(__file__).parents[1] / 'src/sementic_map_mcp/preview.py',
               Path(__file__).parents[1] / 'src/sementic_map_mcp/service.py']
    write_json(args.output / 'sources.json', {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})
    write_json(args.output / 'conditions.json', dict(comparison=args.comparison, order=order, max_tokens=args.max_tokens, reduced_max_width=300 if args.comparison == 'single-resolution' else 1200))
    rows = []
    for case, utterance, query in CASES:
        case_dir = args.output / case
        case_dir.mkdir()
        raw, context, media, preview = call_for_model(service, 'search_candidates', dict(queries=[query]))
        candidates = context['data']['candidates']
        ids = [c['object_id'] for c in candidates]
        if len(ids) != 5 or len(media) != 1 or preview is None:
            raise ValueError('Experiment requires exactly five pictured candidates')
        write_json(case_dir / 'tool.json', raw.envelope)
        write_json(case_dir / 'preview.json', preview)
        singles = fixed_images(media[0].content, preview, ids)
        (case_dir / 'montage.png').write_bytes(media[0].content)
        for i, data in enumerate(singles):
            (case_dir / f'{i:02d}-{ids[i]}.png').write_bytes(data)
        text = json.dumps(dict(utterance=utterance, candidates=candidates), ensure_ascii=False)
        hashes = [hashlib.sha256(data).hexdigest() for data in singles]
        write_json(case_dir / 'fixed-input.json', dict(query=query, candidates=candidates,
            text=text, cell_sha256=hashes, pixel_identity_checked=True,
            montage_size=[media[0].metadata['width'], media[0].metadata['height']],
            tile_size=[600, 400]))
        variants = dict(single=singles, montage=[media[0].content])
        if args.comparison == 'resolution':
            renderer = Renderer(store, args.output / 'media-reduced', montage_max_width=1200)
            reduced_preview, reduced_media = renderer.candidate_sheet([store.objects[oid] for oid in ids])
            write_json(case_dir / 'reduced-preview.json', reduced_preview)
            variants = dict(native=[media[0].content], reduced=[reduced_media[0].content])
        if args.comparison == 'single-resolution':
            renderer = Renderer(store, args.output / 'media-reduced', single_max_width=300)
            reduced_preview, reduced_media = renderer.candidate_sheet([store.objects[oid] for oid in ids], 'single')
            write_json(case_dir / 'reduced-preview.json', reduced_preview)
            variants = dict(native=singles, reduced=[m.content for m in reduced_media])
        for trial, mode in enumerate(order):
            parts = [dict(type='text', text=text)]
            for data in variants[mode]:
                parts.append(dict(type='image_url', image_url=dict(
                    url='data:image/png;base64,' + base64.b64encode(data).decode())))
            payload = dict(model=args.model, messages=[dict(role='system', content=PROMPT),
                dict(role='user', content=parts)], temperature=0, max_tokens=args.max_tokens, stream=False)
            prefix = case_dir / f'{trial:02d}-{mode}'
            write_json(str(prefix) + '-request.json', payload)
            tick = perf_counter()
            row = dict(case=case, trial=trial, mode=mode, candidate_ids=ids,
                       image_count=len(parts)-1, request_bytes=len(json.dumps(payload).encode()))
            try:
                request = Request(args.endpoint.rstrip('/') + '/chat/completions',
                    data=json.dumps(payload).encode(), headers={
                        'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
                with urlopen(request, timeout=45) as response:
                    data = json.load(response)
                row['request_seconds'] = perf_counter() - tick
                write_json(str(prefix) + '-response.json', data)
                choice = data['choices'][0]
                if choice['finish_reason'] != 'stop':
                    raise ValueError('Incomplete response')
                content = choice['message']['content'].strip()
                if content.startswith('```json\n') and content.endswith('\n```'):
                    content = content[8:-4]
                decision = json.loads(content)
                validate(decision, DECISION)
                if not set(decision['object_ids']) <= set(ids):
                    raise ValueError('Decision cites object outside frozen pool')
                if decision['status'] == 'resolved' and len(decision['object_ids']) != 1:
                    raise ValueError('Resolved must select one object')
                row.update(execution='completed', decision=decision, usage=data.get('usage'))
            except (URLError, TimeoutError, ValueError, ValidationError) as error:
                row.update(execution='failed', error_type=type(error).__name__)
            row.setdefault('request_seconds', perf_counter() - tick)
            rows.append(row)
            write_json(str(prefix) + '-result.json', row)
            write_json(args.output / 'results.json', rows)
            print(json.dumps({k:v for k,v in row.items() if k!='usage'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
