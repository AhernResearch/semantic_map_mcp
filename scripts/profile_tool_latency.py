"""Read-only tool/encoder profiling, with per-stage clocks and explicit cache state."""
import argparse
from functools import wraps
from pathlib import Path
from time import perf_counter

from sementic_map_mcp.agent import model_context
from sementic_map_mcp.retrieval import OpenClipEncoder
from sementic_map_mcp.service import MapTools
from sementic_map_mcp.store import SceneStore, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', type=Path, required=True)
    parser.add_argument('--encoder-config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    store = SceneStore(args.scene)
    store_seconds = perf_counter() - start
    start = perf_counter()
    encoder = OpenClipEncoder.from_config(store, args.encoder_config)
    encoder_seconds = perf_counter() - start
    calls = []
    original_encode = encoder.encode

    @wraps(original_encode)
    def encode(texts):
        tick = perf_counter()
        result = original_encode(texts)
        calls.append(dict(texts=list(texts), seconds=perf_counter()-tick))
        return result

    encoder.encode = encode
    configs = [('list_scene', {}),
        ('search_candidates', dict(queries=['office printer'])),
        ('search_candidates', dict(queries=['柜子', 'white cabinet', 'white cupboard', 'white shelf'])),
        ('inspect_objects', dict(ids=['S135'])),
        ('inspect_objects', dict(ids=['S010', 'S011', 'S012'])),
        ('view_map', dict(queries=['椅子']))]
    rows = []
    for index, (name, arguments) in enumerate(configs):
        for repetition in range(3):
            service = MapTools(store, args.output / f'{index}-{repetition}-media', encoder)
            for cache in ['empty', 'hit']:
                call_start = len(calls)
                tick = perf_counter()
                raw = service.call(name, arguments)
                raw_seconds = perf_counter()-tick
                tick = perf_counter()
                context = model_context(raw.envelope, arguments)
                projection_seconds = perf_counter()-tick
                preview_seconds = 0
                model_media = raw.media
                if name == 'search_candidates' and context['data']:
                    objects = [store.objects[c['object_id']] for c in context['data']['candidates']]
                    tick = perf_counter()
                    _, model_media = service.renderer.candidate_sheet(objects)
                    preview_seconds = perf_counter()-tick
                encode_calls = calls[call_start:]
                rows.append(dict(tool=name, arguments=arguments, repetition=repetition, cache=cache,
                    raw_seconds=raw_seconds, projection_seconds=projection_seconds,
                    preview_seconds=preview_seconds,
                    measured_seconds=raw_seconds+projection_seconds+preview_seconds,
                    encode_calls=encode_calls, encoder_seconds=sum(c['seconds'] for c in encode_calls),
                    status=raw.envelope['status'], images=len(model_media),
                    png_bytes=sum(len(m.content) for m in model_media)))
                write_json(args.output / 'results.json', rows)
                print(name, arguments, cache, round(rows[-1]['measured_seconds'],4), flush=True)
    write_json(args.output / 'startup.json', dict(scene_seconds=store_seconds,
        encoder_load_verify_seconds=encoder_seconds, snapshot_id=store.scene['snapshot_id'],
        scope='Local raw tools plus projection/preview; excludes transport/base64/model decisions'))


if __name__ == '__main__':
    main()
