"""Frozen diagnostic retrieval comparison, not human-ground-truth accuracy."""

import argparse
import gc
from pathlib import Path
from statistics import median
from time import perf_counter

import numpy as np
from PIL import Image

from sementic_map_mcp.descriptions import sources
from sementic_map_mcp.retrieval import OpenClipEncoder
from sementic_map_mcp.store import SceneStore, digest, read_json, require, write_json


PROBES = [
    ("S021", "a grey trash can with a black liner"),
    ("S021", "an open rubbish bin beside a dark cabinet"),
    ("S037", "a black display on a wheeled floor stand"),
    ("S037", "a mobile screen stand with a lower tray holding pens"),
    ("S135", "a small white machine with a dark grey top"),
    ("S135", "a white box shaped device beside a cardboard box"),
    ("indoor-041", "a white fire equipment cabinet"),
    ("indoor-041", "a white metal door with red fire safety instructions"),
    ("S016", "a white office desk with a monitor"),
    ("S016", "a white workstation surrounded by grey office chairs"),
    ("S138", "a grey electrical box attached to a wall"),
    ("S138", "a wall mounted metal box with cables entering from above"),
    ("S005", "a black wall mounted device with a purple screen"),
    ("S005", "an access control screen next to a small red box"),
    ("S010", "a grey mesh office chair with armrests"),
    ("S010", "a wheeled grey chair next to a white desk"),
    (None, "a white cabinet"),
    (None, "a device that looks like a printer"),
]
INSTRUCTION = "Represent the user's input."


def rank_objects(scores, rows, eligible):
    require(len(scores) == len(rows) and np.isfinite(scores).all(), "Invalid scores")
    best = {}
    for score, row in zip(scores, rows, strict=True):
        oid = row["object_id"]
        if oid in eligible:
            best[oid] = max(best.get(oid, -float("inf")), float(score))
    return sorted(best, key=lambda oid: (-best[oid], oid))


def evaluate(query_vectors, matrix, rows, eligible):
    results = []
    for (anchor, query), vector in zip(PROBES, query_vectors, strict=True):
        start = perf_counter()
        order = rank_objects(matrix @ vector, rows, eligible)
        results.append(
            dict(
                query=query,
                anchor=anchor,
                top10=order[:10],
                anchor_rank=order.index(anchor) + 1 if anchor in order else None,
                ranking_seconds=perf_counter() - start,
            )
        )
    return dict(
        results=results,
        anchor_hits={
            str(k): sum(
                r["anchor_rank"] is not None and r["anchor_rank"] <= k
                for r in results
                if r["anchor"]
            )
            for k in [1, 5, 10]
        },
        anchor_queries=sum(bool(r["anchor"]) for r in results),
    )


class QwenEncoder:
    def __init__(self, model_dir):
        from vllm import LLM
        from vllm.config import PoolerConfig

        self.model = LLM(
            model=str(model_dir),
            runner="pooling",
            dtype="bfloat16",
            trust_remote_code=False,
            pooler_config=PoolerConfig(seq_pooling_type="LAST"),
            gpu_memory_utilization=0.40,
            max_model_len=2048,
            max_num_seqs=4,
            max_num_batched_tokens=2048,
            enforce_eager=True,
            limit_mm_per_prompt={"image": 1, "video": 0},
            mm_processor_kwargs={"max_pixels": 262144},
            mm_processor_cache_gb=0,
            enable_prefix_caching=False,
        )
        self.tokenizer = self.model.get_tokenizer()
        self.audit = []

    def encode(self, rows):
        requests = []
        for row in rows:
            content, images = [], {}
            if row.get("image"):
                with Image.open(row["image"]) as image:
                    images = {"image": image.convert("RGB")}
                content.append(dict(type="image"))
            if row.get("text"):
                content.append(dict(type="text", text=row["text"]))
            require(bool(content), "Empty embedding input")
            conversation = [
                dict(role="system", content=[dict(type="text", text=INSTRUCTION)]),
                dict(role="user", content=content),
            ]
            prompt = self.tokenizer.apply_chat_template(
                conversation, tokenize=False, add_generation_prompt=True
            )
            requests.append(dict(prompt=prompt, multi_modal_data=images or None))
        outputs = self.model.embed(requests, use_tqdm=False)
        vectors = np.array([out.outputs.embedding for out in outputs], dtype=np.float32)
        require(
            vectors.shape == (len(rows), 2048) and np.isfinite(vectors).all(),
            "Invalid embedding output",
        )
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        self.audit.append(
            dict(token_lengths=[len(out.prompt_token_ids) for out in outputs])
        )
        return vectors


def main():
    import torch

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--encoder-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    store = SceneStore(args.scene)
    require(store.descriptions is not None, "Embedded descriptions required")
    rows = {"text": [], "image": [], "mixed": []}
    crops = args.output / "crops"
    crops.mkdir()
    for oid, item in sorted(store.objects.items()):
        description = store.descriptions.get(oid)
        if description:
            rows["text"].append(
                dict(
                    object_id=oid,
                    text=description["text"],
                    description_id=description["description_id"],
                )
            )
        for index, ref in enumerate(sources(store, item)):
            with Image.open(store.path(ref["image_asset_id"])) as original:
                image = original.convert("RGB")
                if ref["crop_xyxy"] is not None:
                    image = image.crop(ref["crop_xyxy"])
                path = crops / f"{len(rows['image']):04d}.png"
                image.save(path)
            row = dict(
                object_id=oid,
                image=str(path),
                image_sha256=digest(path),
                source=ref,
                view=index,
            )
            rows["image"].append(row)
            if description:
                rows["mixed"].append(
                    dict(
                        **row,
                        text=description["text"],
                        description_id=description["description_id"],
                    )
                )
    eligible = set.intersection(
        *(set(r["object_id"] for r in group) for group in rows.values())
    )
    config = read_json(args.encoder_config)
    clip_space = store.feature_spaces[config["space_id"]]
    eligible &= {
        o["object_id"]
        for o in store.objects.values()
        if any(
            f["valid"] and f["space_id"] == config["space_id"]
            for f in o["feature_refs"]
        )
    }
    manifest = {
        str(p.relative_to(args.model_dir)): dict(
            bytes=p.stat().st_size, sha256=digest(p)
        )
        for p in sorted(args.model_dir.rglob("*"))
        if p.is_file()
        and ".cache" not in p.parts
        and ".git" not in p.parts
        and p.suffix in [".json", ".safetensors", ".jinja", ".txt"]
    }
    protocol = dict(
        scene_id=store.scene["scene_id"],
        snapshot_id=store.scene["snapshot_id"],
        scene_sha256=digest(store.root / "scene.json"),
        model="Qwen/Qwen3-VL-Embedding-2B",
        weights=manifest,
        instruction=INSTRUCTION,
        batch_size=4,
        max_pixels=262144,
        source_sha256=digest(__file__),
        backend="vllm-pooling-LAST",
        clip_config=config,
        probes=PROBES,
        eligible_ids=sorted(eligible),
        evaluation="Derived diagnostic probes from previously seen images and descriptions; anchors are "
        "not exhaustive human ground truth; no held-out semantic accuracy or full Agent latency.",
        aggregation="maximum view cosine per object; all modes ranked on identical eligible objects",
        rows=rows,
        retries=0,
    )
    write_json(args.output / "protocol.json", protocol)
    report = dict(semantic_accuracy=None, eligible_objects=len(eligible), modes={})

    def query_benchmark(encoder):
        start = perf_counter()
        encoder.encode([dict(text=PROBES[0][1])])
        warmup = perf_counter() - start
        elapsed, vectors = [], []
        for repeat in range(3):
            current = []
            for _, query in PROBES:
                torch.cuda.synchronize()
                start = perf_counter()
                current.append(encoder.encode([dict(text=query)])[0])
                torch.cuda.synchronize()
                elapsed.append(perf_counter() - start)
            if repeat == 0:
                vectors = np.stack(current)
        return vectors, dict(
            warmup_seconds=warmup,
            samples=len(elapsed),
            median_seconds=median(elapsed),
            p95_seconds=float(np.percentile(elapsed, 95)),
        )

    start = perf_counter()
    clip = OpenClipEncoder.from_config(store, args.encoder_config)
    clip_load = perf_counter() - start

    class ClipAdapter:
        def __init__(self, encoder):
            self.encoder = encoder

        def encode(self, group):
            v = self.encoder.encode([r["text"] for r in group])
            return v / np.linalg.norm(v, axis=1, keepdims=True)

    adapter = ClipAdapter(clip)
    queries, timing = query_benchmark(adapter)
    clip_rows, values = [], []
    for oid, item in sorted(store.objects.items()):
        for f in item["feature_refs"]:
            if f["space_id"] == config["space_id"] and f["valid"]:
                values.append(store.arrays[clip_space["matrix_asset_id"]][f["row"]])
                clip_rows.append(dict(object_id=oid))
    matrix = np.array(values, dtype=np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    report["modes"]["clip"] = dict(
        **evaluate(queries, matrix, clip_rows, eligible),
        query_timing=timing,
        load_seconds=clip_load,
        note="Existing source CLIP image index; preprocessing differs from new crop indexes",
    )
    np.save(args.output / "clip-queries.npy", queries, allow_pickle=False)
    write_json(args.output / "results.json", report)
    del clip, adapter
    gc.collect()
    torch.cuda.empty_cache()
    start = perf_counter()
    encoder = QwenEncoder(args.model_dir)
    report["qwen_load_seconds"] = perf_counter() - start
    report["environment"] = dict(
        torch=torch.__version__,
        cuda=torch.version.cuda,
        transformers=__import__("transformers").__version__,
        gpu=torch.cuda.get_device_name(),
    )
    queries, timing = query_benchmark(encoder)
    np.save(args.output / "qwen-queries.npy", queries, allow_pickle=False)
    for mode, group in rows.items():
        start = perf_counter()
        encoder.encode(group[:1])
        torch.cuda.synchronize()
        warmup = perf_counter() - start
        torch.cuda.reset_peak_memory_stats()
        start = perf_counter()
        vectors = []
        for offset in range(0, len(group), 4):
            vectors.append(encoder.encode(group[offset : offset + 4]))
            if offset % 80 == 0:
                print(mode, offset, "/", len(group), flush=True)
        matrix = np.concatenate(vectors)
        torch.cuda.synchronize()
        seconds = perf_counter() - start
        require(
            np.isfinite(matrix).all()
            and np.allclose(np.linalg.norm(matrix, axis=1), 1, atol=1e-4),
            "Invalid normalized vectors",
        )
        np.save(args.output / f"{mode}-index.npy", matrix, allow_pickle=False)
        report["modes"][mode] = dict(
            **evaluate(queries, matrix, group, eligible),
            query_timing=timing,
            index_seconds=seconds,
            warmup_seconds=warmup,
            rows=len(group),
            indexed_objects=len({r["object_id"] for r in group}),
            gpu_memory_scope="vLLM engine runs in a separate process; parent PyTorch peak is not model memory",
        )
        write_json(args.output / "results.json", report)
        print(
            mode,
            report["modes"][mode]["anchor_hits"],
            "index_seconds",
            seconds,
            flush=True,
        )
    write_json(args.output / "token-audit.json", encoder.audit)


if __name__ == "__main__":
    main()
