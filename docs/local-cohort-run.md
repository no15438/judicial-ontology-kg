# 20篇本地诊断性扩样

用户明确要求直接抽取20篇，不再以小样尚有缺口为由阻止诊断扩样。v40固定运行，已知集合表示不足、模型候选非gold及项目覆盖风险均不隐藏。不是正式语义评测或生产准入。

预登记 research/next-flash-twenty-v40-preregistration.json 记录完整输入哈希、固定20篇顺序与选择方法。两来源各10篇、每个已知family_id最多一篇，保留三篇困难来源；跨来源近重复仍未证明排除。顺序运行，每篇使用完整36篇输入按ID独立选1；总批次口径为选20、未选16，不能把每篇35未选相加。

运行器 judicial_kg.extraction.batch_workflow 在每个模型请求开始及每篇结束写原子状态，拒收继续下一篇。每篇执行原文新抽、零调用回放和接受候选无损装配；禁用旧响应复用与手改图。整个批次冻结代码、配置、契约、提示、来源与预登记文件；检测变化即停止，保留尚未执行的队列。服务繁忙最多等待5分钟后阻塞，不重启或修改服务，不切云端。异常中断不自动重跑同一目录，避免把重复尝试混成首轮。

```bash
PYTHONPATH=src .venv/bin/python -m judicial_kg.extraction.batch_workflow --manifest research/next-flash-twenty-v40-preregistration.json --output artifacts/qwen-extraction/next-flash-twenty-v40-01 --execute
```

长任务使用独立后台进程和Mac的caffeinate防止空闲睡眠；电脑、Spark与局域网需保持在线，合盖或断网仍可能中断。状态在批次目录batch-report.json，各篇document-XX中保留请求、原响应、失败、回放和候选图。仓库只提交代码、哈希和汇总，原文/模型输出/图谱不进入Git。结束后按发现、类型、绑定、语境、属性、超限、服务和装配分别统计，修复另开冻结运行，不覆盖本轮结果。
