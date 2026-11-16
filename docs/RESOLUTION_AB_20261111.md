# 降低拼图分辨率对照（2026-11-11）

固定候选的小样本对照显示：1800×800缩为1200×533后，模型请求平均耗时降低约39%–42%，上报输入token减少约43%。完整Agent表现暂不能归因于分辨率：一条原图任务在看图前就出现模型工具参数错误，另一任务的检索路线与最终判断发生分叉。

## 控制条件

- 拼图格式保持1张图、每页最多5候选。只有拼图最大宽度改变：原始None与1200；高度等比四舍五入为533，像素数1440000→639600（减少55.6%）。PNG、LANCZOS缩放，无重新标注或源图修改。
- 系统prompt、工具定义、场景快照、CLIP身份、Sonnet模型、temperature=0、max_tokens=1600保持一致。每请求无自动重试，不切换模型。
- 固定候选阶段用office printer/white cabinet各检索一次，冻结相同5候选和顺序；每任务按原图→缩小→缩小→原图运行，共8次模型请求，socket超时45秒。固定候选JSON与提示词不因分辨率改变。
- 完整Agent阶段为打印机原图→缩小、白柜缩小→原图，共4任务，最多8轮、socket超时120秒。场景/编码器已加载，独立空工具请求缓存；从用户指令到最终结果或失败计时。
- 所有后续请求均只追加历史，不裁剪历史。实际request JSON逐轮前缀一致性及图片字节已检查通过。没有在后续轮次重新缩放旧图。
- 网关的cached_tokens字段均为0，无法据此确认上游真实缓存命中；本轮只证明客户端没有主动改写前缀。

## 固定候选结果

| 任务 | 原图两次耗时 | 缩小图两次耗时 | 原图均值 | 缩小均值 | 本组均值降低 |
|---|---|---|---:|---:|---:|
| 打印机 | 6.50 / 7.68秒 | 4.01 / 4.70秒 | 7.09秒 | 4.35秒 | 38.6% |
| 白色柜子 | 12.83 / 11.69秒 | 7.88 / 6.28秒 | 12.26秒 | 7.08秒 | 42.2% |

8次均收到完整响应，无超时或输出截断。打印机两种分辨率各2次均选择S135。白柜两种分辨率均为一次选择indoor-041、一次在indoor-041与S016之间澄清。只能说明本组小样本的选择分布一致，不是人工语义准确率或普遍无损证明；输出内容与长度也有变化，耗时差不等于单独测得的GPU图片处理耗时。

网关上报输入token：打印机2371→1346，白柜2384–2386→1362，约减少43%；请求JSON大小分别925KB→514KB、718KB→414KB。图片数始终为1。

该阶段只测冻结候选的一次判断，不是完整任务时间。

## 完整Agent结果

| 任务 | 分辨率 | 完整耗时 | 模型轮次 | 结果 |
|---|---|---:|---:|---|
| 打印机 | 原图 | 27.80秒后失败 | 2 | 查询参数应为数组，模型却给出字符串；尚未收到图片 |
| 打印机 | 缩小 | 11.83秒 | 2 | resolved，S135 |
| 白色柜子 | 原图 | 67.14秒 | 5 | resolved，indoor-041 |
| 白色柜子 | 缩小 | 33.61秒 | 5 | ambiguous，indoor-041/S003，要求澄清 |

打印机原图组只成功执行list_scene，随后模型search_candidates的queries不是数组，被边界校验明确拒绝；没有自动修复/重试。它尚未看图，不能认定错误由原图分辨率导致，也不能把27.8与11.8秒算作正常完成任务的提速比。

白柜原图组执行4次检索并累积4张1800×800图片；缩小组执行3次检索和1次list_scene，累积3张1200×533图片。两组首轮消息相同、首轮都查询白色柜子，但后续检索分叉。耗时约减半伴随最终决策不同，不能单凭秒数判定质量或分辨率的因果收益。模型选择不冒充人工/实物确认，navigation.goal仍为null。

## 实现与入口

`MapTools(..., montage_max_width=1200)`为显式宿主渲染选项；默认None仍保留原尺寸。只支持montage，配置与single同时使用会明确拒绝。小于上限的画布不放大。每张新拼图的cell/panel坐标同步缩放，原始裁剪坐标、原图尺寸、来源和ID保持可追溯。

```python
store = SceneStore('/path/to/scene')
tools = MapTools(store, '/path/to/new-output', encoder, montage_max_width=1200)
result = run_agent(tools, utterance, '/path/to/new-task-output', complete)
```

复现实验在原有两入口增加comparison开关，复用原来的请求及Agent循环，未另外创建一套Agent：

```bash
# 配置同一授权网关凭据至MAP_AGENT_API_KEY，输出目录必须新建。
PYTHONPATH=src HF_HUB_OFFLINE=1 /path/to/python scripts/compare_image_packing.py \
  --comparison resolution --max-tokens 1600 \
  --scene /path/to/scene --encoder-config /path/to/encoder.json \
  --output /path/to/new-fixed-output \
  --endpoint https://lingzhi.agibot.com/v1 --model claude-sonnet-5-5

PYTHONPATH=src HF_HUB_OFFLINE=1 /path/to/python scripts/compare_agent_packing.py \
  --comparison resolution \
  --scene /path/to/scene --encoder-config /path/to/encoder.json \
  --output /path/to/new-agent-output \
  --endpoint https://lingzhi.agibot.com/v1 --model claude-sonnet-5-5
```

## 验证与交付边界

19项地图unittest通过，新增缩放尺寸、媒体ID、原始裁剪坐标保留、面板坐标映射及非法配置拒绝检查。harness全量250通过，保留此前已在原始基线复现的2项reset_context说明失败；Ruff/mypy和前端构建通过。Pillow下界同步到9.1以使用Resampling枚举。

运行证据：runs/resolution-fixed-ab-20261111/、runs/resolution-agent-ab-20261111/。包含实际请求图片字节、响应、原始审计、分项耗时及source/源码快照（逐个校验sources.json哈希），未复制凭据。Agent目录另有history-checks.json记录完整前缀检查、图片尺寸和缓存反馈。敏感运行数据均被Git忽略。

结论：1200宽拼图值得继续作为试验选项，但当前任务数不足且完整Agent质量未核验；尚未更改在线8879/8880服务或默认分辨率。
