"""Run the six review cases against an explicitly configured local multimodal endpoint."""
import argparse
from functools import partial
from urllib.error import URLError

from jsonschema import ValidationError

from map_query_tools.agent import chat, run_agent
from map_query_tools.retrieval import OpenClipEncoder
from map_query_tools.service import MapTools
from map_query_tools.store import SceneStore, read_json, write_json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scene')
    parser.add_argument('encoder_config')
    parser.add_argument('output', type=Path)
    parser.add_argument('--endpoint', required=True, help='Authorized local multimodal endpoint ending /v1')
    parser.add_argument('--model', required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    store = SceneStore(args.scene)
    config = read_json(args.encoder_config)
    encoder = OpenClipEncoder(store.feature_spaces[config['space_id']], config)
    service = MapTools(store, args.output / 'media', encoder)
    suite = read_json(Path(__file__).resolve().parents[1] / 'tasks/navigation_requests.json')
    if suite['snapshot_id'] != store.scene['snapshot_id']:
        raise ValueError('Task snapshot differs from scene')
    selected = {'NG01', 'NG02', 'NG03', 'NG11', 'NG16', 'NG17'}
    report = dict(model=args.model, endpoint=args.endpoint, semantic_accuracy=None, cases=[])
    for case in suite['cases']:
        if case['case_id'] not in selected:
            continue
        row = dict(case_id=case['case_id'], ground_truth='unreviewed')
        try:
            row['result'] = run_agent(service, case['utterance'], args.output / case['case_id'],
                                      partial(chat, args.endpoint, args.model), case['context'])
            row['execution'] = 'completed_not_semantically_verified'
        except (URLError, TimeoutError, ValueError, ValidationError, KeyError) as error:
            row.update(execution='failed', error_type=type(error).__name__, error=str(error))
        report['cases'].append(row)
        write_json(args.output / 'report.json', report)
        print(case['case_id'], row['execution'], flush=True)


if __name__ == '__main__':
    main()
