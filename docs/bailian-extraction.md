# 百炼小批抽取

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

继续使用 `prompts/qwen-extraction-v3.txt` 与相同草稿和最终契约。使用 JSON mode，关闭思考和联网搜索；温度采用官方非思考模式默认值 0.7，不把这次换服务商当成温度对照实验。只传字段名及原文，不传上游 QA 答案或标签。六类节点与证据逐字回放、语境检查仍在本地完成；程序生成主体的反向提及索引，不修补语义错误或截断的 JSON。

2026-10-04 官方北京单价为输入 12 元/百万 tokens、输出 36 元/百万 tokens。每次按最多 120,000 输入 tokens、16,384 输出 tokens 预留 2.029824 元，两次预留 4.059648 元；已用 reservation 不因失败或超时释放。本地先要求所有消息 UTF-8 字节总数加 4,096 模板余量不超过输入预留；这是一项保守长度检查，不冒充服务端 tokenizer 实测。未通过长度或预算检查时不提交。

报告依据服务端 usage 按原价计算估算费用，不假定缓存或免费额度优惠；这不是阿里云结算账单。缺 usage、传输错误、HTTP 错误或服务端超出预留时停止后续提交。客户端限额只约束本轮运行，并非阿里云账号级硬封顶；其他项目调用和另行发起的新批次不包含在内。

完整原始响应、本地请求、候选及逐篇账本放在忽略目录；可提交的报告只有计数、哈希、版本、耗时和费用估算。全部 36 篇仍保留在分母，34 篇未选中的文书不被抹去。结构通过不等于语义正确；输出是模型候选，不是 gold。没有独立适用标注时，图谱语义 P/R/F1 保持缺失，不要求用户提供法律专家标注。

官方依据：[模型与价格](https://help.aliyun.com/zh/model-studio/qwen3-8-max)、[兼容接口与温度](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)、[JSON 输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)。
