"""Run the six review cases against an explicitly configured local multimodal endpoint."""
import argparse
from functools import partial
from pathlib import Path
from urllib.error import URLError

from jsonschema import ValidationError

from sementic_map_mcp.agent import chat, run_agent
from sementic_map_mcp.multimodal import load_encoder
from sementic_map_mcp.service import MapTools
from sementic_map_mcp.store import SceneStore, grounding_cases, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scene')
    parser.add_argument('encoder_config')
    parser.add_argument('output', type=Path)
    parser.add_argument('--endpoint', required=True, help='Authorized local multimodal endpoint ending /v1')
    parser.add_argument('--model', required=True)
    parser.add_argument('--descriptions', type=Path, help='Snapshot-bound offline VLM sidecar')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    store = SceneStore(args.scene)
    encoder = load_encoder(store, args.encoder_config)
    service = MapTools(store, args.output / 'media', encoder, descriptions_path=args.descriptions)
    report = dict(model=args.model, endpoint=args.endpoint, semantic_accuracy=None, cases=[])
    for case in grounding_cases(store):
        row = dict(case_id=case['case_id'], ground_truth='unreviewed')
        try:
            row['result'] = run_agent(service, case['utterance'], args.output / case['case_id'],
                                      partial(chat, args.endpoint, args.model,
                                              use_descriptions=service.descriptions is not None), case['context'])
            row['execution'] = 'completed_not_semantically_verified'
        except (URLError, TimeoutError, ValueError, ValidationError, KeyError) as error:
            row.update(execution='failed', error_type=type(error).__name__, error=str(error))
        report['cases'].append(row)
        write_json(args.output / 'report.json', report)
        print(case['case_id'], row['execution'], flush=True)


if __name__ == '__main__':
    main()
