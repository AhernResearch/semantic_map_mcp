# deep_search 隔离验收（2026-11-12）

已实现MCP可选第五工具deep_search，并通过真实SDK→MCP→Haiku4.5子Agent→现有地图工具完成隔离验证。正式8879/8880实例未重启，PID和状态与此前记录一致；所有临时服务已停止。

接口与运行入口见[DEEP_SEARCH.md](DEEP_SEARCH.md)。instruction保留原始中文，context是可选的必要前文摘要，默认空串。只提供前文不赋予对象证据；服务端预置完整5区域卡片，区域结果使用card_id，物体结果须检索/查看且引用实际证据。配置为claude-haiku-4-5-20251001、6轮、45秒、并发上限2、无重试或模型替换。

## 最终真实模型结果

最终14次调用均返回结构/引用合规结果，平均4.08秒、中位数2.92秒，共57.07秒、21次内部模型请求、7次底层数据工具调用。模型等待56.15秒，工具0.449秒。结果包含11次resolved、1次ambiguous、1次insufficient_evidence和1次needs_context。流程合规不是14/14语义正确。

| 用例 | 调用次数 | 平均工具耗时 | 内部模型请求数（各次） | 结果 |
|---|---:|---:|---|---|
| F01 找吃饭的地方 | 2 | 4.94秒 | [2, 2] | ambiguous；insufficient_evidence |
| F02 讨论并展示内容 | 2 | 2.84秒 | [1, 1] | resolved R01；resolved R01 |
| F03 边写边画讲方案 | 2 | 2.47秒 | [1, 1] | resolved R01；resolved R01 |
| F04 打印资料 | 2 | 7.05秒 | [2, 3] | resolved S135；resolved S135 |
| F05 放电脑坐下工作 | 2 | 6.23秒 | [2, 2] | resolved S016；resolved S016 |
| F06 玻璃门和紫色门禁 | 2 | 2.26秒 | [1, 1] | resolved R03；resolved R03 |
| F07 带前文：就会议室那个吧 | 1 | 2.95秒 | [1] | resolved R01 |
| F08 缺前文：就那个吧 | 1 | 2.55秒 | [1] | needs_context |

F07直接透传指令“就会议室那个吧”，context说明此前寻找讨论/展示地点并提供过会议室与走廊两个选项；返回R01，耗时2.95秒。F08没有context，返回needs_context及明确追问，耗时2.55秒。两例实际模型请求中的utterance/context逐项核对，未被宿主翻译或改写。

用途型任务可直接交付区域，F02/F03/F06及F07均一次模型请求，避免为取得物体锚点额外检索。F04/F05选择物体，仍经检索或看图。本次是MCP工具调用端到端，包含真实内部推理和数据工具，未包含外层模型发起与最终转述；与此前“必须选对象”的自由Agent结果口径不同，不能直接据秒数声称等准确率提速。

打印用例Haiku继续选择S135。既有图像/描述只能确认白灰色箱形设备，打印功能仍缺独立核对；不把这一resolved当成设备功能已验收。其他用例没有现场语义真值，semantic_accuracy=null。所有navigation.goal=null，没有机器人动作。

## 初始失败与修正

保留所有试验目录：
- `/home/agiuser/lx_workspace/runtime/map-deep-search/20261112T105854Z`：9/14次结构合规。
- `/home/agiuser/lx_workspace/runtime/map-deep-search/20261112T110121Z`：12/14次结构合规。

初版要求区域/物体数组互斥，真实模型同时填写物体和所属区域而失败；最终契约允许这一合理表达，并检查区域必须属于已选物体。另一次澄清遗漏必填evidence_ids，补充模型参数说明要求无引用时显式填写[]，未自动补字段。第二版在办公任务列出桌子和椅子两个resolved目标；明确用途地点优先区域，配套设施作为证据而非多个已选目标。宿主仍拒绝多个已选物体，不猜测选择。每轮修正独立冻结、零自动重试，旧失败与请求均保留。

## 校验

最终35项地图unittest通过；harness语义地图6项通过。结构测试含：context不能替代证据、非空卡片与区域绑定、物体/区域归属、未见ID与错误引用拒绝、不可递归、严格finish、并发隔离、超时、模型请求取消，以及当前只读工具完成前保留并发槽。

harness全套pytest为252通过、2失败，均是此前已记录并在基线复现的reset_context描述问题；未借本次地图功能修改导航执行代码。Ruff、mypy、navgpt未定义变量检查、前端build和JSON示例校验通过。前端未改交互流程。

真实MCP只暴露原四工具及deep_search，子Agent工具列表仅四工具+finish；逐请求检查原始指令/context透传、完整卡片前缀、固定模型、实际PNG字节、英文查询与翻页规则、trace隔离、快照及源文件哈希，全部通过。模型与原始工具JSON分别保存，未把原始审计数据误称为全部发送给模型。

## 记录

最终目录：`/home/agiuser/lx_workspace/runtime/map-deep-search/20261112T110355Z/`。配置与卡片为config.json/cards.json，experiment.json冻结派生任务/预算/顺序，frozen/冻结源码和schema；results.json保存SDK返回，validation.json保存审计与计时，checks.json记录检查范围，owner.json/cleanup.json保存临时服务归属与停止结果。

服务端server-trace/deep-search/保存初始原始场景结果、模型前缀及卡片来源，各trace_id目录内为输入、agent实际wire请求/响应、原始MapTools结果、模型投影、结果与worker停止记录。MCP外层只返回可展示答案及证据摘要，真实图片保留在子Agent请求和审计中；图像引用已逐字节核对。凭据通过既有配置在进程内注入，未写入配置、参数或日志。
