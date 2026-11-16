# 离线物体描述

本地 Qwen3.5-4B 生成英文外观描述，先保存为包外sidecar，再显式导出含描述的统一场景新快照。源标签、图片和检索向量不变。文本是模型对关联图片的判断，不是人工真值；无有效关联图片的对象标记 `unavailable`。

已完成的部署、并发对照和191对象全量结果见[实测记录](QWEN35_VLLM_20261112.md)。

## 本地 vLLM

权重来自 ModelScope `Qwen/Qwen3.5-4B`，保存在 `/home/agiuser/lx_workspace/models/Qwen3.5-4B`。逐文件 SHA256 清单为 `/home/agiuser/lx_workspace/runtime/qwen35-vl/model-manifest.json`。独立运行环境为 `/home/agiuser/lx_workspace/envs/qwen35-vllm`。

运行服务、启动命令和验证结果以 `/home/agiuser/lx_workspace/runtime/qwen35-vllm/current.json` 指向的目录为准。只监听 `127.0.0.1:8890`，接口 `http://127.0.0.1:8890/v1`，模型名 `qwen35-4b`。该本机接口不要求令牌，地图 MCP 的认证不覆盖它。

参考：[vLLM GPU 安装](https://docs.vllm.ai/en/v0.17.1/getting_started/installation/gpu/)、[Qwen3.5 部署说明](https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3.5.html)。实际 CUDA/PyTorch 版本、参数和实测结果保存在运行目录；不根据版本标签推定兼容。

## 并发生成

```bash
cd /home/agiuser/lx_workspace/sementic_map_mcp
PYTHONPATH=src /home/agiuser/lx_workspace/envs/qwen35-vl/bin/python \
  scripts/build_descriptions.py \
  --scene local_data/mfloor-v1 \
  --output /home/agiuser/lx_workspace/runtime/map-descriptions/NEW_RUN \
  --endpoint http://127.0.0.1:8890/v1 --model qwen35-4b \
  --workers 8 --disable-thinking \
  --model-manifest /home/agiuser/lx_workspace/runtime/qwen35-vl/model-manifest.json
```

每对象最多两个 `target_support=valid` 的关联视角，每视角使用既有渲染器的物体证据图。每对象一次请求，最多1000个输出token、60秒超时，零重试。`--workers` 支持1–16，`--ids S135 S021` 指定子集。输出目录必须不存在。远程认证接口可通过 `MAP_AGENT_API_KEY` 注入令牌，令牌不落盘；本机接口无需该变量。

`protocol.json` 保存协议；`descriptions.json` 按对象顺序保存结果，每项完成即更新。编号目录保存实际图片字节、请求、响应、来源、状态和计时。`summary.json` 区分场景加载、整批墙钟时间与各状态数量。`complete=true` 只表示全部对象都有记录，包含失败和缺图，不等于描述全部成功。失败不自动换模型、换视角或重试。

## 写入统一场景

```bash
PYTHONPATH=src /home/agiuser/lx_workspace/envs/qwen35-vl/bin/python \
  -m sementic_map_mcp embed-descriptions \
  local_data/mfloor-v1 \
  /home/agiuser/lx_workspace/runtime/map-descriptions/qwen35-vllm-full-20261112/descriptions.json \
  /path/to/NEW_SCENE_PACKAGE --snapshot-id NEW_DISTINCT_SNAPSHOT
```

Python接口为embed_descriptions(scene_path, descriptions_path, output, snapshot_id)。成功文本写入objects[].description；description_metadata保存原description、生成状态、description_id、来源和错误；description_generation保存模型、权重哈希、生成协议、原快照和原描述文件哈希。注册资产完整复制，输出目录必须不存在且位于源包之外。

已导出：`/home/agiuser/lx_workspace/data/map_query_tools/mfloor-qwen35-20261112/scene.json`。191个对象中189条成功、S094为unclear、S143为unavailable；原有68条描述均保留。原始生成日志继续位于runtime/map-descriptions。

实际包验收：全部575项资产哈希、原标签/几何/特征和191条描述记录保持对应；原OCR“天虹物业”仍召回S040；英文trash bin召回包含S021，模型搜索上下文返回描述且0张图。24项unittest通过。报告：`/home/agiuser/lx_workspace/runtime/map-descriptions/embedded-map-validation-20261112/validation.json`。此为数据契约与通路验收，不是语义准确率。

`store = SceneStore(package)`和`MapTools(store, output, encoder)`自动校验并使用包内描述，无需额外的--descriptions。对象ID字符串保留，客户端仍需使用新snapshot_id；旧任务集和sidecar的快照绑定不会自动改写。[字段示例](../examples/embedded_descriptions.example.json)是结构片段，不是完整包。

## 消费与验证

规范见 [Schema](../schemas/descriptions.schema.json) 和 [示例](../examples/descriptions.example.json)。加载时验证场景/快照、scene.json哈希、对象唯一性、图片哈希/裁剪/观测绑定、模型名和描述内容哈希。可将模型权重清单随协议保存。

旧包可指定`MapTools(store, output, encoder, descriptions_path=sidecar)`；已经内嵌描述的场景禁止sidecar覆盖。搜索模型上下文附加visual_description={description_id,text}，不自动生成搜索拼图；缺项明确标记，需要时继续inspect_objects。原始搜索JSON结构不变，新包的description和保留的原始描述均参与既有文本召回。Agent可引用对应description_id，未经绑定的导航站位仍为null。

`scripts/run_grounding_agent.py --descriptions PATH` 同时配置宿主与模型工具说明。原始工具结果、模型上下文、描述审计分别保存，不能将全部来源字段称作已发给模型。已有 MCP 服务不会因生成 sidecar 自动切换。
