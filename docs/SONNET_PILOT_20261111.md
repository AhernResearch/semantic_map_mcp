# 企业网关接入与首次真实 Agent 验证

用户指定参考本机 Claude Code 配置。读取配置中的地址和模型选择，通过已有配置凭据在进程内认证；不将凭据写入源码、参数或日志。

- 地址：`https://lingzhi.agibot.com`。
- 模型发现：`GET /v1/models` 返回32个模型ID；目录含Claude、GPT、Qwen、Kimi、GLM、DeepSeek及图像/嵌入模型。列表不包含可靠的输入模态声明，不能把“在列表里”当作“支持看图”。
- 当前实际测试：`claude-sonnet-5-5`，使用 `POST /v1/chat/completions`。
- 图片协议探针：8×8纯红PNG，作为image_url的data URI发送，返回 `Red`，finish_reason=stop。这仅证明基础图像输入链路可用。
- 脱敏发现结果保存在 `runs/sonnet_agent_20261111/endpoint_probe.json`。

## 初次运行与协议修复

原运行目录 `runs/sonnet_agent_20261111/` 完整保留。6例中4例协议失败：原生tool_calls被旧版拒绝、正文JSON格式不符合约定等。另2例生成了结构合规的结果，但不等于语义成功：NG16错误声称没有工具运行端并直接追问；NG17使用图片说明静态GS无法证明椅子当前空闲。

因此不能把2/6称为语义准确率。此轮暴露的是端点协议兼容性与模型行为问题。

修复内容：显式发送原生函数定义，支持tool_calls及每个tool_call_id对应的工具结果，保持图片真实字节传递；也保留原JSON动作协议。原生批次上限4次，按序执行，不猜测参数、不自动修复模型正文。相关原生批次/图片测试已加入现有unittest，总计13项通过。

修复后的独立重跑目录为 `runs/sonnet_agent_v2_20261111/`。每例保存请求、模型原始响应、工具结果和最终决策；批次 report.json 是执行状态依据。源标注及图片尚无独立人工真值，semantic_accuracy保持null；导航goal始终null。


## 最终决策协议

v2真实运行又观察到模型在取证后输出自然语言，未提交约定JSON，宿主正确拒绝。增加显式 finish 函数，其参数使用 grounding_decision.json；只能单独调用，仍经过对象ID、媒体引用与导航边界校验。没有添加宽泛JSON提取或自然语言猜测兜底。原生finish纳入已有13项测试中的端到端替身用例。

最终协议复测在 `runs/sonnet_agent_v3_20261111/`。所有历史目录保留。


v3在请求阶段收到HTTP400，端点明确不支持工具input_schema顶层的oneOf/allOf/anyOf。现将发送给端点的finish参数schema去掉顶层条件组合，宿主仍使用完整schema验证结果，条件检查没有放宽。失败原因保留在v3目录；使用兼容参数schema的完整复测输出到 `runs/sonnet_agent_v4_20261111/`。


## 初步语义审查注意点

- v2 的 NG02 将 S016、indoor-041、S115 作为待澄清候选，识别到源“柜子”标签与图像不一致。这是模型意见，不自动更改标签；重复实物猜测也不做合并。
- v2 的 NG03 将 S135 选为“像打印机”的对象，但同时说它可能是碎纸机或净化器。结构上 resolved 不代表设备类别、唯一实物身份或功能已核验。
- v2 的 NG11 能发现 R03/R04 重名，但把 R04 无登记对象推成“没有可用位置”，这一解释不由对象数直接支持。区域边界及坐标需单独查证。
- v4 的 NG01 输出 insufficient_evidence，却在 object_ids 中列出已排除的 S021。字段目前允许候选/讨论对象；在后续消费前，应进一步区分“支持候选”与“排除对象”，不能把非resolved状态的ID当作选定目标。

这些现象说明，结构校验与图片引用校验有必要，但不能代替对模型语义推断的审核。


## v4最终执行结果

六条均完成真实模型调用并通过宿主结构/引用校验。不是6/6语义准确；无独立人工答案，semantic_accuracy=null。所有结果goal=null，未触发导航。

| 用例 | 模型状态 | 返回讨论/候选ID | 工具调用数 | 提供的媒体数 |
|---|---|---|---:|---:|
| NG01 | insufficient_evidence | S021 | 6 | 3 |
| NG02 | ambiguous | indoor-041, S016 | 5 | 16 |
| NG03 | resolved | S135 | 5 | 10 |
| NG11 | ambiguous | 无 | 3 | 1 |
| NG16 | insufficient_evidence | S003 | 3 | 2 |
| NG17 | insufficient_evidence | S023, S034, S035 | 3 | 14 |

完整模型判断见各用例result.json，统计见execution_audit.json。13项单元测试通过。非resolved的ID不是已选目标，尤其NG01/NG16列出的是存在标签冲突的记录。

运行入口：`PYTHONPATH=map_query_tools/src HF_HUB_OFFLINE=1 envs/cg/bin/python map_query_tools/scripts/run_grounding_agent.py map_query_tools/local_data/mfloor-v1 map_query_tools/runs/mfloor_smoke/encoder.json <新输出目录> --endpoint https://lingzhi.agibot.com/v1 --model claude-sonnet-5-5`。认证从既有配置注入MAP_AGENT_API_KEY，仅存进程环境。

## 面向阅读的完整 trace

中文离线入口：`runs/trace_review_20261111/index.html`。六个用例分别成页，展示最终回答、每轮模型消息、工具参数、候选表、全部实际提供的图片、完整工具返回与原始请求/响应链接。系统prompt及工具定义可查看。

归档：`runs/trace_review_20261111.zip`（约90MiB），解压后打开index.html，无需服务器。raw目录包含四次试跑的原文件，均核对与原目录逐字节一致；manifest含329个文件SHA256。46次工具媒体引用均与实际模型请求中的图片字节匹配，ZIP CRC校验通过。

使用本机Chrome/Playwright验证了六页：共27轮、46个图片展示，无损坏图片；390px手机视口无横向溢出。截图：`runs/trace_review_desktop.png`、`runs/trace_review_mobile.png`。

可重建命令（输出目录必须新建）：

```bash
PYTHONPATH=map_query_tools/src envs/cg/bin/python map_query_tools/scripts/export_agent_trace.py \
  map_query_tools/runs/sonnet_agent_v4_20261111 map_query_tools/runs/trace-review-new \
  --history map_query_tools/runs/sonnet_agent_20261111 \
  map_query_tools/runs/sonnet_agent_v2_20261111 map_query_tools/runs/sonnet_agent_v3_20261111
```

## 本地服务与 VPS 访问

2026-11-11 启动独立用户服务 `map-query-trace-20261111-v2.service`，仅监听 `127.0.0.1:18765`，目录为 `runs/trace_review_20261111`。页面及PNG经HTTP读取与磁盘字节一致。服务是临时systemd用户单元，不保证重启自动恢复；停止命令 `systemctl --user stop map-query-trace-20261111-v2.service`。

复用现有FRP：VPS `120.25.203.202:6002` → 本机SSH `127.0.0.1:22`，未修改FRP或公开HTTP端口。VPS端SSH主机公钥已与本机公钥比对一致。本机默认客户端密钥认证被拒，未声称SSH认证后全链路已验收；外网电脑需要已有的agiuser授权密钥。

外网电脑执行（保持终端运行），然后访问 `http://127.0.0.1:18765/index.html`：

```bash
ssh -N -p 6002 -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:18765:127.0.0.1:18765 agiuser@120.25.203.202
```

密钥不在默认位置时加 `-i /path/to/authorized_private_key`。首次连接可核对ED25519指纹 `SHA256:b5iPCkQFX78JJyY358j+Pld6+xORCkt01JdjszDueRQ`。

初次HTTP服务因系统`/etc/mime.types`访问权限报错；修正为使用标准库内置MIME表，原失败保留在journal。访问验证记录在 `runs/trace_access_20261111/verification.json`。
