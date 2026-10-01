# Sidekick

跑在安卓平板本地的轻量 AI agent。一个 Python 文件、零第三方依赖，用浏览器当界面。

> 名字只是出厂默认值，装好后可以在「身份」页改成任何你喜欢的名字。

---

## 它是什么

一个住在你手机/平板里的 AI 助手。它能真的操作你的设备 —— 读文件、改代码、跑命令、开网页、连手机、做表格 —— 而不只是聊天。

- **零依赖**：只用 Python 标准库，不需要编译任何东西，永远不会因为上游升级而失修
- **本地存储**：所有会话、记忆、密钥都在本机 `~/.termux-agent/`，不上传
- **能改自己**：可以修改自己的源码加功能，改坏了自动回滚
- **离线可用的看门狗**：服务挂了自动拉起，不需要 systemd

---

## 安装

前置：安卓设备装好 [Termux](https://f-droid.org/packages/com.termux/)（**建议用 F-Droid 版**，插件必须与主程序同签名）。

```bash
# 1. 装 Python
pkg update && pkg install -y python curl

# 2. 进入本目录，一键安装
bash install.sh
```

安装脚本会：
1. 检查环境
2. 备份已有安装（如果有）
3. 拷贝程序 + 36 个技能文档
4. 生成默认配置（**不会覆盖你已有的 API Key**）
5. 启动服务并自检

---

## 配置 API Key

首次运行需要填 API Key，两种方式：

**方式一（推荐）**：打开界面 `http://127.0.0.1:8765/`，右上角「设置」里填。

**方式二**：命令行
```bash
python3 ~/.termux-agent/agent.py config
```

默认接 DeepSeek（`https://api.deepseek.com`），也可以换成任何兼容 OpenAI 格式的接口 —— 包括跑在你局域网里的本地模型。

---

## 日常使用

```bash
bash ~/.termux-agent/start.sh          # 重启 Termux 后恢复服务
python3 ~/.termux-agent/agent.py doctor    # 环境自检 + 连通性测试
python3 ~/.termux-agent/agent.py restart   # 重启服务
python3 ~/.termux-agent/agent.py selfcheck # 自我体检
```

然后在浏览器打开 **http://127.0.0.1:8765/** 使用。

### 开机自启（可选）

装好 [Termux:Boot](https://f-droid.org/packages/com.termux.boot/)，先手动打开一次该 App，然后：

```bash
python3 ~/.termux-agent/agent.py autostart
```

---

## 能力一览

| 工具 | 作用 |
|---|---|
| `bash` | 执行 shell 命令 |
| `read_file` / `write_file` / `edit_file` | 读写改文件 |
| `list_dir` / `grep` | 列目录、全文检索 |
| `fetch_url` / `web_search` | 抓网页、联网搜索 |
| `download` | 流式下载大文件 |
| `github` | 操作 GitHub 仓库（需配 Token） |
| `sysshell` | 以 shell 权限执行（读系统设置、dumpsys 等） |
| `hdcmate` | 远程控制华为手机（HDC 协议） |
| `imgedit` | 图片处理（擦除/修复/增强/美化/抠图） |
| `wps` | 生成 Word / Excel / PPT |
| `selfupdate` | 修改自己的源码 |

技能文档在 `~/.termux-agent/skills/`，共 36 个，覆盖写作、设计、合同、法律、排版、手机调试等领域。AI 做相关任务前会先读对应技能。

---

## 目录结构

```
~/.termux-agent/
├── agent.py          主程序（单文件，纯标准库）
├── config.json       配置（含 API Key，权限 600）
├── skills/           技能文档
├── sessions/         会话记录
├── memory.md         长期记忆
├── uploads/          上传的图片
├── outputs/          生成的成品
├── versions/         源码版本备份（自更新用）
├── logs/             日志
├── supervisor.sh     看门狗（自动生成）
└── start.sh          启动脚本
```

---

## 安全说明

- 界面**只监听 `127.0.0.1`**，不对局域网开放（界面含命令执行权限）
- `config.json`、Token 文件权限均为 `600`
- 格式化、直写块设备、删根目录等不可逆操作会被自动拦下
- 环境自检：`python3 ~/.termux-agent/agent.py doctor`

---

## 常见问题

**服务起不来？**
```bash
python3 ~/.termux-agent/agent.py selfcheck   # 看它自己怎么说
tail -30 ~/.termux-agent/supervisor.log      # 看看门狗日志
```

**改了代码不生效？**
新代码要先被浏览器加载一次才生效，刷新一下页面。

**想看它踩过哪些坑？**
`skills/termux-traps.md` 记录了这台设备上的各种环境陷阱。

---

## 许可

个人自用项目，随意取用。
