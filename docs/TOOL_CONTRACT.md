# 工具输入输出与执行规则

MCP宿主新增可选高层工具`deep_search(instruction, context='', think_mode='fast')`，由服务端显式配置三档模型与区域卡片后启用。fast使用本地9B，middle使用Sonnet，strong使用GPT-6 Sol；模型由服务端profiles指定，不自动换档。原始MapTools四工具不混入在线生成调用。独立契约、区域/物体决策、context、预算与错误见[DEEP_SEARCH.md](DEEP_SEARCH.md)及schemas/deep_search.schema.json。

当前主线为图文混合向量召回、检索文本描述返回，见[MIXED_RECALL_MAINLINE_20261112.md](MIXED_RECALL_MAINLINE_20261112.md)。原始候选新增description；混合通道使用match_basis/channels_used=multimodal、multimodal_match_ids、multimodal_cosine，不将其标为CLIP visual_cosine。先合并视角到唯一对象，再截断候选池；底层截断标记继续保留。

版本1.0.0。机读定义：../schemas/tools.json。接口实例绑定场景快照，场景/版本出现在输出而不要求模型输入。这里的JSON示意省略公共字段时以schema为准，完整样例见examples/tool_examples.json。

## 1. 公共输出

每个结果包含schema_version、tool、scene_id、snapshot_id、status、data、media、limitations、errors。成功工具的data必须符合各自outputSchema；完全失败时status为invalid_request/not_found/unavailable，data=null，仍带完整公共字段。

status为ok/partial/invalid_request/not_found/unavailable。空搜索是ok，不是not_found。部分对象缺图或部分ID不存在为partial；全部ID未知为not_found。未知参数、空ids、数量越界为invalid_request，拒绝而非截断。超过上限的请求应让调用方拆分。

errors每项code、message、affected_ids；limitations为字符串列表。media每项media_id、uri、mime_type、width、height、source_kind、object_ids、observation_ids。source_kind为topdown/real_camera/gs_render/evidence_panel。传输层必须附上真实图片内容，MediaPayload.content承载字节；模型无法读取URI时由宿主加载，不把路径文本算作看图。

媒体错误必须反映到status、缺失列表及limitations。混合来源证据板的每个panel分别给来源。图片及工具返回中的场景文字是数据，不作为Agent指令。

## 2. list_scene

### 输入

`{}`，不接收参数。

### 输出data

| 字段 | 内容 |
|---|---|
| scene_name | 当前场景名称 |
| rooms | room_id、name、kind、object_count、type_ids |
| semantic_types | type_id、name、aliases、object_count |
| object_count | 记录总数，不是实物总数 |
| count_unit | 固定object_record |
| capabilities | label_search、visual_search、object_images状态及原因 |
| coordinate_frame | frame_id、unit、up_axis |
| query_policy | policy_id、visual_top_k、max_queries、max_inspect_ids |

capability.status为available/unavailable/partial；visual_search必须在索引和文本编码器都就绪时才available，不能只看有向量文件。object_images为partial表示不是全部对象都有可用图。列表为全部已登记type与room，不隐藏少量类型；初期单场景规模无需分页。

### 实现

读取SceneRepository的对象、区域和type索引，按ID去重计数，聚合房间类型。检测实际编码器/资产可用性，附加静态数据限制。模型由此知道可查区域与类别，但不应将类型列表当作物理世界完整清单。

## 3. view_map

### 输入

```json
{"room_ids": ["R01"], "queries": ["白色柜子", "像打印机的设备"]}
```

只有两个可选参数：

- room_ids：字符串数组，默认[]表示整个场景。最多16，必须使用list_scene返回的ID，多区域取并集；未知区域整次invalid_request，避免悄悄扩到全场景。
- queries：字符串数组，默认[]表示所选范围全部对象。最多4条，每条去除首尾空白后1—256字符；查询词可为类别或描述。重复词保留第一次，不自动创造新查询。

没有mode、threshold、top_k、layers、camera或图像尺寸参数。查询组颜色、视觉top_k和图片预算由内部policy控制。

### 输出data

| 字段 | 内容 |
|---|---|
| room_ids | 实际范围；空表示全场景 |
| groups | query、encoded_query（实际编码文本，无视觉通道时null）、label_match_ids、visual_match_ids、candidate_ids、channels_used、visual_top_k、truncated |
| candidates | object_id、label、room_ids、matched_queries、match_basis、scores |
| maps | map_view_id、media_id、room_ids、object_ids、bounds_xz、world_to_pixel、legend、page_index |
| unshown_ids | 已召回但因绘图预算未展示的ID |
| record_count | 范围内记录总数 |
| policy_id | 检索与显示策略版本 |

scores为每查询的label_rank（可null）、visual_cosine（可null）、fusion_score（可null），均不是确认概率。legend给出查询和颜色，并注明候选含义；标签匹配和视觉候选可用实虚线区分。maps中的object_ids必须与图上真正标注的ID一致。

### 召回

1. 按object.room_ids筛选范围，多区域按object_id去重。不把房间未核验归属说成真值；源归属缺失则归入显式unassigned区域，list_scene可发现。1.0不对每次区域查询悄悄切换几何归属算法。
2. label通道匹配名称、类型名、别名、描述和已有属性。中文支持精确/子串匹配，无需依赖英文分词；输出命中字段。检索内容来自未经审核字段时保留来源。
3. visual通道用同空间文本编码器编码查询，L2归一化后与有效对象图像向量点积，召回top_k。中文如需翻译，使用已登记词表/翻译器策略并记录原文与变体，不静默让LLM改源标签。
4. 所有label命中与visual top_k取并集，按object_id去重。label命中不被视觉top_k截掉。排序用等权RRF：每个有效通道贡献1/(60+rank)，rank从1开始；常数写入policy。不得跨不同特征空间直接比较余弦分数。
5. 多queries独立召回、并集绘图；同一对象可属于多组。后台不执行“门旁边”的关系推理，Agent最好分别查询“门”“桌子”，再观察布局。
6. visual不可用时保留label结果，status=partial并报feature_unavailable。queries为空只浏览，不需要文本编码器。两通道均无命中时返回ok+空候选地图，不能说现场不存在。

开放词汇召回只作用于已有对象记录。标签不叫打印机但已建对象的设备可以召回；完全没建对象的设备没有可返回ID。本版不偷偷创建新对象或假位置。

### 绘图

默认白底空间简图：只绘制当前查询召回对象的中心点及稳定ID，并保留源区域范围、方向和米制比例尺。右+X、上-Z。对象名称、外观、点云、包围框和GS纹理不画入图中，按ID通过inspect_objects查看细节。查询组以颜色与简短图例区分，同一候选可属于多个组。

源区域范围使用灰色虚线，并说明不是实际墙体。点和ID保持就近关联，必要时用短线避让；不再用右侧长引线列表。所有结果页固定为所选区域与候选中心的联合范围，跨页坐标不跳变。每页最多24条、最多8页，全部候选仍保留在结构化列表；超出以partial和unshown_ids提示缩小查询。不指定queries时，依原契约展示所选范围的所有记录ID。

world_to_pixel为3×3齐次矩阵，将[x,z,1]映射为[u,v,1]；图像像素原点左上，包含绘图区偏移。图像坐标使用实际返回尺寸。

截图缓存绑定快照、请求、policy和渲染版本；map_view_id仅用于证据引用，本版不增加图像像素裁剪参数。

## 4. inspect_objects

### 输入

```json
{"ids": ["S012", "S035"]}
```

ids必填，字符串数组，1—8个且不重复。ID可来自地图，也可用户直接给出；只需存在于绑定快照，不要求先看地图。

### 输出data

| 字段 | 内容 |
|---|---|
| objects | 每条对象详情，按输入顺序 |
| missing_ids | 不存在的ID |
| omitted_ids | 存在但因媒体/响应预算没有完整处理，允许下一次重试 |

每个对象详情：object_id、label、type_ids、room_ids、center_xyz、bbox、attributes、identity_status、entity_id、source_links、source_refs、evidence、limitations。

evidence每项包含observation_id、media_id、source_kind、camera_position_xyz（可null）、panels。panel包含kind=full/crop、panel_xyxy、original_crop_xyxy、original_size；由此从证据板像素可以追溯到原图。对象没有图时evidence=[]，status=partial并解释缺失。

### 实现

按ID读记录与关联观测；优先资产可读、target_support有效的视图，同等支持状态下按observation_id稳定排序（当前未实现额外视角多样性优化）。每对象默认最多2个视图，每个视图的全图与对象裁剪拼成一张证据板，明确标注对象ID、视图ID、GS/实拍来源。当前场景多数记录有2个选中视角，无法提供不存在的额外视角。

没有crop时只返回全图并说明，不借三维框投影编造精确遮挡边界。图片可等比缩小但不为“看清”而上采样制造细节；原图像素坐标和缩放映射必须可追溯。向模型只发几何摘要和图片，不发所有点云/向量。

这一个工具合并详情和看图功能，不再另设view_observation。一次处理多个ID便于比较；图太多时明确分批，不无声丢弃。身份冲突或重复源关联原样返回，不由工具判定谁是最终目标。

## 5. 使用示例与不确定性

“门旁边的白色柜子”：list_scene发现房间/柜子/门 → view_map查询["柜子","门"]或["白色柜子","门"] → Agent从topdown选候选 → inspect_objects批量看图 → 返回满足条件的ID，或多个候选及追问。

工具输出的是观察与检索证据，Agent负责判断。“桌子下面”不能仅用topdown重合证明，需看对象环境图；“左边”缺朝向需追问。找不到记录不证明不存在；图片也可能只覆盖GS的一部分。最终位置是地图对象中心，不是导航站位。

## search_candidates（候选召回，不绘图）

输入 `{"queries":["柜子","a white cabinet"],"room_ids":["R01"]}`。
queries 必填，1–4 个非空字符串，每条最多256字符；room_ids 可省略，最多16个有效区域ID。
输出 data 为 room_ids、groups、candidates、record_count、policy_id，字段定义与 view_map 完全相同；没有 maps/unshown_ids，media 为空。
标签与视觉召回、去重、RRF排名共享同一个实现；不受地图192对象显示预算限制。缺文本编码器明确 partial，未知房间 invalid_request。
区域名称歧义先用 list_scene 核对；search_candidates 不接受空 queries。view_map 保留无查询浏览行为。

Agent 的最终决策是宿主输出，不属于地图工具返回。见 AGENT_LOOP.md 及 schemas/grounding_decision.json。

### Agent展示层

本节工具schema定义的是原始返回。Agent使用model_context投影后再发给模型：不包含source_kind/source_refs/source_links/provenance/identity_status/limitations/uri；保留业务结果、错误与媒体ID。图片消息统一称“物体图片”。两份JSON分别记录，便于核对模型的实际输入。原始来源仍可审计，不伪装成其他采集方式。

### Agent按需获取（2026-11-11）

仅Agent工具定义增加search_candidates.page（默认1，每页5项）与inspect_objects.view（默认1，可选2），inspect每次最多3个ID。原本地MapTools接口和原始输出保持不变，Agent在边界校验扩展参数，分别记录原始结果与实际模型上下文。

多个查询的候选按ID去重后，按其最高单查询fusion_score排序、ID打破同分；这是新增的跨查询展示排序规则，底层标签/CLIP召回算法不改。只显示ID、标签、区域、匹配查询。next_page为空表示当前召回池翻完，不表示全场景穷尽；retrieval_truncated保留底层截断标记。改写查询可扩大检索。翻页重复调用现有检索，不新增缓存。

每对象默认一个已关联视图；next_view提示可继续获取第二张。原始工具仍生成完整证据，只有选定图片进入模型请求。输出selection由宿主生成：candidate_ids为仍符合条件的对象，excluded_ids为已排除对象，selected_id仅resolved时赋值。候选和排除ID必须互斥且已见过。


## 当前空间图更新（2026-11-11）

view_map 已改为候选周边取景：候选中心范围外扩至少2米、或跨度的18%，保持X/Z等比例投影及所有分页一致的视窗。背景使用本地场景点云，按当前视窗内Y高度15%—85%分位保留后投影、密度着色、0.7像素平滑；并叠加淡色对象轴对齐包围盒。这里是几何背景，不是通行性或墙体识别结果。缺少背景资产时仍可绘制候选并在图中明确说明。标签为ID＋短名称，图像/像素矩阵合同不变。无候选时沿用区域或场景范围。

此前“纯白底、仅中心点、虚线区域”的描述是旧版本。绘图参考semantic_map_query的分层与突出标注，未复制导航站位/路径逻辑。原始点云及对象数据不改。

### 检索拼图试验（2026-11-11）

Agent/MCP 展示层默认在 search_candidates 当前页（最多5个候选）附加一张物体图片拼图。
每对象取与 inspect_objects 相同排序的第一视角，沿用已有完整视角/物体裁剪证据板；每格600×400，最多3列，5项时1800×800。对象ID与名称显示在每格顶部。细节不足时仍可用 inspect_objects 查看单对象大图或 view=2。

原始 MapTools.search_candidates 与 schemas/tools.json 保持候选召回接口，不绘图；共享宿主适配器 sementic_map_mcp.preview.call_for_model 负责分页后绘图。返回 raw_result、model_context、model_media、raw_preview。本地 Agent 与 harness MCP 使用同一适配器。

模型 data.preview 包含 media_id、cells、missing_ids。每个有图 cell 给 object_id、observation_id、source_media_id、target_support、cell_xyxy、panels、next_view；panels 的 panel_xyxy 为拼图像素坐标，original_crop_xyxy/original_size 保留原图坐标。每格可包含 full/crop 两个面板。无图候选在对应格标注缺图，并出现在 missing_ids，模型状态为 partial，错误为 asset_missing。空页/空召回不生成拼图。不伪造缺失的第二视角。

raw_preview 另保留 source_kind 和完整媒体元数据（含uri），写入 *-preview.json；结构见 schemas/candidate_preview.schema.json。原始 *-tool.json 继续保存未经模型投影的检索结果，*-model-context.json 保存模型实际收到的 JSON，实际图片字节另进入模型请求。来源审计字段继续按 agent.model_context 规则投影。

拼图媒体的 object_ids 只包含实际有图的对象；宿主允许引用拼图 media_id 直接选定这些对象，无须额外 inspect。缺图对象不得因占有拼图格子而被视作已看图。resolved 仍须通过已见ID/图片证据校验，导航 goal 仍为空。

Python run_agent/chat 的 search_preview=False 用于与原先“检索后再看图”流程进行对照，默认True。测试该开关时两处必须一致。Agent新增 timings.json 记录模型与工具耗时；结构测试不能视为真实模型性能验收。

结构示例：examples/candidate_preview.example.json（示意图片路径，不是实际场景资产）。

### 完整Agent图片组织对照参数

宿主可显式设置 `run_agent(..., image_packing='single')`，默认仍为 `'montage'`；该参数不是模型工具参数，也不改变原始检索API。`preview.call_for_model(..., image_packing=...)` 共用同一生成逻辑。两种格式的提示词和工具说明统一称“物体图片”，避免提前暗示图片组织形式。

single模式把拼图中实际有图的每个600×400格子原像素分开返回；候选和视角排序不变。raw_preview.media_id为null，cells每项增加实际media_id，cell_xyxy/panels.panel_xyxy改为对应单图的局部坐标；media包含0–5个实际PNG的完整元数据。模型投影去掉无值的顶层media_id，通过cells与media列表引用实际图片。缺图候选继续missing_ids/asset_missing，不借占位格生成图像证据。montage模式及inspect_objects的图片预算不变。

结构检查与图片像素一致性、单图证据引用检查在unittest中覆盖。对照入口 `scripts/compare_agent_packing.py`：真实自由运行Agent，最多8轮，1600输出token，每请求120秒socket超时，固定两任务各两格式共4次，无自动重试。每次记录从接收指令到结果/失败的总耗时，排除一次性场景与编码器加载；查询和翻页可因模型选择分叉，不能把总耗时差直接解释为拼接的因果效果。

### 拼图分辨率对照参数

Python宿主可设置 `MapTools(..., montage_max_width=1200)`。默认None保持原尺寸；正整数最大宽度只对候选montage生效，禁止与single图片组织同时配置，不修改inspect_objects大图、地图或源图片。1800×800的5候选拼图按LANCZOS等比缩至1200×533；不会上采样本来较小的拼图。

缩放同步更新cell_xyxy和panels.panel_xyxy；original_crop_xyxy/original_size及原始source_media_id保持不变。实际媒体ID、PNG字节和width/height按缩放结果生成，raw_preview审计和模型上下文均与图片对应。该参数不暴露给模型，记录在每任务protocol.json。

Agent历史保持追加，图片在工具返回时按实例固定配置生成，后续轮次直接复用已发送内容，不重新缩放或删除历史图片。此性质仅保证客户端前缀未被主动改写，不代表网关一定命中缓存。

### 单图半尺寸试验

宿主可设置 `MapTools(..., single_max_width=300)`，并通过 `run_agent(..., image_packing='single')` 发送每候选独立图片。默认None仍为600×400；300宽对应300×200，即宽高各减半、像素数为25%。该参数只作用于search候选图，不改变inspect_objects大图、空间图或源图片。

single_max_width必须为正整数，且只可与single组织使用；montage_max_width只可与montage使用。两种组织复用Renderer.resize_preview更新实际PNG、媒体ID及cell/panel局部坐标，original_crop_xyxy/original_size不变。模型上下文不接收缩放策略参数；protocol.json记录该配置。历史保持追加，已发送图片字节不改写。

对照入口继续复用scripts/compare_image_packing.py和scripts/compare_agent_packing.py，使用 `--comparison single-resolution`；固定候选阶段指定 `--max-tokens 1600`，两组共用同一模型预算。原在线默认未调整。

### 英文查询与及时决策策略

Agent/MCP工具描述要求search_candidates和view_map的queries全部为英文，不提交中文或中英混合描述。用户指令和回答仍可中文；模型在生成工具参数时自行转换为具体英文短语，工具不会隐式翻译。首轮优先1条具体描述，不堆砌近义词。共享英文规则定义在agent.ENGLISH_QUERY_POLICY，同时写入模型工具描述和queries元素说明。

本地Agent系统提示和search工具描述同时强调：明确匹配就结束；多个具体匹配且缺少用户条件时直接提出具体澄清问题；无匹配时根据具体缺口选择翻页或改写，避免同义改写后反复看相同对象。

这是模型调用规范，原始MapTools API仍保留中文检索和登记别名能力，不加入ASCII/语言自动识别拒绝规则，不静默改写查询或源标签。真实运行应逐条核验queries，不能把描述中的“必须英文”宣称为服务端已自动验证语言。

模型调用示例见examples/agent_english_query.example.json。原始schemas/tools.json参数结构与召回算法保持不变；宿主实际模型定义由agent.model_tools投影生成。图片尺寸、分页和证据校验规则不变。

真实对照发现模型会在改写查询时带上旧page，故描述进一步明确：改写queries或room_ids必须从page=1开始；只有二者完全不变时才可沿next_page翻页。该说明不更改无状态分页API，也不会由服务端静默重写页码。英文v1对照与增加该规则的v2试跑分别保存，不覆盖原结果。

## 可选的离线描述投影

MapTools自动使用scene.json内嵌描述，旧包也可用descriptions_path显式启用包外缓存；两者不能叠加覆盖。preview.call_for_model为search_candidates当页候选附加visual_description={description_id,text}，不返回搜索图片；缺项列入missing_description_ids和description_unavailable错误。原始API的JSON结构和inspect_objects图片契约不变；新包的description与保留的原描述参与文本召回。Agent可引用同对象描述ID作为证据；原始工具、模型上下文和描述来源审计分别保存。详见[离线描述](OFFLINE_DESCRIPTIONS.md)。
