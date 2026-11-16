"""Export a snapshot with validated, frozen image/text embeddings."""

import argparse
from copy import deepcopy
from pathlib import Path
import shutil

import numpy as np

from sementic_map_mcp.descriptions import sources
from sementic_map_mcp.multimodal import fingerprint
from sementic_map_mcp.store import (
    SceneStore,
    digest,
    package_path,
    read_json,
    require,
    write_json,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["scene", "trial", "output", "query-template"]:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--snapshot-id", required=True)
    args = parser.parse_args()
    store = SceneStore(args.scene)
    protocol = read_json(args.trial / "protocol.json")
    require(
        protocol["scene_sha256"] == digest(store.root / "scene.json"),
        "Trial snapshot mismatch",
    )
    require(
        args.snapshot_id != store.scene["snapshot_id"] and args.snapshot_id.strip(),
        "New snapshot required",
    )
    require(
        not args.output.resolve().is_relative_to(store.root),
        "Output must be outside source package",
    )
    rows = protocol["rows"]["mixed"]
    for row in rows:
        item = store.objects[row["object_id"]]
        require(row["source"] in sources(store, item), "Trial image binding mismatch")
        require(
            store.descriptions.get(item["object_id"])["description_id"]
            == row["description_id"],
            "Trial description mismatch",
        )
        crop = Path(row["image"]).resolve()
        require(
            crop.is_relative_to(args.trial.resolve())
            and digest(crop) == row["image_sha256"],
            "Trial crop path/hash mismatch",
        )
    matrix = np.load(args.trial / "mixed-index.npy", allow_pickle=False)
    require(
        matrix.shape == (len(rows), 2048)
        and np.isfinite(matrix).all()
        and np.allclose(np.linalg.norm(matrix, axis=1), 1, atol=1e-4),
        "Invalid mixed matrix",
    )
    template = args.query_template.read_text()
    require(template.count("{query}") == 1, "Invalid query template")
    settings = dict(
        instruction=protocol["instruction"],
        max_pixels=protocol["max_pixels"],
        pooling="LAST",
        normalized=True,
        aggregation="max_per_object",
        query_template=template,
    )
    model = dict(
        model_id=protocol["model"], source="ModelScope", files=protocol["weights"]
    )
    weights = fingerprint(model)
    preprocess = fingerprint(settings)
    sid = "qwen3vl-mixed-2b"
    aid = "qwen3vl-mixed-matrix"
    require(
        sid not in store.feature_spaces and aid not in store.assets,
        "Mixed index already exists",
    )
    scene = deepcopy(store.scene)
    scene["snapshot_id"] = args.snapshot_id
    for row_index, row in enumerate(rows):
        next(o for o in scene["objects"] if o["object_id"] == row["object_id"])[
            "feature_refs"
        ].append(dict(space_id=sid, row=row_index, valid=True))
    metadata = dict(
        kind="image_text",
        pooling="LAST",
        weights_fingerprint=weights,
        preprocess_fingerprint=preprocess,
        query_template=template,
        model_manifest=model,
        settings=settings,
        source_protocol_sha256=digest(args.trial / "protocol.json"),
        rows=[
            dict(
                object_id=r["object_id"],
                description_id=r["description_id"],
                source=r["source"],
                crop_sha256=r["image_sha256"],
            )
            for r in rows
        ],
    )
    scene["feature_spaces"].append(
        dict(
            space_id=sid,
            encoder=protocol["model"],
            weights_fingerprint=weights,
            preprocess_fingerprint=preprocess,
            dim=2048,
            normalized=True,
            matrix_asset_id=aid,
        )
    )
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    for asset in store.assets.values():
        target = package_path(args.output, asset["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(store.path(asset["asset_id"]), target)
    path = args.output / "assets/qwen3vl-mixed.npy"
    np.save(path, matrix, allow_pickle=False)
    scene["assets"].append(
        dict(
            asset_id=aid,
            path="assets/qwen3vl-mixed.npy",
            sha256=digest(path),
            kind="feature_matrix",
            metadata=dict(
                shape=list(matrix.shape),
                dtype=str(matrix.dtype),
                feature_space_id=sid,
                multimodal_index=metadata,
            ),
        )
    )
    scene["provenance"].append(
        "Mixed index derived from snapshot " + store.scene["snapshot_id"]
    )
    write_json(args.output / "scene.json", scene)
    SceneStore(args.output)
    print(args.output / "scene.json")


if __name__ == "__main__":
    main()
