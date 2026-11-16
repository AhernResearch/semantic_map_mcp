# 候选驱动的看图 Agent 原型

MCP高层[deep_search](DEEP_SEARCH.md)复用本文件对应的唯一run_agent循环，以显式配置启用预置区域卡片、区域或物体决策、严格原生工具提交及截止时间；原有本地Agent默认行为保留。context为必要前文摘要，不赋予对象或证据引用资格。

2026-11-11。目标是验证“检索 → 取证 → 选择或澄清”。这是本地只读原型，不是部署完成或导航验收。

## 执行流程

宿主绑定不可变场景快照，注入四个工具定义及用户指令。模型每轮使用原生 tool_calls 调用工具，或输出一个 JSON action（工具调用/最终 decision）。原生批次最多4个调用，宿主逐个执行并回传对应 tool_call_id，图片通过后续 user 多模态消息传入。建议先 list_scene，再 search_candidates；通过 inspect_objects 获取真实图片。view_map 仅按需调用。

search_candidates 不渲染，使用与 view_map 相同的标签/CLIP召回、候选并集和排名。模型可明确补充英文外观查询，但不得删去原始条件。inspect_objects 的完整 JSON 与图片字节一同送入下一轮；不把返回路径冒充图像输入。

每次调用最多8轮；无自动重试、自动修JSON或替换模型。未知工具/参数按边界处理，截断回答、未知结果ID、未提供的媒体引用、超出轮数预算均不接受决策。API错误由批运行入口记录失败并继续下一个独立用例。

最终通过宿主的 finish 工具提交 decision（不是地图工具，也不执行导航）。finish 必须单独调用；仍接受既有 JSON finish 动作，非JSON正文不会自动解析为决策。

最终 decision 字段：

- status：resolved / ambiguous / insufficient_evidence / needs_context / cancelled。
- object_ids：已检索或检查的对象ID；resolved 必须恰好一个。
- evidence_ids：已提供图片的 media_id；resolved 必须引用包含该对象的证据，并且该对象已经 inspect。
- reason：模型的简短证据解释，包括冲突；不是经过验证的真值。
- question：需要澄清的问题；ambiguous、needs_context 时必填。

宿主始终附加 navigation={status:blocked, goal:null} 与 handoff=not_ready。没有站位绑定、可通行性或真机执行。显式ID请求也暂要求 inspect 后才 resolved，这是本次视觉验证的保守约束。

完整系统 prompt 在 `src/sementic_map_mcp/agent.py:PROMPT`，输出约束在 `schemas/grounding_decision.json`。当前没有复杂空间计算、用户方位、会话确认状态机或全场景未知对象检测。

## 运行

工作区根目录运行；模型必须支持 chat-completions 的 image_url 数据图片输入。endpoint 与 model 由宿主显式指定，不自动发现或启动服务。办公场景仅使用已获授权的内网接口；如需凭据，通过 MAP_AGENT_API_KEY 环境变量注入，不写日志。

```bash
PYTHONPATH=sementic_map_mcp/src HF_HUB_OFFLINE=1 envs/cg/bin/python \
  sementic_map_mcp/scripts/prepare_grounding_cases.py \
  sementic_map_mcp/local_data/mfloor-v1 \
  sementic_map_mcp/runs/mfloor_smoke/encoder.json \
  sementic_map_mcp/runs/pilot-evidence-new

PYTHONPATH=sementic_map_mcp/src HF_HUB_OFFLINE=1 envs/cg/bin/python \
  sementic_map_mcp/scripts/run_grounding_agent.py \
  sementic_map_mcp/local_data/mfloor-v1 \
  sementic_map_mcp/runs/mfloor_smoke/encoder.json \
  sementic_map_mcp/runs/pilot-agent-new \
  --endpoint http://HOST:PORT/v1 --model MODEL
```

输出目录必须不存在。每个 Agent 用例保存逐轮 request（含图片base64）、原始 response、工具 JSON 和最终 result；批次 report 保存失败与模型标识。图片另保存在 media。请求日志是敏感场景数据，可能较大，不提交代码库。

## 本次验证

13项 unittest 通过，涵盖共享召回不绘图、实际PNG进入模型请求、未知ID拒绝、截断拒绝、轮数预算、原生工具批次和图片回传、goal=null，以及原有数据/图像/检索合同。模型调用在单元测试里是可控替身，不代表真实模型会做对。

真实 CLIP + 场景证据预备结果在 `runs/agent_preflight_v2_20261111/`。六条用例均完成工具调用：

| 用例 | 检查目标 | 证据判定状态 |
|---|---|---|
| NG01 会议室柜子 | 类型与区域、源标签和图片冲突 | 源锚点待审核，不指定正确ID |
| NG02 白色柜子 | 属性检索与视觉核验 | 待人工/实物确认 |
| NG03 像打印机的设备 | 模糊外观召回 | 外观不证明打印功能 |
| NG11 玻璃隔断区 | 重名区域澄清 | list_scene 提供 R03/R04，不能自动选一个 |
| NG16 急救箱 | 急救箱与消防设施标签冲突 | 不以标签作为答案 |
| NG17 现在空着的椅子 | 静态证据不能证明实时状态 | 需新观测或说明不足 |

预备脚本使用已登记 source_anchors 生成待审图片，Agent 运行只接收 utterance/context，**不接收这些锚点、检索计划或答案暗示**。未完成六例的独立人工审核，所以 semantic_accuracy=null。

首轮预备脚本把 NG11 空查询传入 search_candidates，被工具正确拒绝后脚本报错；原目录 `runs/agent_preflight_20261111/` 保留失败说明。修正为区域歧义使用 list_scene 后，在 v2 新目录重跑成功。

最初未发现可用本地接口。用户随后指定参考 Claude Code 的端点，已发现企业网关并验证图片输入；实际模型运行及失败记录见 SONNET_PILOT_20261111.md。之前的端口检查仅代表当时检查的地址，不代表没有其他可用服务。

引用图片只证明该图片确实提供给模型，不证明模型解读正确。当前宿主检查结构、引用及交付边界，语义正确性仍需独立审核。

## 物体图片上下文调整（2026-11-11）

按用户反馈，系统prompt改为简短的找目标任务说明：检索、查看物体图片、结合用户条件选择，只有影响选择的具体歧义才追问。移除GS/非实时/普遍不可信等重复提醒，也去掉图片上原有的gs_render与source identity unverified标题。图片统一显示“物体图片”、对象ID和视角ID。

原始数据与工具合同保留来源。Agent发送前通过model_context删除来源、审计状态和泛化limitations字段；实际errors继续发送。原始工具返回保存为*-tool.json，发送给模型的精简文本另存*-model-context.json，逐轮request仍包含实际图片字节。图片内容未改，只调整证据板标题。ID/证据引用与导航绑定校验仍由宿主执行。

本次未改变召回数量、排序或视图选择；候选精简需另按检索需求调整。

### 单例真实复测

`runs/object_images_prompt_20261111/`：移除来源提示后，模型仍自行补充“静态渲染图/缺少实时观测”。该结果保留。

随后在prompt中正面明确任务范围：“本任务以地图图片呈现的场景为查询范围，用户描述的占用等状态条件按图片中的可见状态匹配。”

`runs/object_images_prompt_v2_20261111/`：同一模型、同一NG17请求、同一召回参数复测，模型返回ambiguous，列出S023/S034/S035，说明图片中都没人坐，只追问选哪把；不再要求实时画面。两轮模型实际收到的工具文本均未包含GS/来源不可信提示。这是单例行为验证，不代表全部请求都已验证。13项合同测试通过，包括发送内容去除来源字段但原始trace保留来源的检查。

可读完整记录通过既有服务访问：`http://127.0.0.1:18765/updates/object_images_20261111/NG17.html`（外网电脑需先建立既有SSH隧道）。旧六例页面与归档保留。

### 同配置追加两例（2026-11-11）

用户要求追加两例，输出到 `runs/object_images_two_cases_20261111/`。NG02/NG03的系统prompt逐字核对与上一轮椅子复测一致，模型仍为claude-sonnet-5-5，沿用同一场景、CLIP配置和8轮/120秒单请求预算，中途未改配置、未重试。

- NG02“去那个白色柜子那里”：查看8个对象、16张物体图片；第5轮请求等待响应时发生TimeoutError（The read operation timed out）。没有最终指代结果，完整请求与之前的工具/图片记录保留。不能仅凭本次超时确定是图像负载还是服务端其他因素导致。
- NG03“去那个像打印机的设备那里”：4轮模型交互、8张物体图片，返回resolved，选S135；理由直接比较外形与其他设备，没有附加GS/非实时解释。此为模型选择，未另作人工真值验收。

图文记录：`http://127.0.0.1:18765/updates/two_cases_20261111/index.html`。24次媒体引用均与记录的模型请求字节一致；两页与全部图片经Chrome/HTTP验证通过。页面明确显示NG02未收到响应的第5轮；原始日志与ZIP同时保留。导出器增加未返回轮次的请求链接，并正确使用每例protocol.json链接。

### 按需上下文首轮实现

候选分页、单视图获取和selection字段见TOOL_CONTRACT.md。14项测试通过，覆盖分页不丢失/重复候选、原始结果不变、第二视图可获取，以及既有实际图片输入测试。离线回放记录位于runs/compact_context_replay_20261111/report.json：NG02首轮候选27→5，NG03首轮19→5。回放仅评估投影，不代表真实Agent行为或耗时；历史超过3个ID的inspect调用在新Agent边界须拆分，回放未模拟这一交互。尚未在线重跑新链路。

### 精简链路在线对照（2026-11-11）

新运行：runs/compact_online_20261111/；基线：runs/object_images_two_cases_20261111/。同场景、模型、指令、prompt，工具定义/上下文和跨查询展示排序按上述优化改变；非重复随机对照实验。

NG02：首轮候选27→5，物体图16→5（新版另1张空间图），模型请求5→6，工具调用4→6；旧版请求超时，无总耗时记录，新版97.32秒完成ambiguous，候选indoor-041，4个排除ID单列。
NG03：首轮候选19→5，物体图8→4（新版另1张空间图），模型请求4→5，工具调用4→6；旧版约24.50秒（文件时间估算），新版25.98秒（运行计时）。两版都选S135。图片和首屏候选减少，调用轮次反而增加，不能宣称整体提速。

指标原文：runs/compact_online_20261111/comparison.json。页面：/updates/compact_compare_v2_20261111/comparison.html（既有18765服务）。11次媒体引用与实际请求字节核对，Chrome页面/图片HTTP200。首次导出因精简媒体无uri失败，已修正为按media_id从原始工具数据获取路径；失败目录compact_compare_20261111保留，新目录成功导出。未修改本轮运行结果。

### 检索拼图试验

默认检索直接返回当页最多5个候选的单张拼图，足以判断时可在下一轮 finish；需要细节再 inspect_objects。实现与 MCP 共用 preview.call_for_model，映射及缺图规则见 TOOL_CONTRACT.md。run_agent(..., search_preview=False) 与 chat(..., search_preview=False) 可用于旧流程对照。

本次前置优化还加入单 MapTools 实例最多32个请求的快照内结果缓存（返回深拷贝，PNG字节不改）以及模型/工具分段计时。这些改动均不改变召回算法或编码器。

首轮真实Sonnet对照已完成，未证明整轮提速：旧流程两例均超时；拼图流程“打印机”4轮约68秒返回证据不足，“白色柜子”5轮约361秒返回澄清。详见SEARCH_PREVIEW_TRIAL_20261111.md，不将工具通路成功当作模型性能验收。

固定候选/同像素的图片组织对照：见FIXED_PACKING_AB_20261111.md及scripts/compare_image_packing.py。该实验只有一次冻结候选判断，不能把其约9秒耗时称为完整Agent任务耗时。

完整自由运行Agent对照已完成，见FULL_AGENT_PACKING_AB_20261111.md。打印机单图/拼图33.19/18.57秒，均3轮选S135；白色柜子49.07/59.12秒，分别4/5轮且澄清问题不同。本轮没有证实拼图稳定提速。Python宿主可用image_packing='single'做对照，默认仍montage；两组统一称“物体图片”。

[工具与模型分项耗时](TOOL_MODEL_LATENCY_20261111.md)：最新4任务模型等待占94.6%；15次Sonnet请求均值10.08秒。工具补充实测单独标注本地/缓存条件，历史120秒超时保留，不与最新成功均值混算。

[降低分辨率对照](RESOLUTION_AB_20261111.md)：固定候选的1800×800→1200×533对照中，平均请求耗时约降39%–42%，token约降43%；完整Agent仍有工具参数失败/判断分叉，不能宣称稳定端到端提速。历史前缀逐轮检查未改写，缓存字段为0不能认定已命中。

[单图宽高减半对照](HALF_SINGLE_AB_20261111.md)：600×400→300×200，保留5候选与完整历史，输入token约降56%；完整打印机任务从选中S135变为证据不足（S135未进入半尺寸组实际图片），不能当成同等效果提速。新参数single_max_width=300为显式试验选项。

[英文查询/及时决策策略](ENGLISH_QUERY_POLICY_20261111.md)：模型queries改为必须英文，首轮优先具体短语，明确匹配/具体歧义及时结束；改写查询须重置page=1。真实调用已全部英文，但描述仍未保证分页合规或稳定提速；旧对照和v2补充试跑分别冻结。8880试验端已刷新描述，8879主实例保持原样。

### 离线描述试验（2026-11-12）

显式配置sidecar后搜索返回英文外观描述；关键细节不足时仍可inspect_objects。脚本run_grounding_agent.py支持--descriptions PATH，宿主和chat工具说明同步启用。证据ID可为实际返回的description_id，不能引用其他对象的描述来确认目标。未修改既有MCP服务或默认召回。生成协议与并发脚本见[OFFLINE_DESCRIPTIONS.md](OFFLINE_DESCRIPTIONS.md)，结构测试不代替语义或耗时验收。

后续按用户要求导出含描述的新统一场景：SceneStore自动读取description_generation与对象description_metadata，MapTools和run_grounding_agent.py同步识别，无需再指定sidecar。主要外观文本存入description，原始OCR等描述保留在description_metadata.original_description并继续参与检索；旧包行为不变。新snapshot_id需要匹配的新任务集，既有任务集不会跨快照自动复用。实际新包路径和CLI见OFFLINE_DESCRIPTIONS.md。

真实MCP完整任务见[MCP_AGENT_E2E_20261112.md](MCP_AGENT_E2E_20261112.md)。run_agent可显式注入tool_executor和tool_definitions以复用同一模型循环；chat可传tools及request_log记录真实请求正文。默认本地调用行为保留。描述优先模式同步调整系统提示中的“先看图片”为“先读描述，关键细节不足再看图”。MCP运行器的*-tool.json是MCP SDK原始结果，服务端原始MapTools审计另存，不能混称。
