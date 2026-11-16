# 英文查询与及时决策策略（2026-11-11）

按用户要求修改模型工具描述：所有queries必须英文，不得中文或中英混合；首轮优先一个具体英文短语。同时补充明确匹配就结束、具体歧义及时澄清，以及改写查询重置页码的说明。

## 实现与兼容范围

共享规则在agent.ENGLISH_QUERY_POLICY，由model_tools加入search_candidates/view_map描述和queries元素说明；harness MCP从同一model_tools读取工具描述。本地Agent系统提示同步去除“可用中文类别”旧表述。用户输入及最终回答仍可中文。

本次只改变模型调用规范。原始MapTools检索仍接受中文和登记别名，不改变召回算法、RRF排序、模型、图片尺寸或源标签，不自动翻译/替换模型参数。没有加入将ASCII等同英文的服务端语言判定；描述中的“必须”不是声称程序能保证所有客户端模型都遵守。审计逐项核验实际生成的查询。

## 真实完整Agent A/B（v1）

旧agent.py在修改前复制为冻结基线；两组同一场景快照、CLIP与Sonnet、默认1800×800拼图、5候选、1600输出token、最多8轮、单请求120秒socket超时。打印机先旧后新，白色柜子先新后旧；各条件各任务1次，无自动重试或替代模型。

| 任务 | 旧策略 | 英文/及时决策v1 |
|---|---|---|
| 打印机 | 9.75秒，2轮，选择S135 | 57.44秒，6轮，选择S135 |
| 白色柜子 | 63.92秒，7轮，澄清indoor-041/S115 | 20.19秒，3轮，澄清indoor-041/S115/S016 |

v1全部10条查询短语为英文；旧策略有中文或中英混合。英文规范被本轮模型遵守，但没有稳定提速。

打印机v1首轮只查printer，S135在其第7位，首屏未见。下一次模型改为office printer copier machine却带page=2，跳过新查询首屏。这不是服务端召回失败，而是模型参数组合语义不当；API无状态分页仍合法，工具没有静默重置页码。后续模型又尝试equipment并翻页，最终选中S135。

## 补充v2分页说明与独立试跑

基于v1发现，工具描述与系统提示进一步强调：改写queries或room_ids必须page=1，只有二者不变才可沿next_page翻页。v1源码及结果完整冻结，v2另建目录，不重写原对照，也不将其称为失败请求的重试。

| 任务 | v2补充试跑 |
|---|---|
| 打印机 | 39.49秒，5轮，选择S135 |
| 白色柜子 | 24.61秒，3轮，澄清indoor-041/S115/S016 |

v2实际6条查询短语仍全部英文。打印机仍出现一次改写为office printer on desk同时带page=2，表明仅靠描述不能保证分页规则完全执行。没有服务端静默纠正或隐藏违规。

两个版本的全部6次任务均结束，选择/澄清是模型输出，不是独立语义真值确认；导航goal仍为null。v2与v1不是同一批次的重复随机实验，不从单次耗时差得出因果收益。不能因为查询变英文就宣称减少了模型轮次。

## MCP试验端交付

8880试验服务已加载v2工具描述，地址不变：`http://172.22.162.253:8880/mcp`，认证令牌不变，客户端需刷新工具列表。新单元 `map-query-mcp-english-709e7dff3078.service`，旧试验单元已停止；8879主入口保持原实例。

运行身份索引：`/home/agiuser/lx_workspace/runtime/map-mcp/preview-current.json`；新运行目录 `map-query-mcp-english-709e7dff3078/` 保存owner、launch、trace与acceptance.json。实际MCP客户端检查英文描述、分页说明、office printer检索首候选S135及单张图片传输均通过。

该服务仅提供工具描述，不能替外部Agent注入系统提示；若客户端旧提示仍写“可用中文类别”，应同步为“工具queries一律英文，用户回答仍可中文”。本地Agent的完整决策提示已同步。

## 验证与证据

21项地图unittest通过，包括英文描述/参数说明和原始中文API仍可用的兼容检查。harness全量250通过，保留已在原始基线复现的2项reset_context描述失败；Ruff/mypy和前端构建通过。

- 模型英文调用示例：examples/agent_english_query.example.json。
- A/B入口：scripts/compare_query_policy.py，显式提供--baseline-agent冻结源码及既有scene/encoder/endpoint/model配置。
- v1证据：runs/english-policy-ab-20261111/（含旧agent.py、逐项工具参数、请求图片字节、响应、结果和完整source快照）。
- v2证据：runs/english-policy-v2-pilot-20261111/（含run.py、source快照、协议、实际参数与结果）。

上述运行目录按敏感场景数据保存并被Git忽略，未复制认证凭据。当前修改实现了模型英文查询规范；后续若要保证更少轮次，仍需针对首屏相关性和分页状态单独设计并验证，不能仅依靠更强措辞。
