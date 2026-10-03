# 云端开发环境

本次核验日期：2026-10-04（Australia/Sydney）。当前 Codex 云端实例已经运行，工作目录 `/workspace/judicial-ontology-kg`，origin 指向新仓库。复用了用户已建立的仓库和已挂载环境。

## 可重复初始化

环境要求：Linux、Git、Python 3.12（含 venv）。当前实测 Python 3.12.14、uv 0.12.19、Git 2.52.0。

```bash
bash scripts/setup.sh
bash scripts/check.sh
```

`setup.sh` 固定 uv 0.12.19，存在相同版本时复用；否则仅在项目 `.bootstrap/` 安装。使用 `uv sync --frozen` 安装锁定依赖到 `.venv/`，创建忽略目录并执行离线诊断。开发依赖为 pytest 和 Ruff；当前业务代码仅用标准库。

云端环境的 Setup command 可填写 `bash scripts/setup.sh`，检查命令为 `bash scripts/check.sh`。本次已在当前实例实际运行脚本；没有调用平台配置写入接口，也不声称这些命令已保存到平台环境模板。仓库中的脚本是可提交、可复用的配置。

另提供 `.devcontainer/devcontainer.json` 供兼容开发容器的工具使用；没有在本次启动该容器，基础镜像标签不是不可变镜像摘要。`.github/workflows/check.yml` 在 GitHub push/PR 时使用相同脚本；本地检查不能替代远端 Actions 的实际运行结果。

## 数据与依赖边界

- `research/registries/`：两份 Drive 历史登记原字节，共 13 + 7 项；保留两个版本，不伪装为重新审计后的统一准入表。
- `research/legacy/`：固定旧仓库参考，来源见 `research/sources.json`。
- `data/raw/`：后续从授权官方入口获取的不可变原始文件，当前未下载案件语料。
- `data/derived/`：带处理版本与偏移映射的派生数据。
- `artifacts/`：运行输出、统计与日志；`local/`：个人连接设置等。

初始化不自动下载语料、安装上游训练仓库或调用模型。完整性命令校验导入的 9 份参考文件，不对上游语料的 SHA-256、原文一致性或标注质量作新认证。

## 网络及 Spark

当前运行时策略：restricted，package_managers 预设已 enforced；GitHub 克隆和 PyPI 依赖获取已实际成功。没有已配置的 secrets、runtime variables、outbound identities 或 VPN，TCP grant 列表为空。保留继承的 HTTP(S) 代理和系统 CA，不修改策略快照来尝试授予访问。

Spark 历史档案记录主机 `spark-f74d` / `192.168.1.116`，SSH 别名 `spark-lan`。当前云端没有 `.ssh/` 配置、SSH agent 或 Tailscale 客户端。用户确认目前只有局域网，要求先检查旧仓库；随后表示正在设置远程访问，稍后提供信息。本次尚未连接 Spark。

历史模型地址 `127.0.0.1:8003`（Qwen）与 `127.0.0.1:3000/v1/answer`（OpenJev）属于 Spark，不能当作云端本机地址。将来需先建立受支持的 VPN/SSH 网络与安全凭据，再只读核验模型协议、版本及 `/work/dataset_shenbao/legal_qa_expanded_v1_1_b02/` 的清单、来源、人工标注记录；目录名中的 expanded 不能证明数据真实或合成。

目前环境足够开展来源适配、契约、测试及离线评测代码开发；模型实验和 Spark 数据内容核验仍待连接条件就绪。

## 本次验证结果

`setup.sh` 实际安装成功；`check.sh` 的 Ruff、格式检查、7 项来源完整性测试、离线 doctor 与 Git 空白检查全部通过。两个 Drive ZIP 的清单成员全部匹配，9 份导入参考再次核验通过。Shell 语法、开发容器 JSON 与锁文件结构检查通过。

这些结果只验证开发环境和资料接续，不代表完整语料审计、远端 Actions、开发容器启动或 Spark 连通性已通过。
