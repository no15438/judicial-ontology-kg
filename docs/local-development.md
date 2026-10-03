# 本地开发接续

用户决定开发阶段在本机运行，Mac 通过局域网访问 Spark，不为云端额外部署收费远程连接。已有初始化脚本和研究工具不依赖云端服务。

## 在 Mac 接续

本机项目“知识图谱研究”的父目录是 `/Users/no15438/Documents/Codex/2026-10-02/new-chat-5`，它本身不是 Git 仓库。2026-10-04 实查发现 `judicial-ontology-kg` 子目录仍是 `2eb9811` 旧历史，与远端 `bec2b6a` 没有共同祖先。本次保留该目录及其忽略数据，通过 Git worktree 在 `judicial-ontology-kg-current` 接续远端，分支为 `codex/spark-data-audit`。不要把这两套历史直接合并。

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

> 阅读 AGENTS.md、CODEX_HANDOFF.md、docs/progress.md 和 docs/gold-data-audit.md。继续使用当前远端历史及本机 -current 工作树，不重新创建仓库或导入旧系统。Mac 初始化和 Spark QA 首轮盘点已完成；先读取已保存的核验结果，继续 AppealCase 严格借贷案对原文准入。不把 Spark QA 声明的人工审核和固定标注者字段当图谱 gold；不配置收费远程连接或修改 Spark 服务。

## Spark 与模型选择

`spark-lan` / `192.168.1.116` 已于 2026-10-04 实测成功，返回主机名 `spark-f74d`。使用既有 SSH 配置，启用 `BatchMode=yes`、`StrictHostKeyChecking=yes` 和 10 秒连接超时；未更改密钥或主机信任。指定数据目录的盘点记录见 [标注审计](gold-data-audit.md)；连通性是本次观察，不保证未来运行仍可达。

模型运行可继续使用 Spark 上的 Qwen/OpenJev，避免额外公网流量。模型 API 是另一个选项，不是本地开发的必要条件；用户尚未指定供应商、模型或调用预算，本次未配置 API、创建密钥或产生模型调用。

本地执行不表示 Codex 助手本身完全离线：代码、数据处理和 SSH 在 Mac 执行，Codex 对话仍使用其产品服务。

## 本次 Mac 导入故障记录

初次检查通过后，Python 曾在后续检查中报告 `ModuleNotFoundError: judicial_kg`。`python -v` 显示跳过带 macOS hidden 标记的 `.pth` 文件，而安装记录及路径正常。对项目 `.venv/lib/python3.12/site-packages/` 内 `_editable_impl_judicial_ontology_kg.pth` 和 `_virtualenv.pth` 执行 `chflags nohidden` 后，13 项测试及 doctor 均通过。标记的产生原因未确定；若复发，先检查 `ls -lO` 与 Python 启动日志，不把它误判为源码丢失或另装全局依赖。
