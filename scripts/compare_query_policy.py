"""Compare a frozen prior Agent policy with the English-query/early-decision policy."""
import argparse
from functools import partial
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from time import perf_counter
from urllib.error import URLError

from jsonschema import ValidationError

from sementic_map_mcp import agent
from sementic_map_mcp.retrieval import OpenClipEncoder
from sementic_map_mcp.service import MapTools
from sementic_map_mcp.store import SceneStore, read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-agent', type=Path, required=True)
    parser.add_argument('--scene', type=Path, required=True)
    parser.add_argument('--encoder-config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--model', required=True)
    args = parser.parse_args()
    if not os.environ.get('MAP_AGENT_API_KEY'):
        raise ValueError('MAP_AGENT_API_KEY required')
    args.output.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location('sementic_map_mcp._baseline_agent', args.baseline_agent)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    (args.output / 'baseline-agent.py').write_bytes(args.baseline_agent.read_bytes())
    store = SceneStore(args.scene)
    encoder = OpenClipEncoder.from_config(store, args.encoder_config)
    cases = [('printer', '去那个像打印机的设备那里', ['baseline', 'english']),
             ('cabinet', '去那个白色柜子那里', ['english', 'baseline'])]
    write_json(args.output / 'experiment.json', dict(scene_id=store.scene['scene_id'],
        snapshot_id=store.scene['snapshot_id'], encoder_config=read_json(args.encoder_config),
        model=args.model, endpoint=args.endpoint, cases=cases, image_packing='montage',
        montage_max_width=None, max_turns=8, max_tokens=1600, temperature=0,
        request_socket_timeout_seconds=120, retries=0, semantic_accuracy=None))
    root = Path(__file__).resolve().parents[1]
    paths = [Path(__file__).resolve(), *sorted((root/'src/sementic_map_mcp').glob('*.py'))]
    write_json(args.output / 'sources.json', {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
    rows = []
    for case, utterance, modes in cases:
        for mode in modes:
            module = baseline if mode == 'baseline' else agent
            folder = args.output / f'{case}-{mode}'
            service = MapTools(store, args.output / f'{case}-{mode}-media', encoder)
            start = perf_counter()
            row = dict(case=case, mode=mode)
            try:
                result = module.run_agent(service, utterance, folder,
                    partial(module.chat, args.endpoint, args.model))
                row.update(execution='completed', status=result['grounding']['status'],
                           selection=result['selection'])
            except (URLError, TimeoutError, ValueError, ValidationError, KeyError) as error:
                row.update(execution='failed', error_type=type(error).__name__, error=str(error))
            row['seconds'] = perf_counter()-start
            row['model_requests'] = len(list(folder.glob('*-request.json')))
            row['model_responses'] = len(list(folder.glob('*-response.json')))
            events = read_json(folder/'timings.json')['events']
            row['model_seconds'] = sum(e['seconds'] for e in events if e['stage']=='model')
            row['tool_seconds'] = sum(e['seconds'] for e in events if e['stage']=='tool')
            calls = []
            for path in sorted(folder.glob('*-arguments.json')):
                args_used = read_json(path)
                raw = read_json(path.with_name(path.name.replace('-arguments', '-tool')))
                context = read_json(path.with_name(path.name.replace('-arguments', '-model-context')))
                queries = args_used.get('queries', [])
                calls.append(dict(tool=raw['tool'], arguments=args_used,
                    queries_english_ascii=all(q.isascii() and any(c.isalpha() for c in q) for q in queries),
                    candidate_ids=[c['object_id'] for c in (context['data'] or {}).get('candidates', [])]))
            row['tool_calls'] = calls
            rows.append(row)
            write_json(args.output/'results.json', rows)
            print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
