# map_query_tools

当前第一版代码基线：**v1**。功能、最新图样和验证范围以[第一版说明](docs/V1_BASELINE.md)为准；以下保留了迭代过程中的设计说明。

面向多模态 Agent 的只读语义地图工具库，契约1.0.0。

```python
list_scene()
search_candidates(queries, room_ids=[])
view_map(room_ids=[], queries=[])
inspect_objects(ids)
```

已实现本地场景包校验/加载、M-floor转换、类别与OpenCLIP向量召回、只标候选ID的topdown空间简图、批量对象证据图片和CLI。每个实例绑定一个快照，工具返回JSON以及实际PNG字节。没有HTTP/MCP部署或机器人运动接口。

## 当前研究主线

暂停继续调整topdown画法，转向“模糊指令 → 候选取证/澄清 → 确定指代 → 校验导航目标”。现有工具仍可用，地图不是必经步骤。见[任务定义](docs/TASK_GROUNDING.md)、[20条待审核指令](tasks/README.md)和[首次检索/图片检查](docs/TASK_AUDIT_20261111.md)。已实现有轮数上限的看图 Agent 原型；已接入用户指定网关的 Sonnet 多模态接口，测试情况见 docs/SONNET_PILOT_20261111.md，M-floor 未绑定导航站位。见[Agent 运行与验证](docs/AGENT_LOOP.md)。

## 文档

- [统一输入包与数据结构](docs/DATA_CONTRACT.md)
- [工具参数和输出契约](docs/TOOL_CONTRACT.md)
- [设计与模块边界](docs/ARCHITECTURE.md)
- [场景Schema](schemas/scene.schema.json)、[工具Schema](schemas/tools.json)
- [验证结果](docs/VALIDATION.md)

## 本地运行

在工作区根目录执行；以下使用本机已有环境，不安装或修改建图环境。普通环境需要requirements.txt；真实视觉检索另外需要torch、open_clip_torch与匹配的本地权重。渲染默认使用系统NotoSansCJK字体，也可在Python构造函数中传入font路径。

```bash
# 源包只读，输出必须是不存在的新目录。
PYTHONPATH=map_query_tools/src envs/cg/bin/python -m map_query_tools import-mfloor \
  '/home/agiuser/下载/同事4090_20261110/mfloor_agent_minimal' \
  map_query_tools/local_data/mfloor-new

# 每次CLI调用写入新的输出目录。
PYTHONPATH=map_query_tools/src envs/cg/bin/python -m map_query_tools call \
  map_query_tools/local_data/mfloor-v1 list_scene \
  --output map_query_tools/runs/list-new

# --args 是包含room_ids/queries的JSON文件。
PYTHONPATH=map_query_tools/src envs/cg/bin/python -m map_query_tools call \
  map_query_tools/local_data/mfloor-v1 view_map \
  --args map_query_tools/examples/query.json \
  --encoder-config map_query_tools/runs/mfloor_smoke/encoder.json \
  --output map_query_tools/runs/query-new

PYTHONPATH=map_query_tools/src envs/cg/bin/python -m unittest discover \
  -s map_query_tools/tests -v
```

不传encoder-config时，带查询的view_map明确返回partial和feature_unavailable，保留类别检索；无queries浏览和inspect_objects不需要模型。不会自动下载权重或替换编码器。

Python宿主可直接复用已加载的实例，避免每次CLI重新加载模型：

```python
from map_query_tools.store import SceneStore
from map_query_tools.service import MapTools

store = SceneStore('map_query_tools/local_data/mfloor-v1')
tools = MapTools(store, 'map_query_tools/runs/python/media')
result = tools.view_map(['R01'], ['椅子', '门'])
# result.envelope: JSON结构；result.media: 带实际PNG字节的MediaPayload。
# 宿主应把这些字节作为图片输入交给多模态模型，不只发送uri文本。
```

## 编码器配置

配置文件由宿主提供，不是模型工具参数。必须指定space_id、encoder、weights_fingerprint、preprocess_fingerprint、checkpoint、checkpoint_sha256；device可为cpu或cuda。字段与场景空间一致且文件SHA匹配才加载。示例结构见[encoder.example.json](examples/encoder.example.json)，其中示意路径和SHA不可用于实际运行。

M-floor已有61类人工英文别名，精确中文类别查询使用对应英文编码，并在groups.encoded_query记录；任意描述没有隐式LLM翻译，原文直接编码。当前真实测试使用英文描述，中文自由描述质量未验收。

## 当前范围与限制

- 191条源记录、382个GS视图已转换为统一包；它们不是191件已确认独立实物。
- 候选返回保留标签命中与向量得分；相似度不是概率。空间关系由Agent看图判断。
- topdown默认白底，只绘制查询候选的中心点和稳定ID、源区域范围、方向与比例尺，不画点云/GS背景和对象轮廓。每页最多24条，最多8页，所有页面保持相同空间范围；超出通过unshown_ids明确报告。区域范围用虚线标注，不冒充真实墙体。物体外观由inspect_objects提供。
- 对象证据目前按target_support优先、ID稳定排序选最多2个视图；未实现额外视角多样性优化或新的GS渲染。
- 输入包损坏、哈希/引用/坐标错误在加载时拒绝。对象合法但无观测时按契约返回partial。
- 当前无通用未知物体检测、无整图语义检索、无导航站位绑定。未独立验证实物身份与语义准确率。

大数据在local_data/，运行结果在runs/，均已排除版本管理。旧0.2设计保留在工作区docs作为历史，以本目录契约为准。


## 当前空间图更新（2026-11-11）

view_map 已改为候选周边取景：候选中心范围外扩至少2米、或跨度的18%，保持X/Z等比例投影及所有分页一致的视窗。背景使用本地场景点云，按当前视窗内Y高度15%—85%分位保留后投影、密度着色、0.7像素平滑；并叠加淡色对象轴对齐包围盒。这里是几何背景，不是通行性或墙体识别结果。缺少背景资产时仍可绘制候选并在图中明确说明。标签为ID＋短名称，图像/像素矩阵合同不变。无候选时沿用区域或场景范围。

此前“纯白底、仅中心点、虚线区域”的描述是旧版本。绘图参考semantic_map_query的分层与突出标注，未复制导航站位/路径逻辑。原始点云及对象数据不改。
