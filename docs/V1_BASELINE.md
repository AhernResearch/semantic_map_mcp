# 第一版代码基线

用户于2026-11-11确认保留当前实现为第一版，Git标签为v1。本地独立仓库：map_query_tools；不代表生产部署或导航验收。

## 当前行为

- 统一只读场景包、M-floor导入、list_scene/search_candidates/view_map/inspect_objects及本地CLI。
- 标签与CLIP视觉召回；Agent上下文每页5个候选，每次inspect最多3个对象、每对象默认1张图片，支持继续翻页和第二视角。
- 模型按物体图片匹配条件；支持原生工具调用、结构化finish、候选/排除/选定对象分离。原始trace和实际模型上下文分别保存。
- 空间图围绕候选取景，淡色点云投影与包围盒背景，彩色ID＋名称，类别数量图例、比例尺和右下角坐标箭头。右为+X，上为−Z；未记录北向，不标东西南北。已去掉顶部“空间图/候选周边”、解释副标题和调用工具提示。
- 独立图文trace导出器，包含实际图片、完整请求/响应、工具结果、失败轮次及归档。

## 验证与边界

14项unittest通过。真实模型对照记录见AGENT_LOOP.md与SONNET_PILOT_20261111.md：精简后NG02完成追问、NG03选S135，图片量减少；仅单次对照，未证明整体提速或语义准确率。数据与日志保留在被忽略的runs/local_data中，不随代码提交。

工具每轮限制不等同于每个模型回合的总图片预算；原生批次可调用多个工具。view_map仍可返回比search_candidates首屏更多对象。分页限于当前召回池，不代表穷尽全场景。原始inspect仍生成两张证据板，Agent仅发送所请求的视图。导航goal保持null，没有已核验站位绑定；无HTTP/MCP查询部署。

## 复核命令

在工作区根目录执行：

```bash
PYTHONPATH=map_query_tools/src envs/cg/bin/python -m unittest discover -s map_query_tools/tests -v
```

运行依赖、模型配置与场景输入见README.md。不要把本地环境、模型、凭据或场景资产打包进代码仓库。当前18765页面服务及VPS隧道属于本地运行安排，不随Git提交部署。
