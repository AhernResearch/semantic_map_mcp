# 当前网关多模态低延迟候选探针（2026-11-12）

当前网关GET /v1/models返回32个模型ID，没有可靠的输入模态或延迟字段。对9个低延迟候选与Sonnet基线做了看图加原生工具调用探针；最终按当前Agent默认工具选择方式复核5个模型，各3次、串行且轮换顺序。此轮最优可直接复核候选为claude-haiku-4-5-20251001。

## 与当前Agent一致的默认工具选择方式

每次输入1张192×96合成PNG，要求识别左右背景色和黑圆所在侧，再调用report_visual。三个固定变体：红/青＋左圆，蓝/黄＋右圆，绿/品红＋左圆；期望答案不发给模型。temperature=0，max_tokens=512，stream=true。未传tool_choice，GPT显式reasoning_effort=none；其他使用供应方默认推理配置。无自动重试、无模型回退。每次HTTP请求包含新连接开销，与现有urllib调用方式一致。首有效输出是首次非空正文或工具调用delta，不是HTTP响应头。

| 模型ID | 完整响应中位数 | 首有效输出中位数 | 三次完整耗时 | 看图＋工具通过 |
|---|---:|---:|---|---:|
| claude-haiku-4-5-20251001 | 2.07秒 | 1.60秒 | 2.08, 2.07, 1.58秒 | 3/3 |
| claude-haiku-5-5 | 2.55秒 | 2.25秒 | 2.55, 3.38, 2.21秒 | 3/3 |
| qwen3.7-plus | 2.64秒 | 2.36秒 | 2.58, 2.77, 2.64秒 | 3/3 |
| claude-sonnet-5-5 | 3.18秒 | 2.59秒 | 3.18, 3.30, 2.74秒 | 3/3 |
| gpt-6.1-terra | 4.08秒 | 3.76秒 | 5.20, 1.83, 4.08秒 | 3/3 |

Haiku4.5本轮完整响应中位数较Sonnet5.5低约35%。三次通过仅证明简单颜色/位置识别与当前工具消息协议，不能推成地图目标判断准确率，也不是完整Agent时延。每模型仅3次，存在网关排队、缓存及运行时波动，不作为全网或全目录绝对最快的结论。

## 初筛与模式差异

初筛使用强制report_visual工具，9个模型并发上限3，结果只用于发现可用性并选择复核对象，不用于最终串行排名。随后对初筛最快3个候选各做3次串行强制工具复核。最后另行冻结上表5模型，全部按默认工具选择做3次串行复核，共计33次模型请求。

GPT-6.1 Terra在强制工具的三次串行复核为1.50/1.64/1.78秒，全部通过；默认工具选择复核为5.20/1.83/4.08秒。初筛最快不等于当前Agent模式最快。

GPT-6 Luna强制工具复核中2/3次颜色错误，GPT-5.6 Luna中1/3次颜色错误；保留错误结果，不将它们只按速度排在可直接使用候选前。GLM-5.3-flash初筛通过但完整响应11.44秒；GLM-5.3-flash-local返回正确颜色和工具参数，但finish_reason=stop，与当前Agent严格的原生tool_calls判断不兼容，未静默修正。Sonnet5.5强制工具模式收到HTTP400（不支持该强制tool_choice）；默认工具选择三次全部通过，因此不能说Sonnet不支持看图或工具。

## 官方说明与网关边界

[Anthropic Haiku5.5官方说明](https://www.anthropic.com/claude-haiku-5-5)将其定位为该厂商最快模型；本次本网关默认模式的Haiku4.5中位数更低。官方定位不能替代具体网关和请求方式的测量。

[OpenAI Docs GPT-6指南](https://developers.openai.com/api/docs/guides/latest-model)说明：GPT-6 Luna在Chat Completions调用函数需reasoning_effort=none；GPT-6.1 Terra原生工具调用要求Responses。本网关以custom模型ID提供Chat Completions兼容路径，本次Terra实测能返回该路径原生工具调用，但不能据此宣称官方原生Chat Completions支持，或保证复杂工具schema均可用。

[Z.AI GLM-5.3-Flash官方文档](https://docs.z.ai/guides/vlm/glm-5.3-flash)确认图像输入、Function Calling及其不可关闭的思考模式；本次没有按模型名推断多模态能力，实际均发送了PNG字节。

## 记录

运行目录：`/home/agiuser/lx_workspace/runtime/model-api-audit/20261112T092811Z/`。models.json保存目录，screen.json保存初筛，ranking.json保存强制工具复核，auto-ranking.json为与当前Agent选择方式一致的最终对照，validation.json保存检查结果。每次均保存完整请求正文、SSE事件及时间、实际工具参数和期望答案；HTTP错误另存。认证仅从既有配置在进程内使用，不保存或打印。没有发送真实地图图片、运行完整Agent或切换正式模型。
