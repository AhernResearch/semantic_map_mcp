"""Prepare six source-based evidence bundles; these are not model decisions or answer labels."""
import argparse
from pathlib import Path

from map_query_tools.store import SceneStore, read_json, write_json
from map_query_tools.service import MapTools
from map_query_tools.retrieval import OpenClipEncoder

CASES = {'NG01', 'NG02', 'NG03', 'NG11', 'NG16', 'NG17'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scene')
    parser.add_argument('encoder_config')
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    store = SceneStore(args.scene)
    config = read_json(args.encoder_config)
    tools = MapTools(store, args.output / 'media',
                     OpenClipEncoder(store.feature_spaces[config['space_id']], config))
    suite = read_json(Path(__file__).resolve().parents[1] / 'tasks/navigation_requests.json')
    if suite['snapshot_id'] != store.scene['snapshot_id']:
        raise ValueError('Task snapshot differs from scene')
    write_json(args.output / 'scene_summary.json', tools.list_scene().envelope)
    rows = []
    for case in suite['cases']:
        if case['case_id'] not in CASES:
            continue
        plan = case['retrieval_plan']
        # Region-name ambiguity uses the room inventory, not an empty semantic query.
        result = (tools.search_candidates(plan['queries'], plan['room_ids'])
                  if plan['queries'] else tools.list_scene())
        write_json(args.output / (case['case_id'] + '-recall.json'), result.envelope)
        ids = list(dict.fromkeys(case['source_anchors']))[:8]
        if ids:
            write_json(args.output / (case['case_id'] + '-inspect.json'), tools.inspect_objects(ids).envelope)
        rows.append(dict(case_id=case['case_id'], utterance=case['utterance'],
                         source_anchor_ids=ids, recall_tool=result.envelope['tool'],
                         tool_status=result.envelope['status'], review_status='not_ground_truth',
                         agent_status='not_run'))
        write_json(args.output / 'report.json', dict(snapshot_id=store.scene['snapshot_id'],
                   cases=rows, semantic_accuracy=None, navigation_goal=None))
        print(case['case_id'], result.envelope['status'], flush=True)


if __name__ == '__main__':
    main()
