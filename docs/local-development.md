# 本地开发接续

用户决定开发阶段在本机运行，Mac 通过局域网访问 Spark，不为云端额外部署收费远程连接。已有初始化脚本和研究工具不依赖云端服务。

## 在 Mac 接续

Codex 检测到本机已有项目“知识图谱研究”，路径为 `/Users/no15438/Documents/Codex/2026-10-02/new-chat-5`。该父目录本身不是 Git 仓库；新仓库应单独克隆，不能覆盖其内旧的 judicial-graphrag。

在 Mac 终端执行（若目标目录已有新仓库，改为进入该目录并先检查 Git 状态）：

```bash
git clone https://github.com/no15438/judicial-ontology-kg.git /Users/no15438/Documents/Codex/2026-10-02/new-chat-5/judicial-ontology-kg
cd /Users/no15438/Documents/Codex/2026-10-02/new-chat-5/judicial-ontology-kg
bash scripts/setup.sh
bash scripts/check.sh
```

脚本使用项目自己的虚拟环境，uv 自动选择或下载 Python 3.12；不会把现有系统 Python 改成另一版本。依赖安装需要正常网络和 GitHub 私有仓库访问权限，案件语料与模型不自动下载。

在 Codex 中以这个新仓库目录作为本地项目打开，并从本地执行的聊天接续。当前云端聊天没有可用的主机切换工具，不能声称这些 Mac 命令已经执行。

接续提示：

> 阅读 AGENTS.md、CODEX_HANDOFF.md 和 docs/gold-data-audit.md。用户已决定本地开发，不再配置收费的云端到 Spark 远程访问。先验证本地环境，再检查已有 spark-lan SSH 配置，通过局域网只读核验 Spark 上 /work/dataset_shenbao/legal_qa_expanded_v1_1_b02/ 的来源和标注资料。先读取文件清单、README、schema 与人工复核记录，不运行旧生成脚本、不改动服务。已有 QA 人工复核不能自动当完整图谱 gold。

## Spark 与模型选择

`spark-lan` / `192.168.1.116` 来自历史档案，尚未本次实测。本机代理可使用用户已有的 SSH 配置，先确认主机身份与非交互连接，再读取资料；不要求用户在聊天粘贴私钥。

模型运行可继续使用 Spark 上的 Qwen/OpenJev，避免额外公网流量。模型 API 是另一个选项，不是本地开发的必要条件；用户尚未指定供应商、模型或调用预算，本次未配置 API、创建密钥或产生模型调用。

本地执行不表示 Codex 助手本身完全离线：代码、数据处理和 SSH 在 Mac 执行，Codex 对话仍使用其产品服务。
