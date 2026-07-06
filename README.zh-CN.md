# Long Novel Agent Kit

给本地桌面智能体使用的长篇小说连续性工具包。

它不是写作模型，也不是稿匣主应用。它负责把长篇小说的长期状态保存到本地 `.novel-agent/`，让 Codex、Claude Desktop、Cursor 等本地桌面 agent 可以在不同会话、不同机器、不同 agent 之间接力写同一部小说。

## 它解决什么问题

长篇小说最容易出问题的地方不是单章文字，而是连续性：

- 聊天窗口一长，前文事实被遗忘。
- 换一个 agent 后，不知道上一位 agent 留下了什么决定。
- 资料、考据、旧稿摘要用完就丢。
- 后段设定提前泄露到前几章。
- 写完章节后没有人物状态、伏笔、剧情债务和下一章交接。

这个工具包把这些内容放进项目目录里的 `.novel-agent/`，让本地桌面 agent 每次开写前先读取章节上下文，写完后再按作者确认更新长期状态。

## 核心能力

- 章节开工前生成 `prepare-session` 和 `build-context`。
- 保存资料摘要、联网考据、冲突选择、事实台账、人物状态和剧情债务。
- 用 `check-chapter` 检查必写项、禁写项、未来标记、事实冲突、人物状态和章节合同。
- 用 proposal 流程审阅写后更新，避免 agent 静默改长期设定。
- 用 handoff 报告让下一位 agent 接手。
- 给普通用户生成本地桌面 agent 资料包。
- 生成免 Python 本地交接包，目标电脑只需要解压运行入口脚本。

## 不做什么

- 不内置大模型。
- 不内置 embedding 检索。
- 不做 PDF/OCR/网页解析。
- 不上传稿件。
- 本地桌面使用不需要服务器。

这些能力由用户正在使用的桌面智能体提供，本工具包只负责长期状态、章节边界和连续性检查。

## 三分钟试用

初始化一个小说项目：

```bash
python cli.py init ./my-novel --title "我的长篇小说"
```

开写第 1 章前生成上下文：

```bash
python cli.py prepare-session ./my-novel --chapter 1 --platform codex --mode read-only --format markdown
```

检查一章草稿：

```bash
python cli.py check-chapter ./my-novel --chapter 1 --file chapters/001.md --format markdown
```

启动只读 MCP：

```bash
python server.py --read-only --tool-profile core
```

生成本地桌面接入说明：

```bash
python cli.py desktop-setup ./my-novel --platform codex --mode read-only --format markdown
```

## 给普通用户的免 Python 包

在构建电脑上生成运行时：

```bash
python cli.py standalone-build \
  --output-dir release/long-novel-agent-runtime-macos-arm64 \
  --target-os macos \
  --apply \
  --force \
  --format json
```

生成交接包：

```bash
release/long-novel-agent-runtime-macos-arm64/long-novel-agent desktop-handoff-bundle ./my-novel \
  --platform codex \
  --mode read-only \
  --chapter 1 \
  --runtime-dir release/long-novel-agent-runtime-macos-arm64 \
  --output-dir release/my-novel-agent-bundle \
  --archive \
  --force \
  --format json
```

目标电脑只需要：

1. 解压 zip。
2. 运行顶层 `START_HERE.command`、`START_HERE.sh`、`START_HERE.ps1` 或 `START_HERE.cmd`。
3. 使用 `mcp-configs/current/` 里的当前路径 MCP 配置。
4. 把 `agent-read-me-first.md` 交给桌面 agent。

## 写入安全

默认 MCP 是只读模式。写入长期状态时有几道限制：

- 需要作者确认。
- 写前检查项目 ID、状态 hash 和章节上下文 hash。
- 高风险 proposal 需要作者审阅。
- 应用 proposal 前会创建状态快照。
- `.write.lock` 防止多个 agent 同时写 `.novel-agent/`。

## 对抗性核验

完整固定验证：

```bash
python scripts/verify_agent_kit.py
```

发布前更严格的核验：

```bash
python scripts/adversarial_release_check.py
```

发布核验会检查源码污染、JSON/schema、Python 语法、本地路径泄漏、完整连续性回归、接力样例、proposal 守卫、桌面资料包、MCP 只读边界和写入确认。

## 开源协议

MIT。见 [LICENSE](LICENSE)。
