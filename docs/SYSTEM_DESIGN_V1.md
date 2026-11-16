# 语义地图 Agent：输入交付规范、工具链路与初步验证

版本：第一版代码 `782ddcb` / 标签 `v1`。整理日期：2026-11-11。

本文是上游建图、工具开发和 Agent 接入共同使用的主说明，按六部分组织。规范基于当前代码，分别标明已实现行为、上游交付要求和后续接口建议；不将规划能力写成已实现能力。原始数据结构由 [scene.schema.json](../schemas/scene.schema.json) 约束，原始工具输出由 [tools.json](../schemas/tools.json) 约束。Agent 的分页、视图选择和上下文精简由 [agent.py](../src/sementic_map_mcp/agent.py) 实现。

目标：用户给出自然语言描述，Agent 利用地图检索和物体图片找到对象，必要时追问，返回稳定对象 ID 和判断依据。当前实现到“指代确定”为止，导航站位由后续独立绑定提供。

## 1. 上游输入：标准语义地图包

### 1.1 我们需要上游交付什么

上游可以用 ConceptGraphs、HMSG、人工框、配准点云或其他方法生成地图，但交付给查询层时，应转换成一份统一的**对象中心数据包**。查询层不要求运行三套原生地图，也不要求接受 pickle。

| 数据 | 完整交付要求 | 用途 |
|---|---|---|
| 场景快照与坐标定义 | 必须 | 固定此次查询的数据版本和几何基准 |
| 类型表、区域表、对象表 | 必须 | 场景发现、标签检索、区域筛选、稳定对象引用 |
| 对象中心与三维包围盒 | 每个对象必须 | 空间定位、topdown标注 |
| 背景点云 | 完整空间图交付必须 | 提供场景投影和空间参照 |
| 对象点云 | 建议提供 | 后续几何处理、原始实例核对；当前空间图也使用包围盒 |
| 物体图片及对象—图片关联 | 每个可视觉验证对象至少1个有效视图，建议2个互补视图 | 颜色、形状、摆放等视觉判断 |
| 对象图像特征矩阵 | 完整模糊检索交付必须 | 用户文字与对象外观的向量召回 |
| 编码器、权重、预处理身份 | 提供特征时必须 | 保证文字和图片特征处于同一空间 |
| 来源、转换、有效性信息 | 必须如实记录 | 审计、定位数据问题；不全部塞入模型上下文 |

这里的“完整交付”是本项目要求上游生成的目标。运行时 schema 允许部分能力缺失，例如没有图片、没有特征或没有背景；这类包可用于调试，但应明确标记缺失能力，不能按完整交付验收。

**不能只交标签和三维框，就认为已经支持“白色柜子”“像打印机的设备”。** 这些请求需要对象图片及与之匹配的视觉特征。只有“printer”标签的文本向量，也不能替代对象图像特征。

### 1.2 规范化存储与目录

新上游统一按下面的目录布局交付。`scene.json` 是唯一元数据入口，引用均使用资产 ID 和包内相对路径。

```text
<scene_id>/<snapshot_id>/
├── scene.json
├── assets/
│   ├── geometry/
│   │   ├── background.npz
│   │   └── objects/
│   │       ├── obj-000001.npz
│   │       └── obj-000002.npz
│   ├── images/
│   │   ├── image-000001.jpg
│   │   └── image-000002.jpg
│   └── features/
│       └── clip-h14/
│           └── objects.npy
└── provenance/
    ├── source_manifest.json
    ├── transforms.json
    └── validation.json
```

- `scene_id` 标识场景；`snapshot_id` 标识一次不可变交付。更新数据产生新快照，不能直接覆盖正在查询的快照。
- `scene.json.assets` 注册所有查询使用的二进制资产，记录相对路径和实际 SHA256。文件名不承担查询语义。
- 对象、区域、类型、观测都用稳定 ID；名称可重复、可更名。禁止将图片编号或临时排名当对象 ID。
- 特征矩阵按特征空间分目录；每个对象通过 `feature_refs.row` 引用矩阵行，不依赖对象 JSON 顺序。
- `provenance` 下三个文件是新上游交付记录的约定名称，不是当前加载器自动读取的入口；关键引用和转换说明仍应登记到 `scene.json` 的相应字段。
- 模型权重、API凭据、虚拟环境、查询缓存、生成图片和 Agent trace 不放入输入包。
- 运行结果放在独立 `runs/<run_id>/`，不反写输入包。模型权重与文本编码器配置属于部署侧。

当前加载器支持其他合法的包内路径；现有 M-floor 导入器仍使用扁平 `assets/` 命名，能正常加载。本次确定的是**新上游统一交付布局**，没有迁移或改写历史数据，也没有宣称加载器已经强制这些目录名。

### 1.3 scene.json 的结构与引用

```text
SceneSnapshot
 ├─ types[]              ← objects[].type_ids
 ├─ rooms[]              ← objects[].room_ids
 ├─ objects[]
 │   ├─ geometry_asset_id ───────────────→ assets(pointcloud)
 │   ├─ feature_refs(space_id, row) ────→ feature_spaces → assets(feature_matrix)
 │   └─ observation_links ─────────────→ observations → assets(image)
 └─ background_asset_id ───────────────→ assets(pointcloud)
```

顶层键均须存在；可缺能力的数据使用空数组或明确的 `null`，不要随意省略字段。

| 字段 | 约定 |
|---|---|
| `schema_version` | 当前固定 `1.0.0` |
| `scene_id`, `snapshot_id`, `name` | 场景ID、快照ID、显示名 |
| `coordinate_frame` | `frame_id`、`unit:"m"`、`up_axis:"y"` |
| `bounds_xyz` | 整个快照的 `min:[x,y,z]` / `max:[x,y,z]` |
| `types`, `rooms`, `objects` | 类型、区域、对象数组 |
| `observations`, `assets`, `feature_spaces` | 观测、资产、特征空间数组 |
| `background_asset_id` | 背景点云资产ID，或 `null` |
| `provenance` | 来源与转换说明字符串数组 |

完整的最小结构示例见 [scene.json](../examples/scene.json)。它是无图片、无特征的格式样例，不能直接当完整视觉交付包。

**SemanticType**：`type_id`、`name`、`aliases[]`。例如“柜子”可登记中文同义词和英文别名。当前编码器对精确中文类型名使用第一个别名编码，因此新上游应把适用的英文类型名放在首个 alias；这是一项当前实现约定。

**RoomRegion**：`room_id`、`name`、`kind`、`bounds_xz`、`polygon_xz`、`boundary_source`。`kind` 为 `room/corridor/area/unassigned`；矩形顺序是 `[xmin,zmin,xmax,zmax]`，有序闭合多边形为 `[[x,z],…]`，未知边界填 `null`。矩形范围不能冒充精确墙线。没有可靠归属的对象使用显式 `unassigned` 区域。

**MapObject**：

| 字段 | 类型/意义 |
|---|---|
| `object_id`, `source_id` | 统一稳定ID、原始系统ID；均为字符串 |
| `label`, `type_ids`, `aliases`, `description` | 名称、类型引用、别名、描述；description可null |
| `room_ids`, `region_assignment_source` | 区域归属及其来源，可属于多个区域 |
| `bbox` | 三维轴对齐框，`{min:[x,y,z],max:[x,y,z]}` |
| `center_xyz` | `(bbox.min+bbox.max)/2` |
| `geometry_asset_id` | 对象点云资产ID，或null |
| `feature_refs` | `[{space_id,row,valid}]` |
| `observation_links` | `[{observation_id,crop_xyxy,target_support}]` |
| `attributes` | `[{key,value,source,verification}]`，保留属性来源 |
| `identity_status`, `entity_id` | 记录身份核验情况；未确认的实体关联为null |
| `source_links` | 源记录之间的关联声明，不自动合并对象 |
| `provenance` | 来源说明 |

对象ID只在绑定的快照内解释。跨系统传递至少带上 `scene_id + snapshot_id + object_id`。不要通过原生框重叠就自动给两个对象同一个实体身份。

### 1.4 图片和特征的具体要求

**图片资产**使用 JPG/PNG。资产登记样式如下（示意片段，SHA必须替换为真实文件哈希）：

```json
{
  "asset_id": "image-000001",
  "path": "assets/images/image-000001.jpg",
  "sha256": "<64位实际SHA256>",
  "kind": "image",
  "metadata": {"width": 1280, "height": 720, "mime_type": "image/jpeg"}
}
```

**Observation** 必须包含 `observation_id,image_asset_id,width,height,source_kind,reconstruction_id,captured_at,rendered_at,K,camera_to_world,camera_convention,provenance`。原始来源如实保存在数据层；模型看到的统一名称为“物体图片”。

- 对象通过 `observation_links` 关联观测。裁剪为原图像素坐标 `[x0,y0,x1,y1)`，左上角原点，不能用已缩小显示图的坐标。
- `target_support` 是 `valid/invalid/unknown`。当前程序优先valid，然后按observation_id排序取前两个视图。新上游应优先准备清晰、目标占比合适的两张图；当前没有美学评分或视角多样性优化器。
- `K` 为3×3，`camera_to_world`为4×4列向量变换；相机约定统一为 `opencv_xright_ydown_zforward`。没有可靠标定时这些矩阵填null，而不是补单位矩阵。
- 当前裁剪是矩形裁剪，不要求实例mask。完整图和裁剪都应能追溯到同一观测。

**点云资产**使用 `.npz`，包含 `points:[N,3]` 浮点米制坐标，`colors:[N,3]` 可选uint8 RGB。metadata登记统一frame_id、point_count、points_dtype及颜色信息。点云必须已经变换到统一坐标，不在查询时反复配准。

**特征资产**使用二维float16或float32 `.npy`，shape为 `[N,D]`，禁止object dtype。对象引用行号必须对应真实对象图像特征。

```json
{
  "space_id": "clip-h14",
  "encoder": "ViT-H-14",
  "weights_fingerprint": "laion2b_s32b_b79k",
  "preprocess_fingerprint": "source-context25-squarepad",
  "dim": 1024,
  "normalized": true,
  "matrix_asset_id": "features-clip-h14"
}
```

上例是现有 M-floor 的空间身份，不是要求任意上游都声称使用这套预处理。新上游必须填写实际使用的编码器、权重和裁剪/聚合协议。多视图如何聚合成对象向量，应由上游实现并记录；查询层当前消费已有对象向量，不替上游生成视觉特征。

无有效图像特征的对象将 `valid=false`，仍可标签召回。不能用零向量冒充有效特征。文本编码器的配置单独由宿主提供，校验模型身份和本地权重SHA；不能因为维数相同就混用模型。

### 1.5 坐标、验收与交付步骤

统一Y-up、米制，水平面XZ，图上右为+X、上为−Z。当前没有地理北向，不能直接标东西南北。源Z-up的常用转换为 `(x,y,z)→(x,z,-y)`，但只对确属该源约定的数据使用，并同步处理点云、框、相机。

对象中心是几何位置，不是机器人接近站位。楼层应在1.0输入阶段拆成独立场景快照；当前代码没有自动分楼层能力。

建议上游依次完成：

1. 固定scene_id/snapshot_id及坐标变换，输出区域、类型、对象表。
2. 输出对象/背景点云和观测，建立ID关联，核对裁剪与对象对应。
3. 按真实视觉模型生成特征矩阵，建立行号映射。
4. 对最终二进制文件计算SHA256，写入scene.json；不在计算后继续改资产。
5. 检查JSON Schema，再用SceneStore执行跨字段与文件校验。
6. 做完整能力验收：list_scene视觉检索可用、代表性对象可返回实际图片、view_map能显示背景、至少一次真实描述检索可执行。
7. 将数据验证结果和模型身份记录进provenance目录；发布新快照。

当前加载器的实际调用：

```python
from sementic_map_mcp.store import SceneStore
store = SceneStore("/path/to/scene_id/snapshot_id")
```

它会检查包内路径、哈希、解码尺寸、数组shape/dtype/有限值、ID与引用、框中心、裁剪、矩阵与特征行等。独立检查脚本或上游验证报告还应验证实际物体对应关系；“文件能加载”与“标注正确”是两个验收项。

## 2. Agent链路与工具定义

### 2.1 运行链路

```text
用户指令 + 可选context
  → 宿主加载一个SceneSnapshot，配置匹配的文本编码器
  → LLM选择工具
      list_scene：发现区域/类别
      search_candidates：召回候选，按页查看
      inspect_objects：获取物体图片，按需补视角
      view_map：按需获取空间图
  → 工具原始输出记trace → 精简为模型上下文 → 图片字节送入模型
  → LLM继续取证或调用finish
  → 宿主校验ID/图片引用，生成selection
  → 返回目标或追问
```

这不是固定四步流水线。模型可以直接按ID查看，可以改写描述、多次检索，也可以不调用空间图。原生一轮允许最多4个工具调用，宿主按顺序执行；所以“每次inspect最多3个ID”不等于整个模型回合最多3张图。

### 2.2 给Agent的API

| 工具 | 输入 | 主要输出 | 负责的任务 |
|---|---|---|---|
| `list_scene` | `{}` | 场景名、区域/类型和对象数、能力、坐标定义 | 知道地图有哪些可查信息 |
| `search_candidates` | `queries`，可选`room_ids,page` | 少量候选、total_candidates、next_page、retrieval_truncated | 文字召回对象 |
| `inspect_objects` | `ids`，可选`view` | 对象名称、区域、中心、属性、图片引用、next_view及实际图片 | 验证外观、查看细节 |
| `view_map` | 可选`queries,room_ids` | 空间图、可见ID、投影矩阵、范围、候选与检索组 | 观察对象之间的空间布局 |
| `finish` | 决策对象 | 宿主最终结果 | 提交判断或追问，不是地图检索工具 |

Agent输入限额：queries最多4条，每条1—256字符；room_ids最多16个；search的page从1开始，每页5项；inspect的ids为1—3个唯一ID，view为1或2，默认1。

底层MapTools原始API没有page/view，inspect最多8个ID且每对象最多两张图。Agent在外层校验扩展参数，调用原始API后切分上下文。不能直接把Agent参数原样传给旧CLI：直接本地CLI应遵循schemas/tools.json。

### 2.3 精简输出示例

下面是search_candidates的**模型侧data示意**，不是省略字段后仍可提交的原始工具结果：

```json
{
  "candidates": [
    {"object_id":"obj-000001","label":"柜子","room_ids":["R01"],
     "matched_queries":["白色柜子","white cabinet"]}
  ],
  "total_candidates": 12,
  "next_page": 2,
  "retrieval_truncated": true
}
```

原始工具结果完整包络是 `schema_version,tool,scene_id,snapshot_id,status,data,media,errors,limitations`。模型侧保留业务数据和实际错误，去掉来源审计字段、泛化limitations与本地uri。`status=partial`仍可能有可用数据；无候选是正常空搜索结果，未知ID和输入错误另报。

inspect模型侧data包含objects和missing_ids；每对象包含 `object_id,label,room_ids,center_xyz,attributes,evidence,next_view`。evidence给出media_id、observation_id及视图元数据；真正图片以image_url/data URI发送，不能只给文件路径。

view_map当前没有应用search_candidates的5项分页：一次最多24对象一页，最多8页。它仍可能返回较多文本和图片，是第一版后续需要统一预算的地方。

### 2.4 模型调用与宿主分工

当前使用用户指定网关的chat-completions接口，实测模型 `claude-sonnet-5-5`。每次最多8轮，单请求120秒，max_tokens=1600，temperature=0；不自动修复JSON或更换模型。结束使用原生finish函数；兼容既有JSON动作格式。

模型负责识别目标、比较候选和提出问题。宿主负责schema、对象/证据引用、候选与排除集合互斥、媒体实际传递，以及导航交付边界。

正式结果中：

```json
{
  "selection": {
    "selected_id": "S135",
    "candidate_ids": ["S135"],
    "excluded_ids": ["S129","S131","S132"]
  },
  "navigation": {"status":"blocked","goal":null,"reason":"No verified navigation binding"},
  "handoff": "not_ready"
}
```

`selected_id`由宿主在resolved时设置；模型不能通过填某个坐标就生成导航目标。完整结果还包含scene_id、snapshot_id和grounding。

## 3. 各工具的检索与数据访问链路

### 3.1 list_scene：从元数据建立可查范围

直接读取types、rooms、objects，不调用LLM、不读取每张图片。按room_ids统计区域对象数，按type_ids统计类型对象数；名称重复不合并区域。能力取实际运行状态：仅有矩阵但未配置匹配编码器时，visual_search仍不可用。

### 3.2 search_candidates：区域筛选 → 双路召回 → 排序 → 分页

1. **限定范围**：校验room_ids，按对象区域归属取得对象集合；未指定区域则全场景。
2. **标签路**：Unicode NFKC/casefold规范化；依次匹配对象名称、别名、类型名称/别名的精确命中、子串命中，再匹配description或attributes值的子串。按匹配层级和ID稳定排序得到标签排名。
3. **视觉路**：编码query、L2归一化；对有效对象图像向量计算余弦相似度，默认取前10。无有效feature_refs的对象不进入这一路。
4. **单查询合并**：取标签命中与视觉前10的并集；使用等权RRF排名融合：

   `score(o,q) = Σ 1 / (60 + rank_channel(o,q))`

   仅对实际命中的通道求和。这里没有Router输出的map_weights，也没有三图加权投票。
5. **多查询合并**：按object_id去重，保留matched_queries及原始分数。Agent展示层按 `max_q score(o,q)` 排序，同分按ID；避免简单累加使多个近义查询重复加权。
6. **投影与分页**：只给ID、名称、区域、命中query；默认第一页5条，next_page继续取当前召回池。翻页会重新执行检索，当前没有分页结果缓存。

自由中文描述不会自动翻译，组合文本也不会自动拆词。例如“设备 equipment”不是自动分成两个独立查询；模型需要主动传多个queries。对精确中文类型名才使用类型的第一个alias编码。

`next_page=null`表示当前合并召回池读完；底层每query视觉前10可能仍有截断，因此还要看retrieval_truncated。需要扩大范围时可改写query、放宽区域，不能把翻完一页当全场景不存在。

### 3.3 inspect_objects：ID → 观测 → 图片

校验ID → 读取对象详情 → 选择target_support优先、observation_id稳定排序的前两个关联视图 → 加载原图 → 按crop_xyxy裁剪 → 生成“完整视角＋物体细节”图片板。

Agent默认只发送第一个视图，每对象一张图片板；需要第二张时传view=2。第一版原始渲染仍生成两张，精简发生在发送层。缺图片时返回实际错误；view=2不存在时返回空evidence和next_view=null，不虚构第二张图。

模型输入统一叫“物体图片”，图片上只有对象ID、名称和视图标识。source_kind等来源信息保留在原始包和trace，而非反复写入prompt。

### 3.4 view_map：共享召回 → 候选周边几何投影

有queries时复用底层双路召回；无queries时显示指定区域内对象。空间图从候选中心范围外扩至少2米或跨度18%，空候选使用区域/场景范围。各分页保持同一坐标尺度。

背景按当前视窗取点云，以局部Y高度15%—85%分位过滤后投影到XZ、作密度着色与轻微平滑，并叠加淡色对象包围盒。候选绘制ID＋短名称，保留类别/数量图例、比例尺和右下角X/Z箭头。

这是帮助观察布局的几何图；当前没有自动生成near、left_of等关系事实，也不计算可通行路径。需要上下关系时结合三维中心/图片；俯视图不能独自表达所有高度关系。

## 4. 交互、追问与状态

### 4.1 当前系统的判断原则

模型按地图和物体图片所呈现的场景匹配用户条件。看到明确匹配就选择；出现会影响选择的歧义时才问一个具体问题。避免只说“无法确定”，应指出已找到什么、还缺哪个区别。

| status | 场景 | 返回行为 |
|---|---|---|
| resolved | 一个对象符合条件且已取得对应图片证据 | object_ids恰好1项，question=null，selected_id由宿主设置 |
| ambiguous | 多个可能对象，或当前候选不足以唯一确定 | 返回仍符合条件的候选，提出具体区分问题 |
| insufficient_evidence | 当前资料没有足够信息匹配目标 | 说明具体缺口，可询问位置/外观；不把被排除对象填入候选 |
| needs_context | 缺少“第二个”“我左边”等必要参照 | 请求相应上下文 |
| cancelled | 用户取消当前目标 | 返回取消，object_ids为空 |

grounding字段：`status,object_ids,evidence_ids,reason,question`，可加`excluded_ids`。候选和排除对象必须互斥，且ID已通过工具返回；resolved还必须引用已提供的、包含对应对象的图片证据。引用校验不自动证明模型视觉判断正确。

### 4.2 具体问法

- 三把椅子都符合“空着”：说明可见的区别，再问选哪把；不因图片来源自动转成要求实时画面。
- 找到白色消防箱，但“白色柜子”还可能指别的对象：问是否这个箱体，或请补充区域/附近物体。
- 两个区域名称相同：用区域ID和已有内容区别，问用户指哪个。
- “我左边”：先确定“我”的位置与朝向。现有固定X/Z方向不能直接替代用户朝向。
- “最近”：明确距离参照和距离类型。当前地图工具未提供路径服务，不把几何距离称作路径距离。

### 4.3 单次工具循环与跨轮会话必须区分

当前run_agent能在一次请求内多轮调用工具并返回question；它**没有持久会话管理器**。调用者可传context，但该字典目前直接交给模型，没有专门的会话schema或“第二个”解析状态机。20条任务中的context是测试材料，不能据此宣称完整多轮产品已经实现。

后续宿主会话建议保存以下字段（建议，不是当前已实现HTTP接口）：

```json
{
  "session_id":"session-001",
  "scene_id":"mfloor",
  "snapshot_id":"mfloor-20261110-f99fa34a71bb",
  "presented_candidate_ids":["S023","S034","S035"],
  "previous_selected_id":null,
  "excluded_ids":[],
  "pending_question":"你想去哪一把旁边？",
  "last_evidence_ids":[],
  "user_pose":null,
  "robot_pose":null
}
```

应保留对用户展示的顺序，才能解释“第二个”；改口时更新候选并保留排除对象；取消时清除待确认目标；换快照时重新核对引用。用户“确认”不应跳过导航绑定检查。现有nav_target的会话确认是另一套已部署服务，尚未接入本模块。

## 5. 初步验证 benchmark

### 5.1 任务来源与运行口径

任务源文件为 [navigation_requests.json](../tasks/navigation_requests.json)，共20条，来自用户需求片段、讨论示例及派生设计。每条grounding_truth目前仍待审核，不能计算可信的20题准确率，也不能把source_anchors当作标准答案或输入提示。

本地场景为M-floor快照，191条源对象记录、61种类型、5个区域、382个物体视图；对象记录数不等于独立实物数。

表中“精简版”指compact_online_20261111这次最新在线对照；“旧链路”是早期完整上下文版本；“图片prompt复测”是去掉来源提醒后、尚未精简候选的版本。没有把各版本最好结果拼成一套准确率。

| ID | 指令（原文） | 覆盖能力/必要上下文 | 已有运行情况 |
|---|---|---|---|
| NG01 | 去会议室的柜子那里 | 类型＋区域、标签/图片冲突 | 旧链路：insufficient_evidence；精简版未跑 |
| NG02 | 去那个白色柜子那里 | 外观属性、多候选 | 精简版：ambiguous，候选indoor-041 |
| NG03 | 去那个像打印机的设备那里 | 模糊外观、泛类别 | 精简版：resolved，S135 |
| NG04 | 找个能打印文件的地方 | 功能与外观区别 | 尚无完整Agent运行 |
| NG05 | 去门旁边那张桌子 | 参照物＋相对关系 | 仅检索探针；无完整Agent运行 |
| NG06 | 去靠墙的柜子那里 | 墙体参照与物体关系 | 尚无完整Agent运行 |
| NG07 | 去桌子下面那个箱子旁边 | 高度关系＋目标区分 | 尚无完整Agent运行 |
| NG08 | 去我左前方那个柜子 | user_pose/robot_pose均null | 尚无完整Agent运行 |
| NG09 | 去离我最近的柜子 | 缺位姿和path_provider | 尚无完整Agent运行 |
| NG10 | 去会议室 | 区域目标；referent_kind=region | 尚无完整Agent运行；当前finish以对象ID为中心 |
| NG11 | 去玻璃隔断区 | R03/R04重名 | 旧链路：ambiguous；精简版未跑 |
| NG12 | 去 S032 那张桌子那里 | 显式稳定ID | 尚无完整Agent运行 |
| NG13 | 就第二个吧 | 已展示顺序[S034,S035]及快照 | 尚无完整Agent运行；会话机制待实现 |
| NG14 | 不是那个，去另一个 | 上次S034；候选[S023,S034,S035] | 尚无完整Agent运行 |
| NG15 | 算了，不去了 | 待确认S032 | 尚无完整Agent运行 |
| NG16 | 去那个急救箱 | 源标签与物体图片冲突 | 旧链路：insufficient_evidence；精简版未跑 |
| NG17 | 去会议室里现在空着的椅子旁边 | 按图片状态匹配、多个椅子 | 图片prompt复测：ambiguous，S023/S034/S035 |
| NG18 | 去没有贴标签的那个打印设备 | 未登记物体发现 | 尚无完整Agent运行；当前仅检索登记对象 |
| NG19 | 确认，就去那里 | pending=S032；binding_verified=false | 尚无完整Agent运行；当前不能生成导航目标 |
| NG20 | 去那两个摄像头旁边 | 重复实例与数量指代 | 尚无完整Agent运行；resolved当前限单对象 |

任务中的历史上下文可能包含旧版测试约定，例如NG17的图片来源说明；后续建立正式benchmark时应统一任务上下文和版本。上表不把“尚未运行”当作模型失败，也不把接口结构通过当作语义正确。

### 5.2 已完成的两例对照

同一模型 `claude-sonnet-5-5`、相同场景、指令和系统prompt；改变工具上下文、显示排序和按需获取方式。

| 指标 | 白色柜子：精简前→后 | 像打印机：精简前→后 |
|---|---|---|
| 首次search候选数 | 27→5 | 19→5 |
| 提供的物体图片数 | 16→5 | 8→4 |
| 提供的空间图数 | 0→1 | 0→1 |
| 模型请求轮数 | 5→6 | 4→5 |
| 工具调用数（不含finish） | 4→6 | 4→6 |
| 结果 | 请求超时→ambiguous | S135→S135 |
| 时间 | 旧版单请求120秒后超时；新版总97.32秒 | 旧版约24.50秒；新版25.98秒 |

旧版成功用例时间由文件时间估算，新版由monotonic计时；没有为失败用例虚构总时间。图片数按工具向模型提供的次数计，不包含后续请求重复携带历史图片的网络负载。

结论：减少了首屏候选与图片，但增加了工具轮次，不能据单次结果宣称整体提速。记录见 [comparison.json](../runs/compact_online_20261111/comparison.json)。14项unittest覆盖数据合同、图片实际传递、分页、视图选择、ID/引用与截断边界；不计入语义准确率。

### 5.3 正式benchmark应补齐什么

先由独立审核者给20条任务标注允许目标集合、排除对象、可接受追问及证据；多轮任务补充用户后续回答，明确多解。确认同一物体的重复记录后再定义实体级评分。数据无法支持的功能、区域导航或未知物体任务应标为能力缺口，不强制填一个对象答案。

之后固定场景哈希、模型与prompt版本、编码器身份、工具预算，重复运行两种链路。分别报告召回覆盖、最终指代正确率、适当追问率、无依据选择率、完成率、超时率、耗时分布、工具/图片/输入token开销。当前尚没有这些完整统计。

## 6. 一个真实trace：去那个像打印机的设备那里

### 6.1 运行身份

- 请求：`去那个像打印机的设备那里`，context为 `{}`。
- 场景：`mfloor` / `mfloor-20261110-f99fa34a71bb`。
- 模型：`claude-sonnet-5-5`；记录目录：[compact_online_20261111/NG03](../runs/compact_online_20261111/NG03)。
- 总计5轮模型交互、6次地图工具调用、4张物体图片＋1张空间图，25.98秒。
- 该trace发生在空间图美化之前；下述链接展示当时真正发给模型的图，不用新图替换历史证据。

### 6.2 逐轮展开

**第1轮：发现场景，并尝试打印机检索。** 模型同轮调用：

```json
{"name":"list_scene","arguments":{}}
{"name":"search_candidates","arguments":{"queries":["打印机","printer","复印机 copier"]}}
```

第一页返回 `S087 标识、indoor-026 开关面板、S011 椅子、S088 标识、S157 玻璃门文字`。这些结果没有直接提供打印设备目标。

**第2轮：改查泛类别，但组合描述仍未命中理想对象。**

```json
{"name":"search_candidates","arguments":{"queries":["设备 equipment"]}}
```

返回 `S052 讨论室、S067 标识、indoor-040 灭火器箱、S113 灭火器、S062 告示牌`。这是实际召回结果，不应在报告里改写成“成功召回设备”。

**第3轮：用精确中文类别查看空间图。**

```json
{"name":"view_map","arguments":{"queries":["设备"]}}
```

原始view_map返回16条候选，其中包括 `S129、S131、S132、S135` 四条标为“设备”的对象，以及一张空间图。模型由此拿到了后续检查的ID。这个步骤同时暴露了第一版的不一致：view_map仍返回完整召回池，能绕过search首屏5项限制。本次不是依靠分页找到S135，也不能归因于首轮向量命中。

**第4轮：分两次调用查看四个对象。**

```json
{"name":"inspect_objects","arguments":{"ids":["S129","S131","S132"]}}
{"name":"inspect_objects","arguments":{"ids":["S135"]}}
```

每个对象实际发送一张物体图片，总4张。模型观察后将前三个排除，把S135作为目标。原始工具trace仍保存另外一个视图的元数据，未发送的图片不计入“模型看过”。

**第5轮：调用finish。** 以下为实际参数：

```json
{
  "status":"resolved",
  "object_ids":["S135"],
  "excluded_ids":["S129","S131","S132"],
  "evidence_ids":["1b67d02b5647ee3fb88bd7b1"],
  "reason":"S135(走廊)是一台灰白色的落地箱式机器,顶部有深色面板,外形像打印机或复印机。其他“设备”不符合:S129 是温控器和开关面板,S131 是桌面插座盒,S132 是桌面摆件。",
  "question":null
}
```

宿主校验后生成 `selection.selected_id=S135`，导航goal仍为null。这一记录证明链路执行及模型选择，不单独证明设备实际功能。

### 6.3 如何查看和复核

[打开这条真实图文trace](http://127.0.0.1:18765/updates/compact_compare_v2_20261111/NG03.html)；[打开前后对照](http://127.0.0.1:18765/updates/compact_compare_v2_20261111/comparison.html)。外网电脑需先建立已有SSH隧道；这些是本地运行页面，不是公共互联网地址。

原始目录每轮保存：

```text
NG03/
  protocol.json                 # 实际工具定义、生成参数和宿主决策schema
  00-request.json               # 模型消息，包含系统prompt、用户输入和后续历史图片字节
  00-response.json              # 服务返回的原始模型响应
  00-00-tool.json               # 第一个原始工具完整结果
  00-00-model-context.json      # 精简后实际发给模型的工具文本
  ...
  result.json                   # 最终grounding / selection / navigation
../media/                       # 实际证据图片文件，以内容哈希命名
../report.json                  # 用例状态及计时
```

trace记录服务返回消息与实际调用，不声称包含模型内部思考。图片media_id可对应PNG与请求中的base64字节；图文导出器核对字节匹配后才展示为模型已见图片。原始工具数据和模型上下文分开保存，便于判断是召回问题、信息裁剪问题，还是模型判断问题。

实际系统prompt原文保存在每条trace的00-request.json，当前代码位于agent.py的PROMPT。复核历史结果应使用记录中的prompt与工具定义，不用当前文件推定历史运行配置。
