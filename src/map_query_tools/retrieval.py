"""Label and object-image vector recall, with explicit encoder identity."""
import unicodedata

import numpy as np

from .ports import TextEncoder
from .store import require


def normalize(text):
    return unicodedata.normalize('NFKC', text).strip().casefold()


class OpenClipEncoder:
    def __init__(self, space, config):
        import open_clip
        import torch
        from .store import digest
        checkpoint, device = config['checkpoint'], config.get('device', 'cpu')
        require(config['encoder'] == space['encoder'] and
                config['weights_fingerprint'] == space['weights_fingerprint'] and
                config['preprocess_fingerprint'] == space['preprocess_fingerprint'],
                'configured encoder does not match stored feature space')
        require(digest(checkpoint) == config['checkpoint_sha256'], 'checkpoint hash mismatch')
        self.space_id = space['space_id']
        self.fingerprint = config['weights_fingerprint']
        self.checkpoint_sha256 = config['checkpoint_sha256']
        self.device, self.torch = device, torch
        self.model, _, _ = open_clip.create_model_and_transforms(
            space['encoder'], pretrained=str(checkpoint), device=device)
        self.model.eval()
        self.tokenizer = open_clip.get_tokenizer(space['encoder'])

    def encode(self, texts):
        with self.torch.inference_mode():
            return self.model.encode_text(self.tokenizer(texts).to(self.device)).float().cpu().numpy()


class Retriever:
    def __init__(self, store, encoder: TextEncoder | None = None, top_k=10):
        self.store, self.encoder, self.top_k = store, encoder, top_k
        self.space = None
        if encoder is not None:
            self.space = store.feature_spaces[encoder.space_id]
            require(encoder.fingerprint == self.space['weights_fingerprint'], 'encoder identity mismatch')

    def search(self, query, objects):
        q = normalize(query)
        matched = []
        for item in objects:
            names = [item['label'], *item['aliases']]
            for tid in item['type_ids']:
                t = self.store.types[tid]
                names.extend([t['name'], *t['aliases']])
            names = [normalize(n) for n in names]
            details = normalize(' '.join([item['description'] or '',
                               *[str(a['value']) for a in item['attributes']]]))
            if q in names:
                matched.append((0, item['object_id'], 'label_exact'))
            elif any(q in n for n in names):
                matched.append((1, item['object_id'], 'label_substring'))
            elif q in details:
                matched.append((2, item['object_id'], 'description_or_attribute'))
        matched.sort()
        labels = {oid: (rank, basis) for rank, (_, oid, basis) in enumerate(matched, 1)}
        visual = {}
        text = None
        truncated = False
        if self.encoder is not None:
            # Registered English aliases make exact Chinese category queries reproducible.
            text = query
            for t in self.store.types.values():
                if q == normalize(t['name']) and t['aliases']:
                    text = t['aliases'][0]
                    break
            vector = np.asarray(self.encoder.encode([text])[0], dtype=np.float32)
            require(vector.shape == (self.space['dim'],) and np.isfinite(vector).all()
                    and np.linalg.norm(vector) > 0, 'invalid text embedding')
            vector /= np.linalg.norm(vector)
            matrix = self.store.arrays[self.space['matrix_asset_id']]
            scores = []
            for item in objects:
                for feature in item['feature_refs']:
                    if feature['space_id'] == self.encoder.space_id and feature['valid']:
                        v = matrix[feature['row']].astype(np.float32)
                        scores.append((item['object_id'], float(np.clip(v @ vector / np.linalg.norm(v), -1, 1))))
            scores.sort(key=lambda x: (-x[1], x[0]))
            truncated = len(scores) > self.top_k
            visual = {oid: (rank, score) for rank, (oid, score) in enumerate(scores[:self.top_k], 1)}
        rows = []
        for oid in labels.keys() | visual.keys():
            lr, basis = labels[oid] if oid in labels else (None, None)
            vr, cosine = visual[oid] if oid in visual else (None, None)
            rows.append(dict(object_id=oid,
                             match_basis=([basis] if basis else []) + (['visual'] if vr else []),
                             score=dict(query=query, label_rank=lr, visual_cosine=cosine,
                                        fusion_score=sum(1 / (60 + r) for r in (lr, vr) if r))))
        rows.sort(key=lambda x: (-x['score']['fusion_score'], x['object_id']))
        group = dict(query=query, encoded_query=text, label_match_ids=list(labels), visual_match_ids=list(visual),
                     candidate_ids=[r['object_id'] for r in rows],
                     channels_used=['label'] + (['visual'] if self.encoder else []),
                     visual_top_k=self.top_k, truncated=truncated)
        return group, rows
