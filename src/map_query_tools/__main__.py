"""Local CLI: import a scene or execute one tool and save JSON plus real images."""
import argparse
from pathlib import Path

from .import_mfloor import import_mfloor
from .retrieval import OpenClipEncoder
from .service import MapTools
from .store import SceneStore, read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    convert = commands.add_parser('import-mfloor')
    convert.add_argument('source')
    convert.add_argument('output')
    call = commands.add_parser('call')
    call.add_argument('scene')
    call.add_argument('tool', choices=['list_scene', 'view_map', 'inspect_objects', 'search_candidates'])
    call.add_argument('--args', type=Path, help='JSON input file; default empty object')
    call.add_argument('--output', type=Path, required=True, help='New output directory')
    call.add_argument('--encoder-config', type=Path)
    args = parser.parse_args()
    if args.command == 'import-mfloor':
        print(import_mfloor(args.source, args.output))
        return
    args.output.mkdir(parents=True, exist_ok=False)
    store = SceneStore(args.scene)
    encoder = None
    if args.encoder_config:
        config = read_json(args.encoder_config)
        encoder = OpenClipEncoder(store.feature_spaces[config['space_id']], config)
    service = MapTools(store, args.output / 'media', encoder)
    result = service.call(args.tool, read_json(args.args) if args.args else {})
    write_json(args.output / 'result.json', result.envelope)
    print(args.output / 'result.json')


if __name__ == '__main__':
    main()
