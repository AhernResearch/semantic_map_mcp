"""Generate one bounded VLM request per object; no retries or scene edits."""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from time import perf_counter
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from jsonschema import validate, ValidationError

from sementic_map_mcp.descriptions import sources, description_id
from sementic_map_mcp.render import Renderer
from sementic_map_mcp.store import SceneStore, digest, read_json, write_json

PROMPT = """Describe the target object in the supplied object images in concise English (40-90 words).
Use visible appearance: color, shape, material, components and immediate placement that distinguish this object.
The full view and object detail refer to the same object; focus on the target, not unrelated background objects.
Use the picture rather than trusting its label. State uncertain type alternatives briefly; do not invent hidden functions or details.
Reply with only JSON: {"target_visible": true or false, "description": "English description"}.
If the target cannot be identified in the images, use target_visible=false and description=""."""
RESPONSE = dict(
    type="object",
    additionalProperties=False,
    required=["target_visible", "description"],
    properties=dict(
        target_visible=dict(type="boolean"),
        description=dict(type="string", maxLength=1000),
    ),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--ids", nargs="+", help="Explicit pilot subset; default all scene objects"
    )
    parser.add_argument("--workers", type=int, choices=range(1, 17), default=8)
    parser.add_argument("--disable-thinking", action="store_true")
    parser.add_argument(
        "--model-manifest", type=Path, help="Pinned local weight hashes"
    )
    args = parser.parse_args()
    token = os.environ.get("MAP_AGENT_API_KEY")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    load_start = perf_counter()
    store = SceneStore(args.scene)
    load_seconds = perf_counter() - load_start
    ids = args.ids if args.ids is not None else sorted(store.objects)
    if len(ids) != len(set(ids)) or not set(ids) <= store.objects.keys():
        raise ValueError("Unknown or duplicate requested object IDs")
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    manifest = dict(
        schema_version="1.0.0",
        scene_id=store.scene["scene_id"],
        snapshot_id=store.scene["snapshot_id"],
        scene_sha256=digest(store.root / "scene.json"),
        model=args.model,
        endpoint=args.endpoint,
        generation=dict(
            prompt=PROMPT,
            temperature=0,
            max_tokens=1000,
            timeout_seconds=60,
            workers=args.workers,
            retries=0,
            created_at=datetime.now(timezone.utc).isoformat(),
            renderer_sha256=digest(
                Path(__file__).parents[1] / "src/sementic_map_mcp/render.py"
            ),
        ),
        complete=False,
        records=[],
    )
    if args.disable_thinking:
        manifest["generation"]["chat_template_kwargs"] = dict(enable_thinking=False)
    if args.model_manifest:
        manifest["generation"]["model_manifest"] = read_json(args.model_manifest)
        manifest["generation"]["model_manifest_sha256"] = digest(args.model_manifest)
    manifest["complete"] = not store.objects
    write_json(args.output / "protocol.json", manifest)
    write_json(args.output / "descriptions.json", manifest)

    def generate(index_oid):
        index, oid = index_oid
        folder = args.output / f"{index:04d}"
        folder.mkdir(mode=0o700)
        item = store.objects[oid]
        refs = sources(store, item)
        row = dict(
            object_id=oid,
            status="unavailable",
            text=None,
            sources=refs,
            provenance=dict(
                model=args.model, trace=f"{index:04d}", object_refs=item["provenance"]
            ),
            error=None,
        )
        object_start = perf_counter()
        timing = dict(request_seconds=None, usage=None)
        if refs:
            renderer = Renderer(store, folder / "media")
            images = []
            audit = []
            for ref in refs:
                link = next(
                    candidate
                    for candidate in item["observation_links"]
                    if candidate["observation_id"] == ref["observation_id"]
                )
                evidence, payload = renderer.evidence(item, link)
                audit.append(dict(evidence=evidence, media=payload.metadata))
                images.append(
                    dict(
                        type="image_url",
                        image_url=dict(
                            url="data:image/png;base64,"
                            + base64.b64encode(payload.content).decode()
                        ),
                    )
                )
            write_json(
                folder / "sources.json", dict(object_id=oid, sources=refs, media=audit)
            )
            body = dict(
                model=args.model,
                temperature=0,
                max_tokens=1000,
                messages=[
                    dict(role="system", content=PROMPT),
                    dict(
                        role="user",
                        content=[
                            dict(type="text", text="Object " + oid + " object images"),
                            *images,
                        ],
                    ),
                ],
            )
            if args.disable_thinking:
                body["chat_template_kwargs"] = dict(enable_thinking=False)
            write_json(folder / "request.json", body)
            start = perf_counter()
            try:
                request = Request(
                    args.endpoint.rstrip("/") + "/chat/completions",
                    data=json.dumps(body).encode(),
                    headers=headers,
                )
                with urlopen(request, timeout=60) as response:
                    data = json.load(response)
                write_json(folder / "response.json", data)
                timing["usage"] = data.get("usage")
                choice = data["choices"][0]
                if choice["finish_reason"] != "stop":
                    raise ValueError("Incomplete VLM response")
                content = choice["message"]["content"].strip()
                if content.startswith("```json\n") and content.endswith("\n```"):
                    content = content[8:-4]
                result = json.loads(content)
                validate(result, RESPONSE)
                if result["target_visible"] and not result["description"].strip():
                    raise ValueError("Empty visible-object description")
                row.update(
                    status="ok" if result["target_visible"] else "unclear",
                    text=result["description"] if result["target_visible"] else None,
                )
            except (
                URLError,
                TimeoutError,
                ValueError,
                ValidationError,
                KeyError,
            ) as error:
                if isinstance(error, HTTPError):
                    with error:
                        (folder / "error-response.txt").write_bytes(error.read())
                row.update(
                    status="error", error=type(error).__name__ + ": " + str(error)
                )
            timing["request_seconds"] = perf_counter() - start
        else:
            row["error"] = "No valid associated object view"
        timing["seconds"] = perf_counter() - object_start
        write_json(folder / "timing.json", timing)
        row["description_id"] = description_id(row)
        write_json(folder / "record.json", row)
        return row

    start = perf_counter()
    completed = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs = {
            pool.submit(generate, (index, oid)): index for index, oid in enumerate(ids)
        }
        for job in as_completed(jobs):
            row = job.result()
            completed[jobs[job]] = row
            manifest["records"] = [completed[index] for index in sorted(completed)]
            manifest["complete"] = len(manifest["records"]) == len(store.objects)
            write_json(args.output / "descriptions.json", manifest)
            print(row["object_id"], row["status"], flush=True)
    elapsed = perf_counter() - start
    counts = {
        status: sum(row["status"] == status for row in completed.values())
        for status in ["ok", "unclear", "unavailable", "error"]
    }
    write_json(
        args.output / "summary.json",
        dict(
            scene_load_seconds=load_seconds,
            batch_seconds=elapsed,
            workers=args.workers,
            requested=len(ids),
            counts=counts,
            successful_objects_per_second=counts["ok"] / elapsed,
        ),
    )


if __name__ == "__main__":
    main()
