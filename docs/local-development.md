# 本地开发接续

用户决定开发阶段在本机运行，Mac 通过局域网访问 Spark，不为云端额外部署收费远程连接。已有初始化脚本和研究工具不依赖云端服务。

## 在 Mac 接续

本机项目“知识图谱研究”的父目录是 `/Users/no15438/Documents/Codex/2026-10-02/new-chat-5`，它本身不是 Git 仓库。2026-10-04 实查发现 `judicial-ontology-kg` 子目录仍是 `2eb9811` 旧历史，与远端 `bec2b6a` 没有共同祖先。本次保留该目录及其忽略数据，通过 Git worktree 在 `judicial-ontology-kg-current` 接续远端，分支为 `codex/spark-data-audit`。不要把这两套历史直接合并。

来源适配开发使用同一工作树中的 `codex/source-adapters` 分支；本体契约继续在其 `73a418b` 上建立的 `codex/ontology-contracts`；当前抽取开发在 `codex/qwen-extraction`。运行入口为 `bash scripts/run.sh`，会显式使用当前 checkout 的源码，避免本机 editable `.pth` hidden 标记影响导入。

本机接续执行：

```bash
cd /Users/no15438/Documents/Codex/2026-10-02/new-chat-5/judicial-ontology-kg-current
git status --short --branch
bash scripts/setup.sh
bash scripts/check.sh
```

脚本使用项目自己的虚拟环境，uv 自动选择或下载 Python 3.12；不会把现有系统 Python 改成另一版本。依赖安装需要正常网络和 GitHub 私有仓库访问权限，案件语料与模型不自动下载。

在 Codex 中以 `judicial-ontology-kg-current` 作为本地仓库目录接续。本次已实际执行初始化与检查，使用项目虚拟环境 Python 3.12.2、uv 0.12.19；系统解释器没有被替换。旧目录包含当前工作树共用的 Git 元数据，应保留，不要直接删除。

接续提示：

> 阅读 AGENTS.md、CODEX_HANDOFF.md、docs/progress.md、docs/source-development.md 和 docs/ontology-contract.md。继续使用本机 -current 工作树和 codex/qwen-extraction 分支，不重新创建仓库或导入旧系统。两类来源适配、开发小样、最小本体与抽取契约已实现，接续百炼小批抽取与保留语境的图谱装配。用户无法提供法律人工 gold，不以用户补标为前提；固定审核字段不能当 gold。不配置收费远程连接，不上传案件正文，不修改 Spark 服务。8K/16K 测速请求已撤回。

## Spark 与模型选择

`spark-lan` / `192.168.1.116` 已于 2026-10-04 实测成功，返回主机名 `spark-f74d`。使用既有 SSH 配置，启用 `BatchMode=yes`、`StrictHostKeyChecking=yes` 和 10 秒连接超时；未更改密钥或主机信任。指定数据目录的盘点记录见 [标注审计](gold-data-audit.md)；连通性是本次观察，不保证未来运行仍可达。

2026-10-04 用户决定优先使用阿里云百炼，暂停本地 Qwen 调参。专用密钥及北京业务空间 Base URL 位于当前工作树被忽略的 .env.local（权限 600），不得读出或提交。已批准固定 qwen3.8-max-0902 和两篇开发文书，首轮累计限额 5 元；后续扩批另定。见 [百炼抽取](bailian-extraction.md)。原始数据和 QA 仍留本机/Spark，仅已批准文书通过推理请求发送到百炼，不上传整个数据集或 GitHub。

本地执行不表示 Codex 助手本身完全离线：代码、数据处理和 SSH 在 Mac 执行，Codex 对话仍使用其产品服务。

## 本次 Mac 导入故障记录

初次检查通过后，Python 曾在后续检查中报告 `ModuleNotFoundError: judicial_kg`。`python -v` 显示跳过带 macOS hidden 标记的 `.pth` 文件，而安装记录及路径正常。对项目 `.venv/lib/python3.12/site-packages/` 内 `_editable_impl_judicial_ontology_kg.pth` 和 `_virtualenv.pth` 执行 `chflags nohidden` 后，13 项测试及 doctor 均通过。标记的产生原因未确定；若复发，先检查 `ls -lO` 与 Python 启动日志，不把它误判为源码丢失或另装全局依赖。

该问题后来复发；新增 `scripts/run.sh` 和检查脚本显式设置本次进程的 `PYTHONPATH` 到当前仓库 `src/`，不改全局环境。此入口下 41 项测试、doctor 和真实来源准备均已通过，不需要反复依靠清除隐藏标记。
