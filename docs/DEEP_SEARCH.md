# deep_search：地图任务委托

MCP宿主的可选第五工具，接收原始指令，在服务端运行地图子Agent，返回可展示答案和结构化定位。原始MapTools四个只读接口不混入在线生成调用。机读契约：[deep_search.schema.json](../schemas/deep_search.schema.json)，完整示例：[deep_search.example.json](../examples/deep_search.example.json)。

## 输入和 context

```json
{
  "instruction": "就会议室那个吧",
  "context": "用户此前想找能坐下来讨论、还能展示内容的地方。上轮提供会议室和走廊休息区两个选项，等待用户选择。",
  "think_mode": "fast"
}
```

instruction必填，1–4096字符且不能全为空白，保留用户原话。context为可选纯文本，默认空串、最多8192字符，由调用方填写理解当前指令必需的前文摘要：原始目标、已给出的候选、用户补充。独立完整指令省略context；不需要整个聊天记录、系统提示或工具trace。工具不保存跨调用对话，澄清仍由同一接口携带必要摘要。context里的对象ID不等于已检索对象，不能替代证据校验。

调用方可直接传中文。英文规则只作用于子Agent发出的search_candidates/view_map.queries。think_mode可选fast/middle/strong，省略为fast；未知值拒绝。它选择服务端预置模型，不能覆盖endpoint、模型ID、认证或预算；任务中不换档，不自动重试。

| think_mode | 当前配置 | 接口与推理 |
|---|---|---|
| fast（默认） | 本地Qwen3.5-9B，FP8加载 | Chat Completions；服务端关闭thinking |
| middle | claude-sonnet-5 | Chat Completions；原生工具调用 |
| strong | gpt-6-sol | Responses；reasoning.effort=medium |

GPT-6 Sol在Chat Completions中仅关闭推理时支持工具调用，因此strong采用Responses，使用本项目responses.py适配器，保留推理项、工具call_id及图片。[官方模型文档](https://developers.openai.com/api/docs/models/gpt-6-sol)。本次网关Sonnet 5.5拒绝required工具调用，middle显式配置Sonnet 5；没有运行中回退。

## 返回

公共envelope包含scene_id、snapshot_id、status、trace_id、execution、metrics、data、media、errors、limitations。execution记录实际think_mode、model和api_format；输入校验失败时为null。data包括：

- answer：直接展示的回答，来自question或reason，无额外模型总结调用。
- grounding：status、object_ids、room_ids、evidence_ids、reason、question、excluded_ids。
- selection：selected_id、selected_room_id、candidate_ids、candidate_room_ids、excluded_ids。
- evidence：实际引用的描述/卡片片段，或子Agent已查看的图片引用；默认不向外层重复发图。
- navigation与handoff：当前仍为goal=null、handoff=not_ready，没有机器人执行。

纯区域resolved使用唯一room_id及支持该区域的card_id，不必寻找物体锚点。物体resolved须唯一object_id且真正获得对应描述/图片证据；可同时给出其所属room_ids，宿主校验归属。卡片不能代替物体证据，空卡片不能支持区域resolved。

grounding.status为resolved/ambiguous/insufficient_evidence/needs_context/cancelled。外层status=ok表示得到结构与引用合规的结果，可能是澄清或证据不足，不表示语义真值。缺少前文指代时用needs_context并填写question。

执行错误分开返回：invalid_request拒绝非法参数；unavailable的errors.code为busy、deadline_exceeded、agent_protocol_error、model_error、upstream_error或cancelled。失败data=null。自然语言代替finish会拒收，不猜测答案或修复JSON。

## 执行与预算

复用唯一agent.run_agent循环；子Agent只看到原四工具和本地finish，不包含deep_search。区域卡片作为稳定场景数据前缀预置，用户原话和context另行追加。保留完整历史和实际图片字节。要求原生工具调用，最后预算轮次限定finish；仍验证完整schema、快照、对象、区域和证据，不自动重试或换模型。三档共享总并发槽，不共享会话。

默认6轮、总响应预算45秒、最多2任务并发。模型HTTP请求使用剩余预算，并发满返回busy。共享渲染/cache锁只覆盖底层数据工具调用，不覆盖模型等待。取消/超时取消当前模型请求，阻止后续模型调用。

Python线程中的当前只读数据工具无法强制中断，会完成或按自身超时退出（当前HTTP嵌入超时30秒）；期间保留并发槽，后台不再继续推理。测试覆盖取消、超时、忙碌拒绝和慢工具后的槽释放。metrics分别记录seconds、model_calls、tool_calls、model_seconds、tool_seconds。外层Agent发起/转述的耗时应单独计算，一次MCP调用仍可能包含多次模型请求。

## 配置和启动

[配置契约](../schemas/deep_search_config.schema.json)、[配置示例](../examples/deep_search_config.example.json)。profiles必须明确包含fast、middle、strong，每项配置endpoint、model，可选api_format（默认chat_completions）、output_tokens、reasoning_effort、api_key_env。需要推理和工具同时使用时采用responses。api_key_env只引用进程环境变量，不保存密钥；仅无认证的本地服务省略此项。Responses适配器位于本项目，不依赖Harness。旧单模型配置需显式改为三档，不隐式复制到各档。

本地vLLM生成语法不支持uniqueItems，fast显式配置omit_unique_items=true，仅从wire生成schema移除这个关键词；宿主仍用完整契约检查参数和决策，不自动去重。原始schema和实际wire请求分别保留。

cards_path为绝对路径；卡片按[semantic_cards.schema.json](../schemas/semantic_cards.schema.json)绑定schema_version=1.0.0、源场景/快照/scene.json哈希和支持对象的description_id/text。必须覆盖全部区域记录，card_id按快照及内容生成。示例[semantic_cards.example.json](../examples/semantic_cards.example.json)展示尚无描述时的卡片；旧试验文件通过导出新版本文件接入，不覆盖源包或旧审计。

```bash
export PYTHONPATH=/path/to/sementic_map_mcp/src
# 从既有私有配置向进程注入 MAP_AGENT_API_KEY。
python -m sementic_map_mcp.semantic_map_http \
  --scene /absolute/path/to/snapshot \
  --encoder-config /absolute/path/to/encoder.json \
  --deep-search-config /absolute/path/to/deep-search-config.json \
  --output /absolute/path/to/new-private-trace \
  --host 127.0.0.1 --port 8882 --token-file /private/path/to/mcp-token
```

Python会话使用MAP_DEEP_SEARCH_CONFIG指向同一配置；未配置时仍只注册原四工具。直接调用用`await tools.call_tool('deep_search', arguments)`；该工具异步，不能同步invoke。

## 当前三档试验服务（2026-11-12）

用户已授权停止本地4B并运行9B、通过MCP试用三档。入口为`http://172.22.162.253:8880/mcp`，认证沿用原8880的Bearer令牌，客户端需刷新工具列表。8879主入口和Embedding服务的PID保持不变。8880现在注册四个底层工具与deep_search。

```json
{"instruction":"找个能坐下来讨论、还能展示内容的地方","think_mode":"fast"}
```

9B从ModelScope官方Qwen/Qwen3.5-9B下载，约18GiB文件，SHA256及分片索引校验通过。源权重为BF16，vLLM 0.17.1加载时量化FP8；本机4090实际模型加载约10.8GiB，服务进程GPU占用约15GiB。监听127.0.0.1:8892，context=16384、单并发、关闭thinking；API不直接暴露到内网。4B描述服务已停，9B与Embedding并存。

当前MCP拥有者为`map-deep-modes-lan-84a324d71e36.service`，模型拥有者为`qwen35-9b-d0e9781ce983.service`。冻结源码、配置、卡片、launch/owner、认证与低层工具验收保存在`/home/agiuser/lx_workspace/runtime/map-mcp/map-deep-modes-lan-84a324d71e36/`。当前身份索引为`runtime/map-mcp/preview-current.json`及`runtime/map-mixed-mainline/mcp-current.json`；重启必须新建拥有者与输出目录。模型索引为`runtime/qwen35-9b/current.json`，源下载清单在`runtime/qwen35-9b/20261112T112519Z/manifest.json`。

同一会议区域指令在热态LAN MCP上的一次调用：

| 模式 | MCP总耗时 | 内部模型调用 | 底层数据工具调用 |
|---|---:|---:|---:|
| fast（省略参数，验证默认路由） | 2.45秒 | 1 | 0 |
| middle | 5.21秒 | 1 | 0 |
| strong | 2.78秒 | 1 | 0 |

这是单例，不代表普遍速度排序。另有4类指令在三档串行轮换测试：会议区域、灰色黑袋垃圾桶、带前文指代、无前文指代。各档4次均返回结构与引用合规的结果，平均分别为fast 7.47秒（含服务重启后首请求15.58秒）、middle 6.38秒、strong 3.41秒。物体任务中fast查看3张图后选择S045，middle澄清S045/S021，strong根据描述选择S045；模型路径和结果不同，不将差异解释为纯模型/看图的因果收益。

fast有明确限制：无前文“就那个吧”误选会议室，虽然引用合法，语义判断不合格；额外要求查看S045图片的任务引用了其他物体的描述，宿主按完整证据契约拒收。该额外任务耗时5.48秒，未计入上面4类任务均值。fast保留为本地试验档，不把12/12流程合规当语义正确；没有现场语义真值，semantic_accuracy=null。不自动切换到远程模型。

先前失败和修复均保留：trial-01的本地请求因uniqueItems不受支持而拒绝；trial-02出现多目标/状态填写错误，三图请求达到8103/8192 tokens，只剩89 tokens输出预算并截断。随后扩到16K、补充目标与状态说明后重新冻结为trial-03。没有自动重试或JSON修复。运行记录位于`/home/agiuser/lx_workspace/runtime/deep-search-modes/20261112T114221Z/`，validation.json含逐请求路由、英文查询、分页、图片字节与provider输出重放检查。孤立验收8882服务已停止。

验收包括无令牌401、三档枚举与fast默认值、非法模式不触发模型请求、候选返回文本、物体PNG及多页空间图。40项地图unittest、6项harness地图测试通过；全量harness为252通过、2项既有reset_context描述断言失败，与本次改动无关。Ruff、mypy和前端构建通过。最初LAN校验脚本误以为空间图仅一页；实际两页均有效，已使用保存的原响应修正验证，没有重复运行模型来覆盖结果。

## 审计

deep-search/保存冻结配置、原始场景结果、模型场景前缀及卡片依据。每调用有独立trace_id目录，保存输入、完整模型请求/响应（*-wire-request.json、*-wire-response.json是实际HTTP正文）、原始工具结果、模型投影、派生图片审计、结果/指标与worker-stopped.json。Responses归一化响应另存*-response.json，不能冒充原始wire响应；agent协议文件记录循环参数，生成参数以wire请求为准。并发任务不共享对话或编号，凭据不进入记录。合成结构测试与真实模型验证分别报告，语义准确率没有现场真值时保持null。
