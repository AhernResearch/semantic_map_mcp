"""Read-only model-facing tools. No model calls except the injected text encoder."""
from copy import deepcopy
from functools import lru_cache
import json

from jsonschema import Draft202012Validator, ValidationError

from .ports import ToolResult
from .render import FONT, Renderer
from .retrieval import Retriever
from .store import SCHEMAS, read_json, require


class MapTools:
    def __init__(self, store, output, encoder=None, font=FONT, montage_max_width=None, single_max_width=None, descriptions_path=None):
        self.store = store
        from .descriptions import Descriptions
        require(not (descriptions_path and store.descriptions),
                'Scene already embeds descriptions; an external override is not allowed')
        self.descriptions = Descriptions(store, descriptions_path) if descriptions_path else store.descriptions
        self.retriever = Retriever(store, encoder)
        self.renderer = Renderer(store, output, font, montage_max_width, single_max_width)
        self.schemas = {t['name']: t for t in read_json(SCHEMAS / 'tools.json')['tools']}
        # One immutable snapshot and encoder per instance; bound image memory to 32 requests.
        self._execute = lru_cache(maxsize=32)(self._execute)

    def call(self, tool, arguments):
        if tool not in self.schemas:
            raise ValueError(f'unknown tool: {tool}')
        try:
            Draft202012Validator(self.schemas[tool]['inputSchema']).validate(arguments)
            if any(not q.strip() for q in arguments.get('queries', [])):
                raise ValueError('query cannot be whitespace')
        except (ValidationError, ValueError) as error:
            return self._result(tool, None, errors=[self._error('invalid_request', str(error), [])],
                                status='invalid_request')
        return deepcopy(self._execute(tool, json.dumps(arguments, sort_keys=True)))

    def _execute(self, tool, arguments_json):
        return getattr(self, '_' + tool)(**json.loads(arguments_json))

    def list_scene(self):
        return self.call('list_scene', {})

    def view_map(self, room_ids=(), queries=()):
        return self.call('view_map', dict(room_ids=list(room_ids), queries=list(queries)))

    def search_candidates(self, queries, room_ids=()):
        return self.call('search_candidates', dict(queries=list(queries), room_ids=list(room_ids)))

    def inspect_objects(self, ids):
        return self.call('inspect_objects', dict(ids=list(ids)))

    @staticmethod
    def _error(code, message, ids):
        return dict(code=code, message=message, affected_ids=list(ids))

    def _result(self, tool, data, media=(), errors=(), limitations=(), status=None):
        envelope = dict(schema_version='1.0.0', tool=tool,
                        scene_id=self.store.scene['scene_id'], snapshot_id=self.store.scene['snapshot_id'],
                        status=status or ('partial' if errors else 'ok'), data=data,
                        media=[m.metadata for m in media], errors=list(errors),
                        limitations=list(limitations))
        Draft202012Validator(self.schemas[tool]['outputSchema']).validate(envelope)
        return ToolResult(envelope, tuple(media))

    def _list_scene(self):
        objects = list(self.store.objects.values())
        images = sum(any(link['target_support'] == 'valid' for link in o['observation_links']) for o in objects)
        visual = self.retriever.encoder is not None
        def capability(ready, reason):
            return dict(status='available' if ready else 'unavailable', reason=None if ready else reason)
        def covered(count, missing):
            if 0 < count < len(objects):
                return dict(status='partial', reason=missing)
            return capability(count > 0, missing)
        descriptions = sum(bool(self.descriptions.get(o['object_id'])) for o in objects) if self.descriptions else 0
        indexed = sum(any(f['space_id'] == self.retriever.encoder.space_id and f['valid']
                          for f in o['feature_refs']) for o in objects) if visual else 0
        coverage = {name: dict(available=count, total=len(objects)) for name, count in
                    [('descriptions', descriptions), ('images', images), ('vectors', indexed)]}
        rooms = [dict(room_id=rid, name=r['name'], kind=r['kind'],
                      object_count=len(scoped := self.store.scoped([rid])),
                      type_ids=sorted({tid for o in scoped for tid in o['type_ids']}))
                 for rid, r in self.store.rooms.items()]
        types = [dict(**t, object_count=sum(tid in o['type_ids'] for o in objects))
                 for tid, t in self.store.types.items()]
        data = dict(scene_name=self.store.scene['name'], rooms=rooms, semantic_types=types,
                    object_count=len(objects), count_unit='object_record',
                    capabilities=dict(label_search=capability(True, ''),
                                      visual_search=capability(visual and self.retriever.channel == 'visual', 'Image-only vector retrieval not active'),
                                      multimodal_search=capability(visual and self.retriever.channel == 'multimodal', 'Mixed vector retrieval not active'),
                                      object_descriptions=covered(descriptions, 'Some or all object descriptions are unavailable'),
                                      object_images=covered(images, 'Some or all valid object images are unavailable')),
                    coverage=coverage,
                    retrieval=dict(mode=self.retriever.channel if visual else 'label', indexed_object_count=indexed),
                    coordinate_frame=self.store.scene['coordinate_frame'],
                    query_policy=dict(policy_id='default-v1', visual_top_k=self.retriever.top_k,
                                      max_queries=self.schemas['search_candidates']['inputSchema']['properties']['queries']['maxItems'],
                                      max_inspect_ids=self.schemas['inspect_objects']['inputSchema']['properties']['ids']['maxItems']))
        return self._result('list_scene', data, limitations=self.store.scene['provenance'])

    def _recall(self, tool, room_ids=(), queries=()):
        unknown = sorted(set(room_ids) - self.store.rooms.keys())
        if unknown:
            return self._result(tool, None, status='invalid_request',
                                errors=[self._error('unknown_room', 'Unknown room IDs', unknown)])
        queries = list(dict.fromkeys(q.strip() for q in queries))
        objects = self.store.scoped(room_ids)
        by_id = {o['object_id']: o for o in objects}
        groups, candidates, errors = [], {}, []
        for query in queries:
            group, hits = self.retriever.search(query, objects)
            groups.append(group)
            for hit in hits:
                oid = hit['object_id']
                if oid not in candidates:
                    item = by_id[oid]
                    candidates[oid] = dict(object_id=oid, label=item['label'], room_ids=item['room_ids'],
                                           description=item['description'],
                                           matched_queries=[], match_basis=[], scores=[])
                row = candidates[oid]
                row['matched_queries'].append(query)
                row['match_basis'] = sorted(set(row['match_basis']) | set(hit['match_basis']))
                row['scores'].append(hit['score'])
        if not queries:
            candidates = {o['object_id']: dict(object_id=o['object_id'], label=o['label'],
                          room_ids=o['room_ids'], matched_queries=[], match_basis=['scope'], scores=[])
                          for o in objects}
        elif self.retriever.encoder is None:
            errors.append(self._error('feature_unavailable', 'Only label search ran; no text encoder', []))
        data = dict(room_ids=list(room_ids), groups=groups, candidates=list(candidates.values()),
                    record_count=len(objects), policy_id='default-v1')
        return self._result(tool, data, errors=errors, limitations=[
            'Candidate similarity is not confirmation; positions are not navigation goals.'])

    def _search_candidates(self, queries, room_ids=()):
        return self._recall('search_candidates', room_ids, queries)

    def _view_map(self, room_ids=(), queries=()):
        recalled = self._recall('search_candidates', room_ids, queries).envelope
        if recalled['data'] is None:
            return self._result('view_map', None, errors=recalled['errors'], status=recalled['status'])
        data = recalled['data']
        objects = [self.store.objects[c['object_id']] for c in data['candidates']]
        maps, media, unshown = self.renderer.maps(objects, room_ids, data['groups'])
        data.update(maps=maps, unshown_ids=unshown)
        errors = recalled['errors']
        if unshown:
            errors.append(self._error('display_budget', 'Narrow room_ids or queries to view remaining IDs', unshown))
        return self._result('view_map', data, media, errors, recalled['limitations'] + [
            'Candidate-local point projection and object bounding-box footprints; not an occupancy map.'])

    def _inspect_objects(self, ids):
        objects, missing, media, errors = [], [], [], []
        for oid in ids:
            if oid not in self.store.objects:
                missing.append(oid)
                continue
            item = self.store.objects[oid]
            row = {k: item[k] for k in ['object_id', 'label', 'type_ids', 'room_ids',
                   'center_xyz', 'bbox', 'attributes', 'identity_status', 'entity_id', 'source_links']}
            row.update(source_refs=item['provenance'], evidence=[], limitations=[])
            links = self.renderer.selected_links(item)
            for link in links:
                evidence, payload = self.renderer.evidence(item, link)
                row['evidence'].append(evidence)
                media.append(payload)
            if not links:
                row['limitations'].append('No associated images')
                errors.append(self._error('asset_missing', 'No associated images', [oid]))
            if any(l['target_support'] != 'valid' for l in links):
                row['limitations'].append('Some views have invalid or unknown target support')
            objects.append(row)
        if missing:
            errors.append(self._error('unknown_id', 'Unknown object IDs', missing))
        data = dict(objects=objects, missing_ids=missing, omitted_ids=[])
        return self._result('inspect_objects', data if objects else None, media, errors,
                            status='not_found' if not objects else None)
