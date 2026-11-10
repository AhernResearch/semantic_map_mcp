# 本地实现验证记录

日期：2026-11-11。当前验证为本地真实工具与OpenCLIP模型执行；没有接入自主多模态Agent或机器人。

## 本次执行

```bash
PYTHONPATH=map_query_tools/src envs/cg/bin/python -m map_query_tools import-mfloor \
  '/home/agiuser/下载/同事4090_20261110/mfloor_agent_minimal' \
  map_query_tools/local_data/mfloor-v1

PYTHONPATH=map_query_tools/src envs/cg/bin/python -m unittest discover \
  -s map_query_tools/tests -v

PYTHONPATH=map_query_tools/src envs/cg/bin/python -m map_query_tools call \
  map_query_tools/local_data/mfloor-v1 list_scene \
  --output map_query_tools/runs/cli_list_verified
```

- 转换与加载：191条对象记录、382个GS视图、61个语义类型、4个源区域及1个未归属容器；包路径 local_data/mfloor-v1。
- 9项unittest通过：房间范围内向量召回、无效特征排除、保留标签候选、缺模型状态、空结果、非法参数/ID、证据像素映射、地图变换、哈希/路径/裁剪/特征引用及编码器身份。
- 三工具输出均经过各自outputSchema验证。CLI真实执行结果位于 runs/cli_list_verified/result.json。
- 通过Python API真实加载OpenCLIP H/14：runs/mfloor_final/summary.json。会议室“椅子/门”返回22个候选/2张地图；英文描述“a white cabinet / a device that looks like a printer”返回20个候选/2张地图；S003/S108返回4张全图+裁剪证据板。
- 模型SHA256：0084e75319a50ad85ef45377bad5bc38f2f58824459eb690048d51c9f8863be5。使用本机已有semantic_map_gen/package/data/models/semantic_mapping/clip/ViT-H-14.safetensors，未下载、替换或修改权重。
- 三个源text_ft与按原CLASS_PROMPTS编码结果的余弦分别为0.999999404、0.999999702、0.999999464，记录在runs/mfloor_smoke/encoder_identity_probe.json。这支持特征空间匹配，不是语义准确率指标。
- 人工查看了会议室候选图和S003证据板，确认ID、图例、源标签与全图/裁剪可读。S003源标签“急救箱”与图片内容存在可见疑点，保留原标签和未核验状态，没有自动改名。
- 早期不配模型的结果保留于runs/mfloor_smoke/view_map.json，明确为partial；后续真实模型结果另存runs/mfloor_final，不覆盖这条限制记录。

## 未验证范围

未独立审核候选身份、所有对象的相机投影精度、实体去重或语义正确率；中文自由描述质量未验收。未接Agent自主工具循环、HTTP/MCP或导航。没有额外视角渲染、整图未登记物体检索和动态现场状态。

## 历史：仅契约阶段

### 契约检查记录

日期：2026-11-11。检查对象为规范和接口骨架，没有运行地图服务或真实模型。

执行：

- `python -m compileall -q map_query_tools/src`：Python Protocol和数据类语法检查通过。
- 使用已有 `envs/cg/bin/python` 中的 `jsonschema.Draft202012Validator` 执行内联检查：没有安装或修改环境。
- `check_schema` 检查scene schema及三个工具的输入/输出schema，共7份，通过。
- examples/scene.json通过场景schema；三个工具请求和响应分别通过对应schema。
- 7个非法输入检查均拒绝：list_scene多余参数、view_map未知mode、空查询词、超过4条查询、inspect空ID集合、重复ID、超过8个ID。

未验证：生产PackageValidator要求的跨字段/资产/坐标检查（尚未实现）、M-floor真实包转换、特征模型匹配、向量召回、topdown渲染、图片返回、多模态Agent运行和语义正确性。JSON示例是人工结构样例，partial响应不伪装成已有渲染结果。

## 默认空间简图修订

按用户要求，view_map默认改为白底候选中心+ID，不画点云/GS和对象外观；源区域范围用虚线，多页保持同一坐标范围。沿用三工具输入输出契约。

执行 `PYTHONPATH=map_query_tools/src envs/cg/bin/python -m unittest discover -s map_query_tools/tests -v`，9项通过。真实H/14查询R01“椅子/门”，返回22个候选、一张空间简图，结果为ok，保存于runs/schematic_preview/result.json。已查看图片，检查文字可读及点和ID的就近对应；未将这项人工绘图检查视为模型空间理解准确率验证。
