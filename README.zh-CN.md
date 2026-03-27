# CodeCast

[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](#环境要求)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](#许可证)
[![UI](https://img.shields.io/badge/interface-terminal%20panel-black)](#交互界面)
[![Status](https://img.shields.io/badge/status-mvp-orange)](#路线图)

把每次 `git push` 变成可发布的开发动态。

CodeCast 会监听 push，聚合提交生成草稿，最后经人工确认后通过 `opencli` 发布到社交平台。

English: [README.md](./README.md)

## 为什么做 CodeCast

- 避免“功能做了很多，但没有及时对外同步”。
- 按 push 聚合，避免 commit 级别刷屏。
- 终端面板式交互（`codecast` 直接进入）。
- 发布前人工确认，降低误发风险。
- 支持按仓库配置。
- 通过 `opencli` 发布（可接 Twitter/X 等）。

## 30 秒体验流程

1. 正常开发并 `git push`
2. 输入 `codecast` 打开面板
3. 预览草稿、切换风格、dry-run
4. 确认后发布

## 架构

```mermaid
flowchart LR
    A["git push"] --> B["post-push hook"]
    B --> C["codecast collect"]
    C --> D["SQLite (events/commits/drafts/logs)"]
    D --> E["codecast 面板 UI"]
    E --> F["opencli twitter post"]
    F --> G["Twitter/X"]
```

## 功能特性

- push 采集并本地落库（SQLite）
- 草稿状态流转：`PENDING -> FAILED/PUBLISHED -> ARCHIVED`
- 三种文案风格：`formal` / `friendly` / `punchy`
- 多仓库发布：`merged` / `separate`
- 发布历史弹窗 + 失败重试
- 斜杠命令和面板操作并存

## 安装

### 方式 A：用户级安装（推荐）

```bash
python3 -m pip install --user /path/to/CodeCast
```

若提示 `codecast: command not found`，加入 PATH：

```bash
export PATH="$HOME/Library/Python/3.9/bin:$PATH"
```

### 方式 B：源码直接运行

```bash
cd /path/to/CodeCast
PYTHONPATH=src python3 -m codecast.cli
```

## 快速开始

首次执行一次：

```bash
codecast init
codecast config set --key publish.opencli_cmd --value "opencli twitter post"
codecast install-hook --repo /path/to/your/repo
```

然后在你的开发仓库：

```bash
git add .
git commit -m "feat: ship something"
git push
```

打开面板：

```bash
codecast
```

## 交互界面

`codecast` 默认进入 panel 模式。

### 键位说明

```text
j / k / ↑ / ↓  选择草稿
p              发布当前草稿（会弹确认框）
d              dry-run 当前草稿
x              重试当前 FAILED 草稿
h              查看当前草稿发布历史
s              切换风格（formal/friendly/punchy）
a              切换列表（pending/all）
/              输入斜杠命令
q              退出
```

## 斜杠命令

```text
/pending
/all
/view <draft_id> [style]
/post <draft_id|latest> [--dry-run]
/retry <draft_id|latest> [--dry-run]
/history <draft_id|latest> [limit]
/repos <repo_a,repo_b> <merged|separate> [--dry-run]
/config show
/config set <key> <value>
/exit
```

## CLI 命令

```bash
codecast init
codecast collect --repo /path/to/repo --oldrev <old_sha> --newrev <new_sha>
codecast drafts list --all
codecast drafts render --draft 1 --style friendly
codecast publish --draft 1 --dry-run
codecast publish --repos /repo/a,/repo/b --mode merged
codecast settings set --repo /repo/a --every-n-pushes 10 --default-style friendly
codecast install-hook --repo /repo/a
codecast ui --plain
```

## 配置项

- `publish.opencli_cmd`：发布命令（例如 `opencli twitter post`）
- `publish.every_n_pushes`：按仓库设置聚合阈值
- `publish_enabled`：按仓库发布开关
- `style.default`：按仓库默认风格

## 环境要求

- Python 3.9+
- Git
- `opencli`（真实发布需要）
- Chrome + opencli Browser Bridge 扩展（Twitter/X 这类浏览器适配器需要）

## 常见问题

### 为什么发布报错 “Extension is not connected”？

`opencli` daemon 已启动，但 Chrome 扩展未连接。  
请安装并启用扩展，然后执行 `opencli doctor` 直到显示 connected。

### 数据存在哪里？

默认：

```text
~/.codecast/codecast.db
```

可通过环境变量覆盖：

```bash
CODECAST_DB_PATH=/custom/path/codecast.db
```

### 默认会自动真发吗？

不会。MVP 默认是人工确认后才真实发布。

## 路线图

- 更丰富的文案模板和风格包
- 新手引导命令（`codecast setup`）
- 基于同一 DB 的可选 Web UI
- 可插拔发布后端

## 贡献

欢迎提 Issue 和 PR。  
如果改动交互体验，请附上：

- 改动前后行为
- 键位/流程影响
- 命令兼容性说明

详细说明见：

- [CONTRIBUTING.md](./CONTRIBUTING.md)
- [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)

## 许可证

[MIT](./LICENSE)
