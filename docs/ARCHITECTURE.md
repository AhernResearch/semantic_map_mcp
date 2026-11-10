# 设计与关键抽象

版本1.0.0，2026-11-11。本地实现见下文模块对应；不代表已部署远程服务。

## 定稿决策

1. 四个工具：list_scene、search_candidates、view_map、inspect_objects。search_candidates 与 view_map 共用召回实现，前者不画图。取消0.2版独立view_observation；查看图片合并进批量对象详情。
2. view_map的queries接受类型名称与自然语言描述。标签匹配和图文向量相似度检索在后台完成，不暴露map_weights、阈值、编码器、裁剪比例等参数。
3. 地图返回白底topdown空间简图：只标查询候选中心和稳定ID，不画点云/GS纹理。模型观察空间关系，工具不输出near/below等关系判定。
4. 统一模型是对象记录、区域、观测、资产、特征空间和来源。暂不要求所有源图都生成关系图。
5. 一个工具实例绑定一个不可变SceneSnapshot。宿主可建立多个实例，但不让对象ID在场景间混用。
6. 标准输入为scene.json加资产目录，源地图转换通过SourceAdapter实现。运行时不同时维护三个来源的独立候选票数。
7. 当前最小范围为已有对象记录的精确/模糊召回。未登记目标的整图检测、视图向量检索和临时对象创建不进入1.0；inspect_objects返回的环境图可以让模型发现线索，但无ID线索只能引用图片，不能伪造定位。

## 关键领域对象

| 抽象 | 身份与职责 | 不代表什么 |
|---|---|---|
| SceneSnapshot | scene_id、snapshot_id及包哈希，固定版本数据 | 现场实时状态 |
| SemanticType | type_id、名称、别名，供发现可查类型 | 类别已核验或完整词汇集合 |
| MapObject | 稳定object_id、源记录、类型、几何、属性、特征引用 | 必然独立的一件实物 |
| RoomRegion | room_id、kind、名字、边界与归属 | 自动推断的通行性 |
| Observation | 完整图片、相机参数、来源；对象关联另存 | 一定是实拍或独立观测 |
| FeatureSpace | 编码器/权重/预处理身份，维数和数组绑定 | 仅维数相同就可以混用 |
| RetrievalHit | 查询、对象ID、排名、分通道分数和命中依据 | 目标确认概率 |
| MapView | 地图图片、页号、投影、图例、可见ID及检索组 | 导航代价地图 |
| EvidenceMedia | 实际图像内容及media_id、来源、关联对象 | 返回路径就等于模型看过 |
| ToolResult | 统一状态、数据、媒体、限制和错误 | 自动审核的语义真值 |

实体去重不作为首次导入的前提：对象记录保留source_id，identity_status为unresolved或reviewed；可选entity_id仅用于明确审核后的绑定。源inspectRefs保留为关系声明，不自动合并。

## 模块和依赖方向

```text
ConceptGraphs / HMSG / 人工标注 / 相机资产
                   ↓ SourceAdapter
           标准场景包（scene.json + assets）
                   ↓ PackageValidator
              SceneRepository
             ↙       ↓        ↘
   ObjectRetriever  Renderer  ObservationSelector + MediaLoader
      ↑ TextEncoder            ↓
              MapQueryTools
                   ↓
       JSON + 实际图片 → 多模态Agent
```

当前具体实现：SceneStore在store.py中同时承担包校验与只读访问；import_mfloor.py是源转换函数；retrieval.py包含Retriever与OpenClipEncoder；render.py集中绘图和证据板；service.py提供MapTools。避免为每个概念建立没有实际用途的类。

ports.py只保留ToolResult、MediaPayload与可替换TextEncoder协议。下列逻辑职责保留，但不要求一职责一类。运行配置由宿主注入，不成为模型参数。

- SourceAdapter：一次性读取源包，写入新目录，记录原始哈希与坐标变换。
- PackageValidator：负责schema、路径边界、引用、数组形状、数值和模型身份检查；错误包不绑定服务。
- SceneRepository：按ID和区域访问只读统一数据；初期可内存索引，SQLite是派生缓存。
- ObjectRetriever：区域内类型/别名/属性和视觉检索，输出RetrievalHit。
- TextEncoder：提供与已存图像向量匹配的文本特征。缺失时明确降级，不下载或替换权重。
- TopDownMapRenderer：按统一坐标投影候选中心，绘制源区域范围和就近ID，必要时分页，不绘制外观或计算语义关系。
- ObservationSelector：按有效性和视角差异选取代表图；缺少质量指标时不能编分数。
- MediaLoader：从已登记资产读图片并产生实际字节与来源信息。
- MapQueryTools：校验参数、编排上面模块、生成统一输出。
- 传输适配：后续HTTP/MCP仅转发同一契约，不另加业务决策。

## 服务内部配置

配置包括文本模型本地路径/指纹、label别名字典版本、视觉top_k、排名融合常数、地图每页ID上限、分辨率、每对象图片数、媒体总预算和缓存根。有效配置以policy_id与摘要进入工具结果，不让模型传递配置对象。

初始默认：每查询视觉top_k=10，最多4个queries，inspect一次最多8个ID，每对象最多2个有效视角，每视角全图+裁剪拼成一张证据板。上述数值是显式产品配置，不是经实验验证最佳值。

若图片预算无法容纳全部对象，inspect返回partial及omitted_ids供下一次调用；不悄悄丢图。地图自动分块仍放不下时返回unshown_ids及建议按房间缩小范围，不强制引入分页参数。

## 与历史方案的差异

旧方案是Router计划、各图提案、融合评分、LLM/VLM复核；本库只提供可被Agent自主调用的观察工具。可以复用CG向量和几何、HMSG区域与观测关联，不沿用强制单选、源地图投票或导航ID契约。

统一坐标继续采用Y-up米制，与0.2设计一致。输入布局由此前“SQLite主存储”收敛为“JSON交换包为权威、SQLite为缓存”；旧视图ID/单对象工具不再作为公开接口。既有0.2没有部署服务，因此本次是设计升级，不存在生产API迁移。


## 当前空间图更新（2026-11-11）

view_map 已改为候选周边取景：候选中心范围外扩至少2米、或跨度的18%，保持X/Z等比例投影及所有分页一致的视窗。背景使用本地场景点云，按当前视窗内Y高度15%—85%分位保留后投影、密度着色、0.7像素平滑；并叠加淡色对象轴对齐包围盒。这里是几何背景，不是通行性或墙体识别结果。缺少背景资产时仍可绘制候选并在图中明确说明。标签为ID＋短名称，图像/像素矩阵合同不变。无候选时沿用区域或场景范围。

此前“纯白底、仅中心点、虚线区域”的描述是旧版本。绘图参考semantic_map_query的分层与突出标注，未复制导航站位/路径逻辑。原始点云及对象数据不改。
