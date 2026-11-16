# 完整Agent图片组织对照（2026-11-11）

本轮完整任务耗时：打印机单图33.19秒、拼图18.57秒；白色柜子单图49.07秒、拼图59.12秒。两任务方向不同，各格式各任务仅一次，且模型查询路线发生分叉，不能认定拼图稳定提速。

## 实验条件与计时范围

复用同一run_agent循环，任务由用户指令开始，模型自行检索、翻页、看大图/第二视角、澄清或finish。两组同一场景快照、ViT-H-14编码器、claude-sonnet-5-5、提示词与工具定义；temperature=0、max_tokens=1600、最多8轮、单请求120秒socket超时，无自动重试或替代模型。

只主动改变候选图片组织：single把同一生成逻辑的600×400候选格分别发送，montage保持3列拼图；检索每页仍最多5候选，inspect_objects仍最多3对象/每对象1视角。两组图片像素一致性与媒体ID/坐标映射由unittest覆盖。媒体引用及坐标因图片组织形式而对应变化，这是必要的格式差异。

初始消息与工具定义已经逐字比对相同。后续模型可选择不同查询，因此本轮是自由运行Agent的整体表现观察，不是固定候选的图片组织因果试验；固定候选试验见FIXED_PACKING_AB_20261111.md。

按预定顺序执行打印机single→montage、白色柜子montage→single，总共4次任务。各任务使用独立的空工具结果缓存，场景与编码器只加载一次。表中从接收指令到最终结果，包括模型、工具与trace写入；不含一次性的场景/编码器加载。启动耗时单独保存在experiment.json。

## 完整结果

| 任务 | 格式 | 总耗时 | 模型轮次 | 模型等待合计 | 工具合计 | 结果 |
|---|---|---:|---:|---:|---:|---|
| 打印机 | 单图 | 33.19秒 | 3 | 31.22秒 | 1.95秒 | resolved，S135 |
| 打印机 | 拼图 | 18.57秒 | 3 | 17.06秒 | 1.49秒 | resolved，S135 |
| 白色柜子 | 单图 | 49.07秒 | 4 | 46.12秒 | 2.91秒 | ambiguous，候选indoor-041/S037 |
| 白色柜子 | 拼图 | 59.12秒 | 5 | 56.84秒 | 2.23秒 | ambiguous，未保留具体候选，追问类型/区域 |

4次任务均在预算内结束，没有超时或截断。resolved仅是模型判断，不是人工或实物真值确认，导航goal仍为null。

## 行为差异

### 打印机

两组首轮都查询 `打印机`、`printer-like device`，候选顺序完全相同：S087/S112/S005/S157/S089。

单图第二轮查询 `复印机`、`打印设备 办公设备`、`copier machine`，S135位于当页第5。

拼图第二轮查询 `复印机`、`多功能一体机`、`copier machine on floor`、`办公设备`，S135位于当页第2。

两组随后都finish并选择S135。拼图这次更快，但输入候选已经不同，而且尚未看到图片的首轮请求也出现1.90秒与3.55秒的差异，因此不能将14.62秒差值完全归因于拼图。

### 白色柜子

拼图先查 `白色柜子`，再改为 `柜子/white cabinet/储物柜`，然后翻到第2页，最后list_scene并追问具体类型和区域。共5轮模型、4次工具调用。

单图第一轮就查 `white cabinet/白色柜子/white cupboard/white shelf`，随后两次检索在改查询的同时指定page=2/3，最后查看S037第二视角。共4轮模型、4次工具调用（后两次工具可同一模型轮调用）。最终提出以indoor-041为主的具体澄清问题。

两组都要求澄清，但不是等价的最终答案，不能仅比较秒数而忽略候选与问题质量。

## 判断

- 已取得完整Agent的端到端数据，不能用此前冻结候选的约9秒代替这里的任务耗时。
- 本轮拼图没有稳定减少模型轮次：打印机均3轮，白色柜子拼图5轮/单图4轮。
- 检索查询、首屏相关性、后续翻页和网关请求等待仍主导任务耗时；保持5候选并不自动保证任务两轮结束。
- 本轮不支持将拼图提升为确定的性能优化。后续若要优化，应先统一更可靠的首轮查询表达和澄清时机，再在预先固定的任务集上做重复对照，分别比较结果质量、轮次和耗时。

## 入口与证据

入口：scripts/compare_agent_packing.py。认证从MAP_AGENT_API_KEY注入。

```bash
PYTHONPATH=src HF_HUB_OFFLINE=1 /path/to/python scripts/compare_agent_packing.py \
  --scene /path/to/scene --encoder-config /path/to/encoder.json \
  --output /path/to/new-private-output \
  --endpoint https://lingzhi.agibot.com/v1 --model claude-sonnet-5-5
```

运行数据：runs/full-agent-packing-ab-20261111/，包含experiment.json、sources.json、results.json、comparison-checks.json，各任务每轮实际请求（含图片字节）、响应、参数、原始工具结果、模型上下文、媒体审计、timings.json与最终result.json。敏感数据保存在Git忽略的runs目录，不提交源码。

18项地图unittest通过（新增单图/拼图像素一致性、局部坐标及真实媒体引用检查）；harness全量250通过，保留已在原始基线复现的2项reset_context说明失败；Ruff/mypy和前端构建通过。没有更改或重启在线8879/8880服务。
