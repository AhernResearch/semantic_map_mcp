# 统一数据与输入文件规范

版本1.0.0。机读约束见../schemas/scene.schema.json。schema负责结构，本文的跨字段规则同样必需。

## 1. 接收的标准目录

```text
<package>/
  scene.json                     # 唯一权威元数据入口
  assets/
    background.npz               # 可选，points/ colors
    objects/<id>.npz             # 可选，对象点云
    features/objects.npy         # 可选，浮点N×D图像特征
    images/<asset-id>.jpg         # 可选，但无图就无法视觉核验
  provenance/                    # 可选，源文件或导入记录
```

所有资产通过scene.json内assets注册：asset_id、相对path、sha256、kind、metadata。包不含模型权重、SQLite缓存或输出图片；这些是部署配置或派生产物。禁止绝对路径、逃逸包根的路径和链接、pickle及object dtype数组。NPY/NPZ读取使用allow_pickle=False，实际shape/dtype、哈希和有限数值均需检查。

目录名不承担语义，数组和图片可任意受控命名，引用靠asset_id。模型部署路径由宿主配置，不写进可移植包。

### 资产格式细节

- image：JPG/PNG，metadata保存width、height、mime_type，与解码尺寸一致。
- pointcloud：NPZ含points[N,3]浮点米制、colors[N,3]的uint8 RGB；metadata保存frame_id、point_count、points_dtype、colors_dtype。无颜色可省略colors，但metadata注明has_colors=false；渲染器用中性色。
- feature_matrix：NPY二维浮点数组，metadata保存shape、dtype、feature_space_id；行号由feature_refs唯一绑定到对象。同一空间同一row不能无说明绑定多个对象。
- 背景和对象都可引用pointcloud资产。全部包内点云已转换到manifest统一坐标，源转换只记录provenance，不在查询时重复执行。

## 2. scene.json 顶层

| 字段 | 必需 | 含义 |
|---|---|---|
| schema_version | 是 | 固定1.0.0 |
| scene_id、snapshot_id、name | 是 | 场景、不可变版本、显示名 |
| coordinate_frame | 是 | frame_id，unit=m，up_axis=y；源转换登记provenance |
| bounds_xyz | 是 | min/max三维范围 |
| types | 是 | SemanticType数组，可空 |
| rooms | 是 | RoomRegion数组，可空；对象无有效区域需生成显式未归属区域 |
| objects | 是 | MapObject数组，可空 |
| observations | 是 | 完整视图数组，可空 |
| assets | 是 | 资产表，可空 |
| feature_spaces | 是 | 特征空间与矩阵，可空；无特征则visual不可用 |
| background_asset_id | 是 | 背景资产ID或null；无背景只画框并说明 |
| provenance | 是 | 来源记录、变换、局限，不保存凭据 |

有效空场景可返回空结果。只有框的场景可以展示、只有标签的场景可以精确召回；无图/无特征必须反馈能力缺失，不为凑结构生成假资产。

## 3. 类型、区域与对象

SemanticType：`type_id、name、aliases[]`，type_id快照内唯一。对象type_ids可以多个；未知类型用显式unknown类型，不改写原标签。开放描述查询不受types列表限制。

RoomRegion：`room_id、name、kind、bounds_xz、polygon_xz、boundary_source`。kind为room/corridor/area/unassigned，水平轴XZ，边界可为null。polygon必须有序且闭合；包围矩形不能标成精确房间轮廓。几何未知仍可通过归属查看对象范围。

MapObject：

| 字段 | 含义 |
|---|---|
| object_id / source_id | 统一查询ID / 原始ID；不能当导航ID |
| type_ids、label、aliases、description | 类型引用、原名称、别名、描述（可null） |
| room_ids、region_assignment_source | 归属引用及来源；可多区域，计数按记录去重 |
| bbox、center_xyz | Y-up米制AABB与几何中心；中心为(min+max)/2，不是接近站位 |
| geometry_asset_id | 对象NPZ或null；无点云退到框投影 |
| feature_refs | space_id、row、valid；一条记录可多特征空间，不混合原始分数 |
| observation_links | observation_id、crop_xyxy、target_support；crop可null，支持可unknown |
| attributes | key、value、source、verification；value为JSON值，允许同key冲突 |
| identity_status / entity_id | unresolved/reviewed；未经审核entity_id=null |
| source_links | target_object_id、kind、source；明确只是源关联 |
| provenance | 原字段来源、核验限制等 |

object_id稳定，不用画图时的临时排序号替代。跨快照引用需连同scene_id和snapshot_id；source_id可同名但object_id在当前快照必须唯一。保留原始巡检和open-vocab两套记录，不自动把重叠框合并成一物。

## 4. 观测与图片

Observation字段：observation_id、image_asset_id、width、height、source_kind、reconstruction_id、captured_at、rendered_at、K、camera_to_world、camera_convention、provenance。

source_kind为real_camera或gs_render；派生topdown和证据拼图只属于运行输出，不作为原始观测。GS的captured_at不知道就null；渲染时间不冒充采集时间。同重建的视图共享reconstruction_id。

K和camera_to_world都可null。具有位姿时必须声明相机约定；导入器转换为opencv_xright_ydown_zforward，camera_to_world为4×4列向量变换。轴未核实则位姿置null并保留来源，不猜朝向。K尺寸为3×3，与原图尺寸匹配。

对象crop_xyxy为原图整数像素[x0,y0,x1,y1)，左上原点，必须在原尺寸内。缺裁剪时可返回完整图，不能凭空产生精确可见掩膜。对象在源关联中出现不代表一定清晰可见。

## 5. 特征空间

FeatureSpace：space_id、encoder、weights_fingerprint、preprocess_fingerprint、dim、normalized、matrix_asset_id。

matrix资产必须声明shape=[N,D]和dtype=float32或float16，D与dim一致；feature_refs.row在N范围内。实际值有限且非零；查询时重新L2归一化。valid=false的记录不进入视觉排名，但仍能标签召回。

文本编码器必须核对encoder、权重和预处理协议身份；不能仅因输出1024维就认为匹配。weights_fingerprint可为可复核SHA或固定上游版本标识，不允许含糊的latest。文本向量在查询时生成，不把标签文本向量当作对象图像特征。

包中的特征是否存在和服务能否视觉检索是两层状态：后者还依赖已配置的匹配文本编码器。list_scene报告实际服务能力。

## 6. 数值与坐标约束

统一Y-up，单位m。Z-up源变换(x,y,z)→(x,z,-y)，反向(x,y,z)→(x,-z,y)，记入provenance；已是Y-up的对象不重复变换。bounds、bbox所有坐标有限且min<=max，center与框一致，背景/对象/相机必须同frame。

topdown默认屏幕右+X、上-Z，Y为高度；每张图含坐标轴与比例尺，不把+X称东。按统一比例投影，返回world_to_pixel，禁止各向拉伸。多个楼层自动分别成图，不让不同高度楼层混在同一平面；当前区域kind不承担楼层信息，跨楼层源数据应先作为独立场景快照输入1.0。

## 7. M-floor源输入适配

本地源：`/home/agiuser/下载/同事4090_20261110/mfloor_agent_minimal`。转换输出到新目录，不覆盖源包。

| 源输入 | 标准模型 |
|---|---|
| CG_portable/map.semantic.json → document.objects | MapObject、type词表、原标签、框、几何和特征引用 |
| CG_portable/arrays/*.npy | 安全读取$array引用；导出对象NPZ及对齐特征矩阵 |
| CG_portable/background.npz | 转换为统一Y-up背景 |
| INSTANCE_INDEX.json | 图像、K、选中视图、裁剪、支持状态 |
| HMSG/rooms/*.json、objects/*.json | 区域及对象归属；与CG按source_id连接 |
| images/、crops/ | 注册图片资产；裁剪与全图保持对应 |
| source_annotations、SCENE_GRAPH、质量与来源JSON | 保留源关联、限制和导入记录 |

HMSG视图ID与图片文件名需按元数据显式映射，不按后缀猜。当前191条是源记录，不是191个已审核实体；190条视觉支持有效，S143无效状态保留。CLIP空间为OpenCLIP ViT-H-14 / LAION2B s32B b79K，实际权重与导入矩阵对应仍需校验。

M-floor的382张选中视图来自GS，源框参与视图/裁剪选择，不能当成独立实拍或场景无偏覆盖。区域归属未核验项保留；reachable=false来自never walked不导入为不可达结论。本库不提供机器人可达性字段。

## 8. 必须执行的跨字段验证

schema之外需校验：所有ID唯一、引用存在、room/type/asset引用类型正确、数组行号对应ID、特征空间身份、框中心与范围、K和原图大小、刚体矩阵和相机约定、裁剪边界、所有路径/哈希、NPZ字段shape。未知源字段保存在provenance而非静默删除；来源冲突可保留，但无效几何或越界资产不能正常载入。

scene.json为权威版本，缓存需绑定scene.json与资产哈希、转换器/索引版本；缓存不能悄悄修正源记录。当前SceneStore已验证schema、资产哈希、路径、数组shape/有效值、类型/区域/观测引用、框中心、裁剪范围、相机矩阵和特征行。尚未做现场坐标/身份验证。
