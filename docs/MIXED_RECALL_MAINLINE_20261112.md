# 图文混合召回主线

用户授权将图文混合向量召回纳入主线，检索默认返回文本描述。当前数据包为 `/home/agiuser/lx_workspace/data/map_query_tools/mfloor-mixed-20261112`，快照 `mfloor-20261110-f99fa34a71bb-qwen35-mixed-20261112`。

## 检索和返回

主向量空间为Qwen/Qwen3-VL-Embedding-2B、2048维、LAST池化、L2归一化。370条图片加描述向量对应189个对象；有效视角分别编码，先按对象取最大余弦分数去重，再按对象取top10。现有标签、原描述/OCR继续作为词法补充，沿用RRF合并，MCP每页返回5个候选。余下2个无成功描述的对象不伪造混合向量，仍可明确通过词法通道召回。

search_candidates的模型上下文包含visual_description={description_id,text}，默认0张图片。原始JSON也有description文本；缺描述通过missing_description_ids和description_unavailable体现。inspect_objects继续每次最多3对象、每对象1视角，需要另一视角时view=2；view_map按需返回空间图。对象ID仍限定在当前快照，导航站位不自动生成。

原始trace将label和multimodal通道分别记录，使用multimodal_match_ids和multimodal_cosine；visual_cosine不冒充混合模型分数。余弦相似度不是概率。向量服务错误直接返回调用错误，不自动改用CLIP或其他模型。

## 统一包和模型配置

混合矩阵注册为scene.json的feature_matrix资产，并以新feature_space、逐对象feature_refs绑定。metadata.multimodal_index依照[Schema](../schemas/multimodal_index.schema.json)保存每行对象、description_id、有效图片哈希/裁剪、原裁剪字节哈希、模型文件哈希、处理协议、查询模板和来源协议哈希。

SceneStore验证矩阵形状、归一化、行覆盖、模型及处理协议指纹、对象/描述/图片绑定；修改描述或图片必须重建索引。旧CLIP空间作为原始数据保留，当前配置不加载CLIP模型。[promote_mixed_index.py](../scripts/promote_mixed_index.py)可将冻结实验索引验证后导出新包，源包不改。

Python与CLI使用multimodal.load_encoder(store, config_path)，新配置示例为[encoder_mixed.example.json](../examples/encoder_mixed.example.json)。配置显式backend=http_embedding，部署URL不写入可移植地图。主线配置在 `/home/agiuser/lx_workspace/runtime/map-mixed-mainline/encoder.json`。

## 服务

- 主MCP：`http://172.22.162.253:8879/mcp`，当前单元map-query-mixed-d26003631915。
- 兼容入口：`http://172.22.162.253:8880/mcp`，当前单元map-query-mixed-fdea3414863c。
- 两者使用同一新快照、同一后端和原Bearer令牌，保持Streamable HTTP。客户端重连并刷新工具列表以获得描述优先的工具说明。
- 本机embedding：`http://127.0.0.1:8891/v1`，模型别名map-qwen3vl-embedding-2b，单元map-embedding-2f7029ea7e2a。仅本机使用，无独立认证；不作为LAN MCP入口。
- Qwen3.5仍用于离线描述生成，原8890服务已恢复。MCP每次查询不调用该VLM。

所有服务按生成的所有权身份管理。MCP指针为runtime/map-mcp/current.json和preview-current.json，后端指针为runtime/map-mixed-mainline/embedding-current.json。每次MCP替换有新unit和新日志目录，previous.json保留旧身份，旧模型、包、启动记录和trace保留。

## 验证

HTTP查询向量与冻结实验向量的余弦相似度约1.0；真实MCP初始化、工具列表、鉴权、搜索分页与按需图片均通过。8879/8880的“像打印机的设备”均首位返回S135，搜索返回0张图，inspect返回1张图。相同16条派生诊断查询在主线前5候选中全部找到指定记录；它们不是穷尽人工真值，不能写成100%语义准确率。未测完整Agent耗时。

验证记录：runtime/map-mixed-mainline/core-validation.json、canary-validation.json、live-validation.json、live-probes.json。地图25项unittest和MCP5项测试通过；Harness全量251通过，仍有2项既有reset_context描述测试失败；Ruff、mypy和前端构建通过。

后续已完成真实Sonnet经MCP的5任务测试，平均25.06秒、2至5轮；4条选择记录、1条澄清，2条不看图片。详见[MCP_AGENT_E2E_20261112.md](MCP_AGENT_E2E_20261112.md)。这补充了完整任务耗时观察，不代表冻结基线对照或语义准确率。
