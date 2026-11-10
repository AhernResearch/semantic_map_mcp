"""Inspect existing retrieval without drawing maps or inventing semantic answer labels."""
import argparse
from pathlib import Path

from map_query_tools.retrieval import OpenClipEncoder, Retriever
from map_query_tools.store import SceneStore, read_json, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('scene')
    parser.add_argument('encoder_config')
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    store = SceneStore(args.scene)
    config = read_json(args.encoder_config)
    encoder = OpenClipEncoder(store.feature_spaces[config['space_id']], config)
    retriever = Retriever(store, encoder)
    probes = [
        ('NG02', '柜子', []),
        ('NG02', '白色柜子', []),
        ('NG02', 'a white cabinet', []),
        ('NG03', '像打印机的设备', []),
        ('NG03', 'a device that looks like a printer', []),
        ('NG05', '桌子', ['R01']),
        ('NG05', '门', ['R01']),
    ]
    results = []
    for case, query, rooms in probes:
        group, hits = retriever.search(query, store.scoped(rooms))
        results.append(dict(case_id=case, query=query, room_ids=rooms, group=group,
            candidates=[dict(**hit, source_label=store.objects[hit['object_id']]['label'],
                             source_rooms=store.objects[hit['object_id']]['room_ids']) for hit in hits]))
    write_json(args.output / 'retrieval.json', dict(scene_id=store.scene['scene_id'],
        snapshot_id=store.scene['snapshot_id'], encoder_sha256=encoder.checkpoint_sha256,
        scope='Recall-stage trace only; source labels are not ground truth; no LLM judgment or navigation',
        results=results))
    for row in results:
        print(row['query'], '=>', [(c['object_id'], c['source_label']) for c in row['candidates'][:5]])


if __name__ == '__main__':
    main()
