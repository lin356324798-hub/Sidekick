# Sidekick

**跑在安卓平板本地的 AI agent。** 一个 Python 文件、零第三方依赖、浏览器当界面。

它不只是聊天 —— 它能**真的操作你的设备**：读写文件、跑命令、改代码、开网页、连手机、做表格、处理图片。

> `Sidekick` 只是出厂默认名，装好后可在「身份」页改成任何你喜欢的名字。

---

## 目录

- [它是什么](#它是什么)
- [安装](#安装)
- [配置 API Key](#配置-api-key)
- [日常使用](#日常使用)
- [能力总览](#能力总览)
- [工具详解（20 个）](#工具详解20-个)
- [技能库（36 个）](#技能库36-个)
- [目录结构](#目录结构)
- [安全说明](#安全说明)
- [常见问题](#常见问题)

---

## 它是什么

一个住在你手机/平板里的 AI 助手，通过浏览器界面使用。

**设计取舍**

| 原则 | 说明 |
|---|---|
| **零第三方依赖** | 只用 Python 标准库，`pkg install python` 即可运行。不碰任何需要编译的原生模块，因此永远不会因为上游升级而失修 |
| **本地存储** | 会话、记忆、密钥全在本机 `~/.termux-agent/`，不上传云端 |
| **能改自己** | 可以修改自己的源码来加功能，改坏了自动回滚 |
| **自带看门狗** | 服务挂了自动拉起，不依赖 systemd（安卓没有） |

---

## 安装

前置：安卓设备装好 [Termux](https://f-droid.org/packages/com.termux/)（**建议用 F-Droid 版**，插件必须与主程序同签名）。

```bash
# 1. 装 Python
pkg update && pkg install -y python curl

# 2. 进入本目录，一键安装
bash install.sh
```

安装脚本会依次：检查环境 → 备份已有安装 → 拷贝程序与技能 → 生成默认配置（**不覆盖已有 API Key**）→ 启动服务并自检。

---

## 配置 API Key

首次运行需要填 API Key，两种方式：

**方式一（推荐）**：打开 `http://127.0.0.1:8765/`，右上角「设置」里填。

**方式二**：命令行
```bash
python3 ~/.termux-agent/agent.py config
```

默认接 DeepSeek（`https://api.deepseek.com`），也可换成任何兼容 OpenAI 格式的接口 —— 包括跑在你局域网里的本地模型。

---

## 日常使用

```bash
bash ~/.termux-agent/start.sh              # 重启 Termux 后恢复服务
python3 ~/.termux-agent/agent.py doctor    # 环境自检 + 连通性测试
python3 ~/.termux-agent/agent.py restart   # 重启服务
python3 ~/.termux-agent/agent.py selfcheck # 自我体检（源码/看门狗/服务/备份）
```

然后在浏览器打开 **http://127.0.0.1:8765/**。

### 命令行直用（不开界面）

```bash
python3 ~/.termux-agent/agent.py "看看磁盘占用"   # 跑一次就退出
python3 ~/.termux-agent/agent.py -c               # 续聊上次会话
python3 ~/.termux-agent/agent.py chat             # 终端里对话（简陋）
```

### 开机自启（可选）

装好 [Termux:Boot](https://f-droid.org/packages/com.termux.boot/)、先手动打开一次该 App，然后：

```bash
python3 ~/.termux-agent/agent.py autostart
```

---

## 能力总览

| 分类 | 能力 |
|---|---|
| **系统操作** | 执行 shell、以 shell 权限读系统设置、安装 APK、管理进程 |
| **文件处理** | 读写改文件、批量补丁、目录浏览、全文正则检索、分页读大文件 |
| **联网** | 网页抓取转文本、免 Key 联网搜索、大文件流式下载 |
| **代码托管** | GitHub 全流程（建仓库/克隆/拉取/提交/推送/搜索代码） |
| **设备互联** | 远程操控华为手机（HDC 协议）、以 adb shell 身份操作本机 |
| **图像处理** | 擦除文字水印、老照片修复、画质增强、人像美化、抠图去背景 |
| **文档产出** | Word / Excel / PPT 生成，HTML 转 DOCX，合同模板，排版美化 |
| **任务管理** | 待办清单、子代理并行思考、关键决策征询 |
| **自我进化** | 修改自己的源码加功能，自动备份 + 语法检查 + 失败回滚 |
| **垂直技能** | 36 个领域技能文档（写作、设计、法律、金融、调试……） |

---

## 工具详解（20 个）

### 系统与文件

| 工具 | 作用 | 亮点 |
|---|---|---|
| `bash` | 执行 shell 命令 | 主力工具，可装包、管理文件、跑程序 |
| `read_file` | 读文本文件（带行号） | 大文件分页读，`offset` 传负数可从末尾看日志结尾 |
| `write_file` | 整文件写入 | 自动建父目录 |
| `edit_file` | 精确字符串替换 | 要求唯一匹配；多处改动会提示行号供定位 |
| `apply_patch` | 多处/多文件补丁 | **原子生效**：任何一处对不上就整体不改，不会改一半 |
| `list_dir` | 列目录 | 含类型、大小、修改时间 |
| `grep` | 递归正则检索 | 返回 `文件:行号: 内容` |

### 联网

| 工具 | 作用 | 注意 |
|---|---|---|
| `fetch_url` | 抓网页/API 转纯文本 | 只适合读文本 |
| `web_search` | 联网搜索 | **无需 API Key** |
| `download` | 流式下载大文件 | 不截断、带超时、自动重试，适合 APK / 安装包 |

### 设备与外部系统

| 工具 | 作用 | 说明 |
|---|---|---|
| `sysshell` | 以 shell（adb）身份执行 | 权限高于普通应用，可读系统设置、`dumpsys`、`getprop`、`pm/am`。**不是 root** |
| `hdcmate` | 远程控制华为手机 | 走 HDC 协议（非 adb）。`exec` 执行命令 / `target` 记住地址 / `test` 测连接。可做完整 UI 自动化 |
| `github` | 操作 GitHub 仓库 | `list/repo/read/tree/clone/pull/push/create/search` 九个动作，需配 Token |
| `wps` | 生成 Word/Excel/PPT | 本地 MCP 服务实现，**不需要登录账号**，文件落在 `~/storage/shared/WPS_AI/` |
| `imgedit` | 图片 AI 处理 | 五种操作：`erase` 擦文字水印 / `restore` 老照片修复 / `enhance` 画质增强 / `beauty` 人像美化 / `matting` 抠图 |

### 协作与自我进化

| 工具 | 作用 | 说明 |
|---|---|---|
| `todo_write` | 维护任务清单 | 3 步以上的任务拆成 2~6 步，实时显示在你输入框上方，中断重连后据此续做 |
| `subagent` | 子代理独立思考 | 把独立子问题丢给它单独想（不接触本机文件），适合并行推进多个难点 |
| `ask_user` | 征询你的决策 | 弹出选择面板。只在必须由你拍板时用：花钱、删数据、方案取舍 |
| `selfupdate` | **修改自己** | 自动备份 + 语法检查，失败立即还原；成功后服务自动重启 |

### 已归档

| 工具 | 说明 |
|---|---|
| `wx_auto` | 微信自动陪聊（读屏 + 自动回复，需授权且对方知情）。含三道安全闸门：识别不准只记日志、找不到发送按钮就放弃、每小时上限 20 条 |

---

## 技能库（36 个）

技能是发给 AI 的**领域方法论文档**。做相关任务前它会先读对应技能，按里面的规范和踩坑经验执行。

### 写作类（11 个）

| 技能 | 用途 |
|---|---|
| `general-writer` | **L1 通用写作兜底**。公文、周报、方案、邮件、文案、散文、新媒体，7 维质量评分 + 10 种文体适配矩阵 |
| `academic-paper-expert` | 学术论文：结构设计、文献综述、摘要、APA/GB-T7714 引用规范、学术润色 |
| `tech-blog-expert` | 技术博客：教程、架构解析、源码分析、开源文档、README |
| `business-copy-expert` | 商业文案：品牌文案、营销邮件、产品描述、Slogan、广告合规（AIDA 模型） |
| `work-report-expert` | 职场汇报：年终总结、述职报告、竞聘演讲、周报月报（金字塔原理 + STAR 法则） |
| `science-writing-expert` | 科普写作：科学解释、科技评测、深度报道（费曼学习法） |
| `poetry-prose-expert` | 诗歌散文：现代诗、古体诗词、随笔、文学评论 |
| `stock-research-report-expert` | **L2 证券研报**：行业深度、个股研究、动态点评、商业计划书，四档体量 |
| `legal-contract-expert` | **L2 法律合同**：起草与审查，必备条款完整性、权利义务对称性、高风险点防范 |
| `humanizer-zh` | 去 AI 味（中文）：基于维基百科「AI 写作特征」指南检测并修复 |
| `humanizer` | 去 AI 味（英文版） |

### 文档产出类（5 个）

| 技能 | 用途 |
|---|---|
| `doc-typeset` | **排版美化**：消费 design tokens + 内容，输出精美 HTML。内置 7 种垂类模板（合同/学术论文/公文/商务报告/会议纪要/研报/年报） |
| `html-to-docx` | HTML 高保真转 Word。支持 CSS 变量预处理、10+ 种元素精确映射、14 种 CSS 属性 |
| `format-extract` | .docx 转语义化 HTML + 提取内嵌图片（保留标题层级、表格样式、缩进、颜色） |
| `generate-fillable-contract-html` | 生成可填写的中文合同、报价单、授权委托书 HTML |
| `underline-toolkit` | 下划线文档：`create` 生成填空模板 / `fill` 对已有模板回填数据（合同、申请表、论文封面） |
| `html-review` | **HTML 质量门禁**：对排版输出做 5 维度检测（令牌合规、结构完整、排版合理、文体契合、装饰适度），不通过则打回定向修正 |

### 设计类（8 个）

| 技能 | 用途 |
|---|---|
| `design-router` | 设计任务调度器：先判断该用哪套设计族，再分发 |
| `design-token` | 按文档类型输出标准化设计令牌，驱动 doc-typeset 的所有样式决策 |
| `design-variables` | 绑定/解绑设计变量（design tokens）到节点属性 |
| `ardot-design-to-code` | 设计稿转前端代码，或从网站提取设计系统 / 样式指南 |
| `ardot-ui-design` | UI/界面设计：网页、仪表盘、落地页、移动端界面 |
| `ardot-poster` | 视觉海报：海报、传单、广告牌、Banner、活动主视觉 |
| `ardot-slides` | 演示文稿设计（不是 .pptx 文件，是设计稿） |
| `component-instance` | 组件实例管理：创建/更新实例、设置组件属性、切换变体 |
| `shared-styles` | 共享样式绑定/解绑（文字样式、填充、描边、效果） |

### 调试运维类（5 个）

| 技能 | 用途 |
|---|---|
| `termux-traps` | **安卓 Termux 环境坑**：这台设备上的各种陷阱与正确姿势 |
| `log-debug` | 日志排查指南：出问题先看哪个文件 |
| `selfupdate` | 自我修改正规流程：改 `agent.py` 的正确通道与铁律 |
| `ui-debug` | Web 界面调试：改 UI 并当场验证 |
| `pc-debug` | 通过「PC 调试桥」在那台 Windows 电脑上执行命令、读写文件 |

### 设备互联类（2 个）

| 技能 | 用途 |
|---|---|
| `hdcmate` | HDC 手机调试：协议说明、鸿蒙常用命令、UI 自动化、踩过的坑 |
| `chrome-cdp` | Chrome 调试抓取：用 DevTools 协议读网页数据，不碰屏幕 |

### 技能管理类（4 个）

| 技能 | 用途 |
|---|---|
| `find-skills` | 帮你发现和安装技能 |
| `skill-creator` | 创建新技能的指南 |
| `marketplace-skill-installer` | 从技能市场搜索安装 |
| `underline-toolkit` | 见上方「文档产出类」 |

---

## 目录结构

```
~/.termux-agent/
├── agent.py          主程序（单文件，纯标准库）
├── cdp.py            CDP 客户端（浏览器调试抓取，ui-debug / chrome-cdp 技能用）
├── chrome_read.py    Chrome 网页数据读取（chrome-cdp 技能用）
├── hdc.py            HDC 协议实现（hdcmate 工具用，447 行）
├── wps.py            WPS 文档生成
├── wps_mcp_server.py WPS 本地 MCP 服务
├── mcp_call.py       MCP 调用辅助
├── config.json       配置（含 API Key，权限 600）
├── skills/           技能文档（36 个）
├── sessions/         会话记录
├── memory.md         长期记忆
├── uploads/          上传的图片
├── outputs/          生成的成品
├── versions/         源码版本备份（自更新用）
├── logs/             日志
├── tmp/              临时文件
├── supervisor.sh     看门狗（自动生成）
└── start.sh          启动脚本
```

---

## 安全说明

- 界面**只监听 `127.0.0.1`**，不对局域网开放（界面含命令执行权限）
- `config.json`、Token 文件权限均为 `600`
- 格式化、直写块设备、删根目录等不可逆操作会被自动拦下
- 无 root 权限，也读不到其它应用的私有数据
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

**想加新能力？**
用 `skill-creator` 技能创建一个新的 `.md` 放进 `skills/`，或让 AI 用 `selfupdate` 给自己加工具。

---

## 许可

个人自用项目，随意取用。
