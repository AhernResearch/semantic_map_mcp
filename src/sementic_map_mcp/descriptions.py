"""Snapshot-bound VLM descriptions, external or embedded in a scene package."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

from jsonschema import validate

from .render import Renderer
from .store import SCHEMAS, digest, package_path, read_json, require, write_json


def sources(store, item):
    rows = []
    for link in Renderer.selected_links(item):
        if link["target_support"] != "valid":
            continue
        obs = store.observations[link["observation_id"]]
        asset = store.assets[obs["image_asset_id"]]
        rows.append(
            dict(
                observation_id=link["observation_id"],
                image_asset_id=obs["image_asset_id"],
                image_sha256=asset["sha256"],
                crop_xyxy=link["crop_xyxy"],
                target_support=link["target_support"],
                source_kind=obs["source_kind"],
                source_refs=obs["provenance"],
            )
        )
    return rows


def description_id(record):
    body = {k: v for k, v in record.items() if k != "description_id"}
    return (
        "desc-"
        + hashlib.sha256(
            json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
    )


class Descriptions:
    def __init__(self, store, path=None):
        if path is not None:
            self.data = read_json(path)
        else:
            header = store.scene["description_generation"]
            records = []
            for item in store.objects.values():
                metadata = item.get("description_metadata")
                if metadata is not None:
                    row = {
                        k: v for k, v in metadata.items() if k != "original_description"
                    }
                    row.update(
                        object_id=item["object_id"],
                        text=item["description"]
                        if metadata["status"] == "ok"
                        else None,
                    )
                    records.append(row)
            self.data = dict(
                schema_version="1.0.0",
                scene_id=store.scene["scene_id"],
                snapshot_id=store.scene["snapshot_id"],
                scene_sha256=digest(store.root / "scene.json"),
                model=header["model"],
                endpoint=header["endpoint"],
                generation=header["generation"],
                complete=header["complete"],
                records=records,
            )
        validate(self.data, read_json(SCHEMAS / "descriptions.schema.json"))
        for key in ["scene_id", "snapshot_id"]:
            require(self.data[key] == store.scene[key], "Description snapshot mismatch")
        require(
            self.data["scene_sha256"] == digest(store.root / "scene.json"),
            "Description scene hash mismatch",
        )
        self.records = {}
        for row in self.data["records"]:
            require(
                row["provenance"].get("model") == self.data["model"],
                "Description model identity mismatch",
            )
            oid = row["object_id"]
            require(
                oid in store.objects and oid not in self.records,
                "Unknown or duplicate description object",
            )
            require(
                row["sources"] == sources(store, store.objects[oid]),
                "Description source binding mismatch",
            )
            require(
                row["description_id"] == description_id(row),
                "Description content hash mismatch",
            )
            require(
                row["status"] != "ok" or bool(row["text"] and row["text"].strip()),
                "Empty description",
            )
            require(
                row["status"] != "ok" or bool(row["sources"]),
                "Description has no valid source",
            )
            require(
                row["status"] == "ok" or row["text"] is None,
                "Unsuccessful description must have null text",
            )
            self.records[oid] = row
        require(
            self.data["complete"] == (set(self.records) == set(store.objects)),
            "Description completeness mismatch",
        )

    def get(self, oid):
        row = self.records.get(oid)
        if row and row["status"] == "ok":
            return dict(description_id=row["description_id"], text=row["text"])
        return None


def embed_descriptions(scene_path, descriptions_path, output, snapshot_id):
    """Export a new, self-contained scene; source package and labels stay immutable."""
    from .store import SceneStore

    store = SceneStore(scene_path)
    require(store.descriptions is None, "Scene already contains generated descriptions")
    cache = Descriptions(store, descriptions_path)
    require(
        bool(snapshot_id.strip()) and snapshot_id != store.scene["snapshot_id"],
        "A distinct nonempty snapshot ID is required",
    )
    output = Path(output).resolve()
    require(
        not output.is_relative_to(store.root), "Output must be outside source package"
    )
    scene = deepcopy(store.scene)
    scene["snapshot_id"] = snapshot_id
    scene["description_generation"] = dict(
        model=cache.data["model"],
        endpoint=cache.data["endpoint"],
        generation=cache.data["generation"],
        complete=cache.data["complete"],
        source_scene=dict(
            scene_id=store.scene["scene_id"],
            snapshot_id=store.scene["snapshot_id"],
            scene_sha256=cache.data["scene_sha256"],
        ),
        source_descriptions_sha256=digest(descriptions_path),
    )
    for item in scene["objects"]:
        row = cache.records.get(item["object_id"])
        if row is not None:
            item["description_metadata"] = dict(
                original_description=item["description"],
                **{k: v for k, v in row.items() if k not in ("object_id", "text")},
            )
            if row["status"] == "ok":
                item["description"] = row["text"]
    scene["provenance"].append(
        "Offline object descriptions embedded; source snapshot "
        + store.scene["snapshot_id"]
    )
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    for asset in store.assets.values():
        destination = package_path(output, asset["path"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(store.path(asset["asset_id"]), destination)
    write_json(output / "scene.json", scene)
    SceneStore(output)
    return output / "scene.json"
