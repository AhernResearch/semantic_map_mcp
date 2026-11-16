# 多模态Embedding离线对照

使用ModelScope的Qwen/Qwen3-VL-Embedding-2B，对比仅描述、仅图片、图片加描述，以及原CLIP图文向量通道。执行脚本：[compare_multimodal_embeddings.py](../scripts/compare_multimodal_embeddings.py)。本轮不切换MCP，不运行完整Agent，不调用在线描述生成模型。

## 冻结协议

- 场景为已内嵌描述的新快照，所有模式在同一189个有成功描述、有效图片和CLIP特征的对象记录中排序。
- 描述组189条；图片组372个有效视角、190个对象；图文组按每视角配相同对象描述。自然索引覆盖与公共评价范围分别记录。
- 查询为前面8个样例对象的16条英文派生诊断查询，另含“白色柜子”和“像打印机的设备”两条开放查询。查询在建索引和查看排名前冻结，但编写时已经接触样例图片和描述；不是独立留出测试。指定对象不是穷尽的人工正确答案，指标称anchor hit，不称语义准确率或现场任务成功率。
- 各模式均不含标签/OCR关键词融合。CLIP使用已有对象图像向量，其context25-squarepad预处理与本轮原始裁剪不同，不能把差异全部归因为模型架构。
- Qwen三组使用同一权重、2048维、默认指令Represent the user's input.，右侧padding、最后有效token池化、L2归一化。最终试验使用官方示例的vLLM pooling路线，显式LAST池化，图文预处理缓存和前缀缓存关闭，不执行远程代码。算法参考[官方实现](https://github.com/QwenLM/Qwen3-VL-Embedding/blob/main/src/models/qwen3_vl_embedding.py)，官方文件另存运行目录供核对；没有采用其中异常后改成NULL输入的处理。
- 使用有效观测的原图裁剪，无ID/旧标签标题；处理像素上限262144，最多2048token，batch=4。记录真实图片哈希、来源观测和裁剪、实际token数。
- 每视角一条向量，按对象取最大余弦分数后去重，分数相同时按ID排序；不混合不同向量空间。
- 查询18条逐条编码，每条测3次，共54次；首次预热另计。对象索引各建一次，记录图片/图文预热和建库时间；无自动重试。GPU计时包含同步、预处理和数据搬运，查询不包含模型加载。

所有模型、索引与图片位于源码目录外。模型目录：`/home/agiuser/lx_workspace/models/Qwen3-VL-Embedding-2B`。运行目录：`/home/agiuser/lx_workspace/runtime/map-embedding/qwen3vl-vllm-20261112/`；trial/protocol.json冻结查询、脚本/模型/场景哈希和输入映射，trial/results.json保存逐查询排名与计时，trial/*-index.npy保存可重建索引，trial/token-audit.json保存实际token记录。NPY读取必须allow_pickle=False。

实验临时停止本会话创建的Qwen3.5描述生成服务以腾出显存，运行器finally中按所有权清单恢复。既有两个地图MCP保持运行。恢复状态单独保存在restore.json。

初次原生Transformers试跑和禁用cuDNN-SDPA的诊断保留于runtime/map-embedding/qwen3vl-20261112；前者在图像阶段因耗时过高被显式中断，只有CLIP和文本组完成。诊断曾与该图像阶段重叠，因此不将其建库耗时纳入最终对照。最终使用新目录完整重跑四组；保留所有试验和服务恢复日志。

## 实测结果

最终环境：RTX4090，vLLM0.17.1，PyTorch2.10.0+cu128，Transformers4.57.6。vLLM使用FLASH_ATTN，eager模式，max_num_seqs=4，显存预算0.40。权重加载日志报告4.24GiB；新引擎初始化19.69秒。跨进程引擎不能用父进程torch.cuda的峰值当成模型显存，结果中明确标记此限制。

| 向量通道 | 指定记录命中Top1 / 16 | 指定记录命中Top5 / 16 | 查询编码中位 / P95 | 离线索引时间 |
|---|---:|---:|---:|---:|
| 原CLIP | 9 | 14 | 4.37 / 4.49ms | 复用已有图像特征，未重建 |
| Qwen仅描述 | 9 | 15 | 8.18 / 8.43ms | 0.748秒，189条 |
| Qwen仅图片 | 2 | 11 | 同一组文本查询向量 | 8.623秒，372条 |
| Qwen图文联合 | 8 | 16 | 同一组文本查询向量 | 8.737秒，370条 |

索引时间不包含模型加载、预热和预先裁剪准备，包含逐批读取图片、编码和数据搬运。图像首次预热1.694秒；实际输入最多356token，均未截断。形状、有限值、归一化、裁剪字节哈希、对象去重与公共候选集检查通过；25项unittest通过。

本轮联合输入使指定对象全部进入前5名，但Top1低于CLIP和仅描述组。仅图片组表现较弱，不能据此直接用新模型图像向量替换CLIP。样本与已有描述有关，不能宣称100%语义准确率。

仅描述组漏掉的查询是a white fire equipment cabinet：indoor-041排第16。现有描述只写white metal cabinet和海报，没有明确写出fire equipment；图文组将该指定对象召回到前5。此为该条排名变化的可能解释，未做独立因果消融。

开放查询a device that looks like a printer：S135在CLIP第2、仅描述第3、图文联合第1，仅图片未进前10。这是指定记录排名，不代表实物类型被确认。a white cabinet各组返回不同候选，仍需Agent判断或澄清。

结论：图文联合值得进入MCP候选召回试验；本轮没有验证完整Agent提速。查询编码比CLIP约多3.8ms，其潜在价值在于候选质量和减少后续交互。两个地图MCP未切换；描述生成服务已按原所有权配置恢复并通过health检查。
