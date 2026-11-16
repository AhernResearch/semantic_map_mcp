"""Validated mixed-image/text indexes and an explicit local embedding client."""

import json
import hashlib
from urllib.request import Request, urlopen

import numpy as np
from jsonschema import validate

from .descriptions import sources
from .store import SCHEMAS, read_json, require


def fingerprint(value):
    return (
        "sha256:"
        + hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    )


def validate_index(store, space):
    asset = store.assets[space["matrix_asset_id"]]
    metadata = asset["metadata"].get("multimodal_index")
    if metadata is None:
        return
    validate(metadata, read_json(SCHEMAS / "multimodal_index.schema.json"))
    require(
        metadata["weights_fingerprint"] == fingerprint(metadata["model_manifest"]),
        "Mixed model manifest fingerprint mismatch",
    )
    require(
        metadata["preprocess_fingerprint"] == fingerprint(metadata["settings"]),
        "Mixed settings fingerprint mismatch",
    )
    require(
        metadata["query_template"] == metadata["settings"]["query_template"],
        "Mixed query template mismatch",
    )
    require(
        space["encoder"] == metadata["model_manifest"]["model_id"],
        "Mixed encoder name mismatch",
    )
    require(metadata["query_template"].count("{query}") == 1, "Invalid query template")
    require(store.descriptions is not None, "Mixed index requires object descriptions")
    require(
        metadata["kind"] == "image_text" and metadata["pooling"] == "LAST",
        "Unsupported mixed index contract",
    )
    require(
        metadata["weights_fingerprint"] == space["weights_fingerprint"],
        "Mixed model mismatch",
    )
    require(
        metadata["preprocess_fingerprint"] == space["preprocess_fingerprint"],
        "Mixed preprocessing mismatch",
    )
    matrix = store.arrays[space["matrix_asset_id"]]
    require(len(metadata["rows"]) == len(matrix), "Mixed index row count mismatch")
    require(
        np.allclose(np.linalg.norm(matrix, axis=1), 1, atol=1e-4),
        "Mixed vectors must be normalized",
    )
    bindings = {
        f["row"]: item["object_id"]
        for item in store.objects.values()
        for f in item["feature_refs"]
        if f["space_id"] == space["space_id"] and f["valid"]
    }
    require(set(bindings) == set(range(len(matrix))), "Mixed feature coverage mismatch")
    for index, row in enumerate(metadata["rows"]):
        require(bindings[index] == row["object_id"], "Mixed row/object mismatch")
        item = store.objects[row["object_id"]]
        description = store.descriptions.get(item["object_id"])
        require(
            description and description["description_id"] == row["description_id"],
            "Mixed description binding mismatch",
        )
        require(row["source"] in sources(store, item), "Mixed image binding mismatch")


class HttpEmbeddingEncoder:
    def __init__(self, store, config):
        self.space_id = config["space_id"]
        space = store.feature_spaces[self.space_id]
        metadata = store.assets[space["matrix_asset_id"]]["metadata"][
            "multimodal_index"
        ]
        validate_index(store, space)
        for key in ["encoder", "weights_fingerprint", "preprocess_fingerprint"]:
            require(config[key] == space[key], "Configured embedding identity mismatch")
        require(
            config["query_template"] == metadata["query_template"],
            "Query template mismatch",
        )
        self.fingerprint = config["weights_fingerprint"]
        self.dim = space["dim"]
        self.config = config

    def encode(self, texts):
        body = dict(
            model=self.config["served_model"],
            encoding_format="float",
            input=[
                self.config["query_template"].replace("{query}", text) for text in texts
            ],
        )
        request = Request(
            self.config["endpoint"].rstrip("/") + "/embeddings",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
        require(
            result["model"] == self.config["served_model"],
            "Embedding response model mismatch",
        )
        rows = sorted(result["data"], key=lambda row: row["index"])
        require(
            [row["index"] for row in rows] == list(range(len(texts))),
            "Embedding response count/index mismatch",
        )
        vectors = np.asarray([row["embedding"] for row in rows], dtype=np.float32)
        require(
            vectors.shape == (len(texts), self.dim) and np.isfinite(vectors).all(),
            "Invalid embedding response vectors",
        )
        return vectors


def load_encoder(store, path):
    config = read_json(path)
    if config.get("backend") == "http_embedding":
        return HttpEmbeddingEncoder(store, config)
    require(
        config.get("backend", "open_clip") == "open_clip", "Unknown encoder backend"
    )
    from .retrieval import OpenClipEncoder

    return OpenClipEncoder.from_config(store, path)
