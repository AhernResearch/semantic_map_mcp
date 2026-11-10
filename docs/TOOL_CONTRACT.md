# 工具输入输出与执行规则

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
