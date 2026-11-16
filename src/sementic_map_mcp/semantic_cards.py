"""Snapshot-bound area summaries with description-backed citations."""

from copy import deepcopy
import hashlib
import json

from jsonschema import validate

from .store import SCHEMAS, digest, read_json, require


class SemanticCards:
    def __init__(self, store, path):
        self.data = read_json(path)
        validate(self.data, read_json(SCHEMAS / 'semantic_cards.schema.json'))
        for key in ('scene_id', 'snapshot_id'):
            require(self.data[key] == store.scene[key], 'Card snapshot mismatch')
        require(self.data['scene_sha256'] == digest(store.root / 'scene.json'), 'Card scene hash mismatch')
        self.cards = {}
        self.room_evidence = {}
        self.empty_record_room_ids = [rid for rid in store.rooms if not store.scoped([rid])]
        for source in self.data['cards']:
            rid = source['room_id']
            require(rid in store.rooms and rid not in self.cards, 'Unknown or duplicate card room')
            require(source['name'] == store.rooms[rid]['name'], 'Card room name mismatch')
            used = {oid for item in source['main_objects'] + source['distinctive_features']
                    for oid in item['object_ids']}
            require(set(self.data['evidence'][rid]) == used, 'Card evidence coverage mismatch')
            for oid in used:
                require(oid in store.objects and rid in store.objects[oid]['room_ids'], 'Card area mismatch')
                require(store.descriptions is not None
                        and store.descriptions.get(oid) == self.data['evidence'][rid][oid],
                        'Card description binding mismatch')
            body = dict(snapshot=self.data['scene_sha256'], card=source)
            cid = 'card-' + hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            self.cards[rid] = dict(card_id=cid, room_id=rid, name=source['name'], summary=source['summary'],
                                  main_objects=[i['name'] for i in source['main_objects']],
                                  distinctive_features=[i['text'] for i in source['distinctive_features']])
            if used:
                self.room_evidence[cid] = {rid}
        require(self.cards.keys() == store.rooms.keys(), 'Cards must cover all registered rooms')
        require(self.data['evidence'].keys() == store.rooms.keys(), 'Unknown card evidence room')

    def enrich(self, context):
        context = deepcopy(context)
        if context['data'] is not None:
            context['data']['semantic_cards'] = deepcopy(list(self.cards.values()))
            context['data']['semantic_card_coverage'] = dict(
                available_room_ids=list(self.cards), not_generated_room_ids=[],
                empty_record_room_ids=self.empty_record_room_ids)
        return context
