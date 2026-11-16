# MCP服务边界迁移

服务端semantic_map.py/semantic_map_http.py、服务端合同测试迁入sementic_map_mcp。MCP registry/transport与Responses wire适配由本项目独立持有，deep_search不再导入Harness。复制部分来自工作区open-nav-harness，许可证及NOTICE保留在licenses/；相关文件已修改为地图项目用途。Harness保留通用MCP客户端、模型工具调用与展示，不加载本地地图。

新入口：`PYTHONPATH=sementic_map_mcp/src envs/map-mcp/bin/python -m sementic_map_mcp.semantic_map_http --help`。命令行参数不变；内部会话配置键从NAVGPT_MAP_*改为MAP_*。客户端原HTTP URL、Bearer、工具名与内容协议保持不变。服务端运行不需Harness PYTHONPATH。

本次先迁移源码；现有运行进程没有被重启，旧进程下一次启动必须换新模块入口。未改动令牌、地图快照或模型服务。


## 启动

安装本项目requirements.txt，设置本项目src即可启动，Python 3.11+。输出目录必须是新目录；配置和令牌使用现有外部文件。

```bash
export PYTHONPATH=/absolute/path/to/sementic_map_mcp/src
python -m sementic_map_mcp.semantic_map_http \
  --scene /absolute/path/to/snapshot \
  --encoder-config /absolute/path/to/encoder.json \
  --deep-search-config /absolute/path/to/deep-search-config.json \
  --output /absolute/path/to/new-trace \
  --host 127.0.0.1 --port 8882 --token-file /private/path/to/token
```

不提供deep-search-config时只注册四个底层工具。stdio使用`python -m sementic_map_mcp.semantic_map --mcp`，传同样的scene/output/encoder-config/deep-search-config参数，不需要host/port/token-file。

## 验证

从工作区执行地图测试（PYTHONPATH不包含Harness）：

```bash
PYTHONPATH=sementic_map_mcp/src envs/map-mcp/bin/python -m unittest discover -s sementic_map_mcp/tests -q
```

46项通过，覆盖三档deep_search、Responses适配、超时/取消、MCP schema、PNG、分页、HTTP鉴权和Host检查；使用合成场景和模拟模型，不代表真实模型语义质量或机器人导航验收。

Harness在既有envs/open-nav-harness环境以`UV_PROJECT_ENVIRONMENT=../envs/open-nav-harness ../envs/open-nav-harness/bin/uv run --no-sync`运行pytest、Ruff和mypy，避免重建环境：251项通过、2项既有reset_context说明断言失败（test_execution_surface_and_reset_ablation[False]、test_lightnav_task_instruction_has_no_map_caption_and_no_manual_reset_tool）。Ruff两项检查、mypy和`npm --prefix frontend run build`通过。此处未修复无关导航说明。

Harness的通用客户端和模型provider仍保留其自身Responses适配；地图deep_search使用本项目responses.py，两者没有运行时导入。Harness prompt不再写死map__deep_search及档位策略，由MCP工具描述公布调用契约。

本次完整测试日志：`/home/agiuser/lx_workspace/sementic_map_mcp/runs/mcp-migration-20261116T161505`。
