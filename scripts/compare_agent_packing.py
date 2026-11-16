"""End-to-end Agent comparison with one shared loop and two image packing modes."""
import argparse
from functools import partial
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
from urllib.error import URLError

from jsonschema import ValidationError

from sementic_map_mcp.agent import chat, run_agent
from sementic_map_mcp.retrieval import OpenClipEncoder
from sementic_map_mcp.service import MapTools
from sementic_map_mcp.store import SceneStore, read_json, write_json


CASES = [('printer', '去那个像打印机的设备那里', ['single', 'montage']),
         ('cabinet', '去那个白色柜子那里', ['montage', 'single'])]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', type=Path, required=True)
    parser.add_argument('--encoder-config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--comparison', choices=['packing', 'resolution', 'single-resolution'], default='packing')
    args = parser.parse_args()
    if not os.environ.get('MAP_AGENT_API_KEY'):
        raise ValueError('MAP_AGENT_API_KEY required')
    args.output.mkdir(parents=True, exist_ok=False)
    cases = CASES if args.comparison == 'packing' else [
        ('printer', CASES[0][1], ['native', 'reduced']),
        ('cabinet', CASES[1][1], ['reduced', 'native'])]
    startup = perf_counter()
    store = SceneStore(args.scene)
    encoder = OpenClipEncoder.from_config(store, args.encoder_config)
    write_json(args.output / 'experiment.json', dict(scene_id=store.scene['scene_id'],
        snapshot_id=store.scene['snapshot_id'], encoder_config=read_json(args.encoder_config),
        endpoint=args.endpoint, model=args.model, cases=cases, max_turns=8,
        max_tokens=1600, temperature=0, request_socket_timeout_seconds=120, retries=0,
        startup_seconds=perf_counter()-startup, semantic_accuracy=None,
        scope='Complete free-running Agent tasks; excludes one-time scene/model startup'))
    source_root = Path(__file__).resolve().parents[1]
    paths = [Path(__file__).resolve(), *sorted((source_root / 'src/sementic_map_mcp').glob('*.py'))]
    write_json(args.output / 'sources.json', {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
    rows = []
    write_json(args.output / 'conditions.json', dict(comparison=args.comparison, cases=cases,
        native_max_width=None, reduced_max_width=300 if args.comparison == 'single-resolution' else 1200, history='append-only'))
    for case, utterance, modes in cases:
        for mode in modes:
            folder = args.output / f'{case}-{mode}'
            # Fresh, empty request cache for each task; shared immutable snapshot and model.
            service = MapTools(store, args.output / f'{case}-{mode}-media', encoder,
                               montage_max_width=1200 if mode == 'reduced' and args.comparison == 'resolution' else None,
                               single_max_width=300 if mode == 'reduced' and args.comparison == 'single-resolution' else None)
            tick = perf_counter()
            row = dict(case=case, mode=mode)
            try:
                result = run_agent(service, utterance, folder,
                    partial(chat, args.endpoint, args.model), image_packing=mode if args.comparison == 'packing' else ('single' if args.comparison == 'single-resolution' else 'montage'))
                row.update(execution='completed', status=result['grounding']['status'],
                           selection=result['selection'])
            except (URLError, TimeoutError, ValueError, ValidationError, KeyError) as error:
                row.update(execution='failed', error_type=type(error).__name__, error=str(error))
            row['seconds'] = perf_counter()-tick
            row['model_requests'] = len(list(folder.glob('*-request.json')))
            row['model_responses'] = len(list(folder.glob('*-response.json')))
            events = read_json(folder / 'timings.json')['events']
            row['model_seconds'] = sum(e['seconds'] for e in events if e['stage']=='model')
            row['tool_seconds'] = sum(e['seconds'] for e in events if e['stage']=='tool')
            calls = []
            for toolfile in sorted(folder.glob('*-tool.json')):
                raw = read_json(toolfile)
                context = read_json(toolfile.with_name(toolfile.name.replace('-tool', '-model-context')))
                data = context['data'] or {}
                calls.append(dict(tool=raw['tool'],
                    arguments=read_json(toolfile.with_name(toolfile.name.replace('-tool', '-arguments'))),
                    candidate_ids=[c['object_id'] for c in data.get('candidates', [])],
                    next_page=data.get('next_page'), image_count=len(context['media'])))
            row['tool_calls'] = calls
            rows.append(row)
            write_json(args.output / 'results.json', rows)
            print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
