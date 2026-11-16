"""Run real model decisions through a live read-only MCP, without retries."""

import argparse
import asyncio
import base64
from pathlib import Path
from time import perf_counter
from urllib.error import HTTPError, URLError

from jsonschema import ValidationError
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from sementic_map_mcp.agent import chat, model_tools, run_agent
from sementic_map_mcp.ports import MediaPayload, ToolResult
from sementic_map_mcp.service import MapTools
from sementic_map_mcp.store import SceneStore, digest, read_json, require, write_json

CASES = [
    ("E01", "去那个像打印机的设备那里"),
    ("E02", "找那个内衬黑色垃圾袋、旁边是深色柜子的灰色垃圾桶"),
    ("E03", "找墙上紫色屏幕、旁边有红色小盒子的门禁设备"),
    ("E04", "去那个白色柜子那里"),
    ("E05", "找离那个带轮子的黑色大屏最近的垃圾桶"),
]


def decode_reply(reply):
    require(
        bool(reply.content) and reply.content[0].type == "text",
        "MCP context is missing",
    )
    import json

    context = json.loads(reply.content[0].text)
    images = [part for part in reply.content if part.type == "image"]
    require(len(images) == len(context["media"]), "MCP media mapping mismatch")
    media = []
    for metadata, image in zip(context["media"], images, strict=True):
        title_index = reply.content.index(image) - 1
        title = reply.content[title_index]
        require(
            title.type == "text"
            and title.text.endswith("media_id=" + metadata["media_id"]),
            "MCP image title/ID mismatch",
        )
        media.append(
            MediaPayload(metadata, base64.b64decode(image.data, validate=True))
        )
    return ToolResult(reply.model_dump(), tuple(media)), context, tuple(media), None


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", required=True)
    parser.add_argument("--mcp-url", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    tick = perf_counter()
    store = SceneStore(args.scene)
    service = MapTools(store, args.output / "unused-local-media")
    loading = perf_counter() - tick
    token = args.token_file.read_text().strip()
    loop = asyncio.get_running_loop()
    report = dict(
        model=args.model,
        endpoint=args.endpoint,
        mcp_url=args.mcp_url,
        scene_id=store.scene["scene_id"],
        snapshot_id=store.scene["snapshot_id"],
        scene_load_seconds=loading,
        semantic_accuracy=None,
        cases=[],
    )
    write_json(
        args.output / "session-protocol.json",
        dict(
            **report,
            tasks=CASES,
            max_turns=8,
            max_tokens=1600,
            temperature=0,
            retries=0,
            model_socket_timeout_seconds=120,
            raw_tool_kind="raw MCP transport reply; raw MapTools JSON is retained by the MCP server",
            scope="Derived cases; no field ground truth and no robot movement",
            script_sha256=digest(__file__),
            agent_sha256=digest(
                Path(__file__).parents[1] / "src/sementic_map_mcp/agent.py"
            ),
        ),
    )
    async with streamablehttp_client(
        args.mcp_url, headers={"Authorization": "Bearer " + token}
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            tick = perf_counter()
            await session.initialize()
            listed = await session.list_tools()
            definitions = [
                dict(
                    type="function",
                    function=dict(
                        name=t.name, description=t.description, parameters=t.inputSchema
                    ),
                )
                for t in listed.tools
            ]
            definitions.append(model_tools(use_descriptions=True)[-1])
            write_json(args.output / "mcp-tools.json", listed.model_dump())
            preflight = await session.call_tool("list_scene", {})
            _, ctx, _, _ = decode_reply(preflight)
            require(
                ctx["scene_id"] == store.scene["scene_id"]
                and ctx["snapshot_id"] == store.scene["snapshot_id"],
                "Local host / MCP snapshot mismatch",
            )
            write_json(
                args.output / "preflight-mcp-response.json", preflight.model_dump()
            )
            report["mcp_preflight_seconds"] = perf_counter() - tick
            for case_id, utterance in CASES:
                folder = args.output / case_id
                calls = []
                counter = 0

                def complete(messages):
                    nonlocal counter
                    path = folder / f"{counter:02d}-wire-request.json"
                    counter += 1
                    return chat(
                        args.endpoint,
                        args.model,
                        messages,
                        use_descriptions=True,
                        tools=definitions,
                        request_log=path,
                    )

                def execute(_service, name, arguments, _packing):
                    start = perf_counter()
                    reply = asyncio.run_coroutine_threadsafe(
                        session.call_tool(name, arguments), loop
                    ).result(timeout=60)
                    require(not reply.isError, "MCP call failed: " + str(reply.content))
                    decoded = decode_reply(reply)
                    require(
                        decoded[1]["snapshot_id"] == store.scene["snapshot_id"],
                        "MCP snapshot changed",
                    )
                    calls.append(
                        dict(
                            tool=name,
                            arguments=arguments,
                            seconds=perf_counter() - start,
                            images=len(decoded[2]),
                            candidate_ids=[
                                c["object_id"]
                                for c in (decoded[1]["data"] or {}).get(
                                    "candidates", []
                                )
                            ],
                        )
                    )
                    return decoded

                start = perf_counter()
                row = dict(case_id=case_id, utterance=utterance)
                try:
                    result = await asyncio.to_thread(
                        run_agent,
                        service,
                        utterance,
                        folder,
                        complete,
                        max_turns=8,
                        tool_executor=execute,
                        tool_definitions=definitions,
                    )
                    row.update(
                        execution="completed_not_semantically_verified", result=result
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
                            (folder / "http-error-body.txt").write_bytes(error.read())
                    row.update(
                        execution="failed",
                        error_type=type(error).__name__,
                        error=str(error),
                    )
                row.update(
                    seconds=perf_counter() - start,
                    model_requests=counter,
                    tool_calls=calls,
                )
                timings = read_json(folder / "timings.json")["events"]
                row["model_seconds"] = sum(
                    t["seconds"] for t in timings if t["stage"] == "model"
                )
                row["tool_seconds"] = sum(
                    t["seconds"] for t in timings if t["stage"] == "tool"
                )
                row["images"] = sum(c["images"] for c in calls)
                report["cases"].append(row)
                write_json(args.output / "report.json", report)
                print(
                    case_id,
                    row["execution"],
                    round(row["seconds"], 2),
                    (row.get("result") or {}).get("selection"),
                    flush=True,
                )


if __name__ == "__main__":
    asyncio.run(main())
