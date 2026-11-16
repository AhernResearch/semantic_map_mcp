# sementic_map_mcp 开发约定

遵守上级AGENTS.md。本目录包含只读地图工具与独立HTTP/stdio MCP服务；具体在线部署状态以拥有者记录为准。

- 规范以 docs/DATA_CONTRACT.md、docs/TOOL_CONTRACT.md 和 schemas/ 为准，变更时同步示例与Python接口。
- 当前已实现 list_scene、search_candidates、view_map、inspect_objects。后续接口按 docs/TASK_GROUNDING.md 的真实任务需求决定，不以“必须三工具”或“必须看图”约束任务；新增接口需保持简洁并同步契约。
- MCP可显式配置deep_search：复用唯一run_agent循环，接收原始instruction及可选文本context，预置快照绑定区域卡片。区域目标须卡片证据，物体目标须已提供的描述/图片；context不提供证据资格。配置、预算、取消和错误见docs/DEEP_SEARCH.md；未配置/未部署不冒称正式服务可用。
- deep_search的think_mode为fast（默认本地Qwen3.5-9B）、middle（Sonnet）、strong（GPT-6 Sol）；服务端profiles显式配置，不自动重试或换档。复用本项目唯一Agent循环和Responses适配器，保留推理项、实际wire记录与图像字节。运行入口和验证结果见docs/DEEP_SEARCH.md。
- 源文件只读；scene.json是交换包元数据权威来源，SQLite/向量索引是可重建缓存。
- 工具实例绑定场景快照；对象ID不自动映射导航ID，不跨快照混用。
- 向量模型身份、有效标记、坐标变换和图片来源必须保留。相似度不是概率，关联不是同物真值。
- 输入包路径/数组/哈希需验证；NPY禁止allow_pickle，读取路径限定包内。不要加载来源不可信pickle。
- 使用标准库 unittest 覆盖数据契约、召回与图片输出；区分结构检查、真实模型召回和语义准确率。
- 模型、运行数据、虚拟环境和渲染缓存不进源码目录。不要安装/修改Thor服务或启动机器人动作。

## 代码简洁性（用户明确要求）

- 代码保持简洁、直接，不写冗余兜底代码，不提前搭建用不到的抽象或兼容层。
- 输入错误在边界明确拒绝；不使用宽泛异常捕获掩盖错误，不静默换模型、数据、ID或检索方式。
- 必需的参数、资产和能力校验保留；缺能力按契约明确返回，不能以“简洁”为由省略数据正确性检查。
- 同一逻辑只实现一次，优先小函数和现有依赖；新增配置必须对应实际需求。

- view_map 按用户新要求绘制候选周边空间图：淡色点云投影与对象包围盒背景，突出ID＋名称，保留方向/比例尺；细节交给inspect_objects。

## 当前主线：任务驱动（2026-11-11）

- topdown是可选证据，不是指代定位的必经步骤；用户已授权参考导航展示改进空间图。
- 目标是模糊指令到确定指代，再到有效导航目标。对象中心与导航站位分开；M-floor无核验绑定时goal=null。
- tasks中的源锚点不是标准答案，派生测试不冒充现场采集指令。模型看图意见不冒充人工或实物确认。
- 后续先验证召回、证据核验、澄清与站位交付条件，再决定最小工具扩展；不自动改源标签或部署生产服务。

- Agent 原型使用用户授权且显式配置的多模态接口；请求日志包含真实图片字节，按场景敏感数据保存。固定轮数、无自动重试或替代模型；结构测试不能当成模型验收。

## 模型上下文与图片表述（用户要求，2026-11-11）

- 模型面对的是“物体图片”。prompt、图片标题和模型工具消息不重复GS、非实时、身份未核实等防御性说明；根据图片和用户条件判断，有明确匹配就选择，只有影响选择的具体歧义才追问。
- 采集方式、来源、身份审核状态保留在场景包和原始工具trace；模型上下文由agent.model_context投影，去掉source_kind/source_refs/source_links/provenance/identity_status/limitations/uri。实际调用错误和缺图错误继续返回。
- 原始工具JSON与模型实际收到的JSON分别存为*-tool.json和*-model-context.json；不得把原始审计数据误称为已全部发给模型。

- Agent上下文按需获取：候选每页5个（page/next_page），每次最多查看3个对象、每对象1张图（view/next_view）。保留底层召回截断标记，不把当前候选池当全场景。宿主selection区分candidate_ids、excluded_ids、selected_id。原始API与trace保留完整数据。

- 检索拼图试验（用户授权）：Agent/MCP search_candidates 当前页最多5个候选各取1视角合成1张图；inspect_objects仍最多3对象、每对象1视角。复用preview.call_for_model，保留缺图和原图坐标映射；*-preview.json保存宿主派生图片的原始审计数据。拼图只为实际有图的ID提供证据，原始search接口不改。

- 完整Agent图片组织对照：宿主image_packing可为montage（默认）或single，模型提示/工具说明统一称“物体图片”。single为同一拼图格子的原像素拆分，保留实际媒体ID和局部面板坐标。计时区分一次性场景/模型加载、完整Agent任务和冻结候选单次判断；自由Agent查询分叉时，不把耗时差直接解释为图片拼接的因果收益。

- 模型工具queries要求英文，规则由agent.ENGLISH_QUERY_POLICY共享；源标签、用户指令/回答不强制英文，原始中文API保留。模型需改写查询时重置page=1；描述约束不冒充服务端语言/分页状态硬校验，真实trace须核验遵守情况。

- 当前主线（2026-11-12，用户授权）：Qwen3-VL-Embedding-2B图文混合召回，默认检索返回文本描述，图片仅按需inspect_objects/view_map。新快照内嵌描述及混合特征；每视角向量按对象取最大余弦后去重，再与词法通道RRF合并。混合模型/描述/有效图像绑定按multimodal_index.schema.json验证，不自动回退CLIP。旧无描述包的拼图行为保留作兼容路径；主线配置与服务见docs/MIXED_RECALL_MAINLINE_20261112.md。

- MCP HTTP/stdio服务、工具注册、deep_search及其模型调用属于本项目；不得运行时导入open_nav_harness或navgpt。Harness只通过MCP协议接入。
