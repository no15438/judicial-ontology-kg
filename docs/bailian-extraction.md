# 百炼小批抽取

此页保存已经结束的百炼试跑。用户随后明确要求后续使用本地模型，当前入口见 [本地 Qwen 抽取](qwen-extraction.md)；不要按历史预算自动发起新付费批次。

用户于 2026-10-04 决定暂停本地模型调参，改用阿里云百炼，明确批准：北京地域、固定模型 `qwen3.8-max-0902`、AppealCase 与 Spark 各一篇既有开发文书、本轮费用上限 5 元。原文经推理请求发送至百炼；不把整个数据集上传为文件、知识库或 Git 资源。该批准不等于后续全量抽取授权。

专用 API Key 仅允许该模型，保存在当前工作树 `.env.local`（权限 600，Git 忽略）。不输出密钥、不复用或重置其他项目密钥、不提交实际环境文件。使用控制台显示的北京业务空间专属 Base URL，而非为 Spark 配置公网入口。没有购买 Token Plan、配置新云主机或更改 Spark 服务。

## 可复跑入口

```bash
# 仅生成本地请求计划，不读取密钥、不发请求。
bash scripts/run.sh extract-bailian \
  --documents artifacts/source-development/appealcase-documents.json \
  --documents artifacts/source-development/spark-documents.json

# 只有在该批数据、模型及费用已获批准时才加 --execute。
bash scripts/run.sh extract-bailian --execute \
  --documents artifacts/source-development/appealcase-documents.json \
  --documents artifacts/source-development/spark-documents.json
```

每次使用新的 `artifacts/bailian-extraction/` 子目录，拒绝覆盖旧结果。配置 `configs/bailian-extraction.json` 限两篇、每个来源一篇、串行、无重试。环境文件仅接受 `DASHSCOPE_API_KEY` 与 `BAILIAN_BASE_URL` 两行 `NAME=value`，不执行 shell，也可用同名进程环境变量覆盖。HTTPS 目的地限定北京百炼专属域名；不跟随 HTTP 重定向。

## 提示、费用与校验

继续使用 `prompts/qwen-extraction-v3.txt` 与相同草稿和最终契约。使用 JSON mode，关闭思考和联网搜索；初轮温度采用官方非思考模式默认值 0.7；用户提出更适合抽取的配置后，项目默认已改为 0.2，不同时修改 top_p，不把低温当成正确性的保证。历史配置与每轮快照保留。只传字段名及原文，不传上游 QA 答案或标签。六类节点与证据逐字回放、语境检查仍在本地完成；程序生成主体的反向提及索引，不修补语义错误或截断的 JSON。

2026-10-04 官方北京单价为输入 12 元/百万 tokens、输出 36 元/百万 tokens。每次按最多 120,000 输入 tokens、16,384 输出 tokens 预留 2.029824 元，两次预留 4.059648 元；已用 reservation 不因失败或超时释放。本地先要求所有消息 UTF-8 字节总数加 4,096 模板余量不超过输入预留；这是一项保守长度检查，不冒充服务端 tokenizer 实测。未通过长度或预算检查时不提交。

报告依据服务端 usage 按原价计算估算费用，不假定缓存或免费额度优惠；这不是阿里云结算账单。缺 usage、传输错误、HTTP 错误或服务端超出预留时停止后续提交。客户端限额只约束本轮运行，并非阿里云账号级硬封顶；其他项目调用和另行发起的新批次不包含在内。

完整原始响应、本地请求、候选及逐篇账本放在忽略目录；可提交的报告只有计数、哈希、版本、耗时和费用估算。全部 36 篇仍保留在分母，34 篇未选中的文书不被抹去。结构通过不等于语义正确；输出是模型候选，不是 gold。没有独立适用标注时，图谱语义 P/R/F1 保持缺失，不要求用户提供法律专家标注。

官方依据：[模型与价格](https://help.aliyun.com/zh/model-studio/qwen3-8-max)、[兼容接口与温度](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)、[JSON 输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)。

## 本机 TLS 与恢复试跑

首轮 pilot-v1 在 Python 缺少默认 CA 文件时发生传输失败，后续一篇未提交。只读无凭据诊断确认 `SSLCertVerificationError`；加载 macOS `/etc/ssl/cert.pem` 后，HTTPS 请求正常返回未认证 401。客户端仅在 macOS 缺少默认 CA 且没有显式 `SSL_CERT_FILE` 时增加系统 CA，证书验证保持开启。

不覆盖原失败记录，也不自动重试。人工检查后使用 `configs/bailian-recovery.json` 发起同样两篇的恢复试跑：第一次失败调用的 2.029824 元预留仍计入本轮，剩余限额为 2.970176 元；恢复配置将输入预留收紧为 32,768 tokens（两篇的 UTF-8 检查均在范围内），每篇预留 0.98304 元，两篇共 1.96608 元。合并预留 3.995904 元，仍在用户本轮 5 元内。此处是长度和费用界限，不是已撤回的 8K/16K 测速实验。

## 用户指定的低温配置

两篇云端 0.7 试跑均完整返回，合计 usage 原价估算 0.357804 元，但分别因 quote_ambiguous 与 evidence_outside_context 未通过最终校验。用户随后要求更合适的采样配置。采用 `configs/bailian-low-temperature.json`，温度 0.2，模型、输入和 v3 提示保持相同，不把低温直接等同于正确。

原轮次的预留仍保留，剩余本轮限额 1.004096 元。低温轮输入预留 24,576、输出上限 5,632，覆盖先前两篇的 24,059/16,837 字节检查值和实际 4,507/2,624 输出 tokens；两篇预留 0.995328 元，累计 4.991232 元。输出上限相较前轮也改变，因此不是纯温度因果实验；超过上限仍如实记录截断，不补造内容。没有扩大来源范围，没有恢复任何性能基准测试。

实跑结果：低温轮两篇均未通过引用/语境校验；四次完整响应合计估算 0.733860 元，接受整篇候选为 0。本轮已停止推理，不能用接口连通和 JSON 完整替代抽取成功。固定版本、逐篇错误码和哈希见 [机器报告](../research/bailian-extraction-report.json)。
