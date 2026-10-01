# format-extract

> 把 .docx 文档转为语义化 HTML + 提取内嵌图片（标题层级、表格样式、段落缩进、字体颜色等结构信息），供调用方做后续格式分析；也可独立评估 HTML 转换质量。适用场景（**技术性底层调用**）："docx 转 HTML"、"提取 docx 结构为 HTML"、"HTML 转换质量评估"。本 Skill 是底层格式提取工具，**不自行判断是否应被触发**、**不猜测输出位置**——是否调

## 使用说明（重要）

本技能从 WorkBuddy 导入。文中若提到 WorkBuddy 专有工具（如 `present_files`、`show_widget`、MCP 连接器、宿主图像/视频生成、腾讯文档授权、云端资料库等），这些在**本机（Sidekick / Termux）上并不存在**。遇到这类步骤，请改用本机已有能力实现，或直接跳过并说明原因。

**本文档的价值在于方法论与规范**（怎么写、怎么排版、怎么避坑）——照此执行即可，不必强行还原 WorkBuddy 的实现方式。

# format-extract — DOCX 格式提取与质量评估

<goal>
把一个 .docx 文件转为**语义化 HTML**（根元素 `<article class="docx-content">`）+ **图片资产**（`images/` 目录），让调用方能基于 HTML 分析源文档的排版风格（标题 / 表格 / 段落 / 字体 / 版面）。

**职责边界**：本 Skill 只负责"docx → HTML/images"与"HTML 质量评估"两件事。产物交付后如何使用（登记到哪个 yaml、喂给哪个下游流程、在什么阶段触发）完全由调用方决定，本 Skill 不做任何假设。
</goal>

<tools>
本 Skill 封装两项能力，统一通过本地预构建 `dist/cli.cjs` 执行，**不依赖 npm 全局安装、不联网**：

| 入口 | 用途 | 何时选用 |
|------|------|----------|
| `run.py`（一键入口） | docx → HTML，自动做门禁检查 + artifact_id 目录隔离 + 产物校验 | **默认首选**，适合 Agent 调用 |
| `node dist/cli.cjs convert` | 底层 CLI，支持单文件/批量/自定义输出路径 | 需要完全自定义路径或批量处理 |
| `node dist/cli.cjs assess` | 独立评估 HTML 质量（5 项检查） | 怀疑转换质量、需要量化报告 |

底层 CLI 参数、编程式 API、返回结构详见 [`references/cli-reference.md`](references/cli-reference.md)，日常调用无需阅读。
</tools>

---

<input-contract>

## 输入契约（调用方需提供）

本 Skill 不负责判断"是否应该触发"或"如何找到要转的 docx"——这些都是调用方的职责。调用方在确定要用本 Skill 时，提供：

| 参数 | 必填 | 说明 |
|------|------|------|
| `<docx-path>` | 是 | 要转换的 `.docx` 文件路径，**优先绝对路径**以规避 cwd 差异 |
| `--output-dir` | **是** | 自定义输出的**父目录**；脚本会在其下自动追加 `<artifact_id>/` 子目录做隔离 |

> ⚠️ **本 Skill 不再自行猜测输出位置**：调用方（如 doc-formatter）必须显式传入 `--output-dir`，通常应指向 pipeline 目录下 stage 的中间产物区（例如 `<workspace>/output/<request_id>/stage2/intermediate/format-extract/`）。不传直接报错退出（exit code 2）。这是"关注点分离"——产物落点由编排层拥有，脚本只负责转换。

> ⚠️ 为什么不在 Skill 里做"是否触发"判定：触发时机强依赖调用方的业务语义（是用户上传的模板？是批处理任务？是质量抽检？），放在本 Skill 里会与调用方耦合。**调用方自己判断"现在要不要跑这个 Skill"，跑的时候把 docx 路径和输出目录都传进来。**

</input-contract>

---

<workflow>

## 标准流程（推荐走 `run.py` 一键入口）

### 步骤 1 — 定位 `run.py`

Skill 的安装位置与当前 Agent 的 cwd 可能不一致，按优先级尝试：

| 优先级 | 策略 |
|--------|------|
| L1 | 调用方显式告知路径（最稳） |
| L2 | 相对 cwd 拼 `skills/format-extract/run.py` |
| L3 | 相对 cwd 拼历史笔误目录 `workpace/...`（兼容） |
| L4 | 从 cwd 向上最多 10 级查找包含本 Skill 的祖先目录 |

四级都找不到 → 走 `<failure-fallback>`。

### 步骤 2 — 环境前置（`run.py` 内部会自动检查，手动调 CLI 时需自检）

**P1 — Node.js ≥ 18.0.0**：跑 `node -v`，不达标则参照 [`references/node-install.md`](references/node-install.md) 安装。bundled CLI 依赖 Node 18+ 的原生 API（ESM / `structuredClone` / `fetch`），低版本直接抛 `SyntaxError`。

**P2 — bundled CLI 可用**：跑 `node <skill-dir>/dist/cli.cjs --version`，若报 `Cannot find module` / `ENOENT` 说明分发异常，走失败兜底。**不要用 `npx docx2html` 绕过**——本 Skill 已脱离 npm 全局安装，绕过会引入未知版本差异。

### 步骤 3 — 执行

```bash
# --output-dir 为**必传参数**；脚本会在其下自动追加 <artifact_id> 子目录
python3 <run.py 路径> <输入 docx 路径> --output-dir <目标父目录>
```

不传 `--output-dir` 会以 exit code 2 报错退出——本 Skill 不再猜测输出位置。

`run.py` 内部自动完成：

1. **计算 `artifact_id`**：`<docx_stem>_<md5_8>`，例如 `财报_38fb08de`
   - 同一 docx 多次处理 → 相同 artifact_id（**幂等**，复用同一目录）
   - 不同 docx → 不同 artifact_id（**永不互相覆盖**）
   - docx 内容变更 → 哈希变化，自动隔离新旧版本
2. 产物落到 `<--output-dir 值>/<artifact_id>/`（若 `--output-dir` 末尾已是 artifact_id，则原样使用不重复嵌套）：
   - `reference_format.html` — 语义化 HTML
   - `format_artifact.json` — manifest（artifact_id / 源 docx 路径与哈希 / images_dir / 时间戳）
   - `images/` — 提取的图片资产
3. P1 / P2 门禁检查
4. 调用 bundled CLI 完成转换
5. 存在性校验（输出目录 + HTML 产物 + manifest）

> 脚本会在 `--output-dir` 下自动追加 artifact_id，**调用方无需自己拼接哈希**。

### 步骤 4 — 捕获产物路径

**成功时 stdout 最后一行即 HTML 绝对路径**，调用方直接读这一行拿到结果。

```
成功示例 stdout（末行）:
<--output-dir 值>/财报_38fb08de/reference_format.html
```

同级的 `images/` 与 `format_artifact.json` 与 HTML 在同一 artifact 隔离目录下。
若要拿到 manifest 路径，从 HTML path 推导：`Path(html_path).parent / "format_artifact.json"`。

### 步骤 5 — 单次重试策略

首次调用失败（exit ≠ 0）时看 stderr 关键词：

| 错误关键词 | 应对 |
|------------|------|
| `输入文件不存在` | 转绝对路径后重试 1 次 |
| `缺少必传参数 --output-dir` / exit code 2 | 调用方 bug——补上 `--output-dir` 后重试；不再兜底猜位置 |
| 其它 | 不再重试，走失败兜底 |

> 反复重试超过一次没有意义——同类错误重跑只是在耗时间预算。

## 交付边界

本 Skill 在返回 HTML 绝对路径那一刻**职责结束**。

**调用方接手做的事**（本 Skill 不规定，仅列出常见用法供参考）：

- 读 HTML 分析排版特征（h1/h2 层级、表格样式、段落缩进、字体色……）
- 把产物路径写入自己的上下文 / yaml / 数据库
- 决定如何在下游流程中使用这些特征
- 决定 HTML 或 images 的生命周期（保留 / 清理 / 归档）

Skill 不登记任何外部字段、不修改调用方的任何文件。

</workflow>

---

<failure-fallback>

## 设计理念

格式提取失败不应阻塞调用方的主流程。本 Skill 通过**清晰的退出码 + 详细的 stderr** 让调用方自己决定如何降级——是跳过格式分析继续走、还是直接给用户报错，由调用方的业务逻辑决定，本 Skill 不越俎代庖。

## Skill 侧的契约

| 场景 | exit code | stdout | stderr |
|------|-----------|--------|--------|
| 转换成功 | 0 | 最后一行 = HTML 绝对路径 | 进度信息 |
| P1 Node 版本不达标 | 1 | — | 版本信息 + 提示升级 |
| P2 CLI 不可用 | 1 | — | `Cannot find module` 等 |
| 输入文件不存在 / 损坏 | 1 | — | `ENOENT` / 格式错误 |
| 超时 / 其它 | 1 | — | 具体错误栈 |

## 调用方侧的建议降级动作

```
if skill_exit_code != 0:
    # 记录失败原因（stderr），根据业务决定：
    #   - 可接受格式降级 → 继续主流程，标记"未提取格式"
    #   - 格式是硬需求 → 终止并反馈用户
    ...
```

## 对用户的透明化建议

如果选择"继续主流程"，建议调用方在最终响应里向用户透明标注**"本次未成功提取参考文档格式特征"**，而不是静默降级——对用户隐瞒降级比降级本身更糟糕。

</failure-fallback>

---

<restrictions>

- ❌ **不要自行实现 docx → HTML 逻辑**：必须通过 `run.py` / `cli.cjs` 统一入口，绕开会破坏产物可重现性和后续的 assess 质量检查
- ❌ **不要反复重试超过 2 次**（首次 + 策略性重试 1 次）：同类错误重跑是在耗光 Agent 的时间预算
- ❌ **不要用 `npx docx2html` 绕过 bundled CLI**：本 Skill 已完全脱离 npm 全局安装，绕过会引入未知版本差异
- ❌ **不要在本 Skill 内登记调用方的字段**（如某个 yaml、某个数据库）：产物路径返回给调用方，后续写入由调用方自己决定
- ❌ **不要在 SKILL.md 里引用任何调用方的具体阶段 / 字段 / 脚本名**：Skill 应对调用方保持无知，文档里只讨论"输入 docx / 输出 HTML"

</restrictions>

---

## 可选：独立 HTML 质量评估

怀疑转换质量（如表格错位、冗余 `mso-*` 属性）时，单独跑 assess：

```bash
node <skill-dir>/dist/cli.cjs assess <html-file> [--output <report-path>]
```

- exit code 0 = 5 项检查全过
- exit code 1 = 有未过项（**不是程序崩溃**），看报告里 `❌` 的条目定位问题

详见 [`references/cli-reference.md`](references/cli-reference.md) 的 assess 章节。

---

## 相关文档

- [`references/cli-reference.md`](references/cli-reference.md) — CLI 参数 / 编程式 API / 返回结构 / 5 项检查维度 / 反模式
- [`references/node-install.md`](references/node-install.md) — Node ≥ 18 跨平台安装脚本


---

## 附：references（详细参考）


### references/cli-reference.md

# format-extract CLI 与 API 参考

> 供 Expert 在需要自定义输入/输出、批量处理、独立质量评估时渐进加载。
> 日常"docx → HTML 格式提取"直接走 `run.py`，无需阅读本文档。

---

## CLI 统一入口

所有命令固定使用 bundled CLI（随 Skill 预构建，无需 npm 全局安装、不依赖外网）：

```
node ./dist/cli.cjs
```

> 路径以本 Skill 目录为基准，实际调用时替换为你的绝对或相对路径。

---

## convert — docx 转 HTML

### 单文件模式

```bash
node ./dist/cli.cjs convert --input <docx-path> --output <html-path> [--image-base-url <url>]
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--input <path>` | 是（与 `--input-dir` 二选一） | 输入 .docx 文件路径 |
| `--output <path>` | 否 | 输出 HTML 路径，默认同名 .html |
| `--image-base-url <url>` | 否 | 图片 src 的相对基础路径，默认 `images` |

### 批量模式

```bash
node ./dist/cli.cjs convert --input-dir <dir> --output-dir <dir> [--image-base-url <url>]
```

仅处理目录**顶层**的 .docx，不递归子目录。`--input` 与 `--input-dir` 互斥。

---

## assess — HTML 质量评估

```bash
node ./dist/cli.cjs assess <html-file> [--output <report-path>]
```

独立评估 HTML 质量，退出码 1 表示未通过检查（非崩溃）。

### 5 项检查维度

| 维度 | 通过条件 |
|------|----------|
| 结构化校验 | 根元素为 `<article class="docx-content">`，标签正确闭合 |
| 样式信噪比 | 文本内容 vs 标记比例 ≥ 50% |
| 表格还原度 | 合并单元格无还原问题 |
| 样式白名单 | 仅保留 `color` / `background-color` / `text-align` / `text-decoration` / `font-weight` / `font-style` / `max-width` |
| 降噪检查 | 无 `mso-*` 属性、无 `MsoXxx` 类名、无空 `<span>` |

> assess 输入必须是 convert 产出的 HTML；不接受原始 .docx。

---

## 编程式 API

### convertDocx

```typescript
function convertDocx(buffer: Buffer, imageBaseUrl?: string): Promise<ConvertResult>

interface ConvertResult {
  html: string;
  images: Array<{ filename: string; data: Buffer }>;
}
```

示例（CommonJS）：

```js
const { convertDocx } = require('@tencent/docs-docx2html');
const fs = require('fs');

const buffer = fs.readFileSync('document.docx');
const result = await convertDocx(buffer, './images');

fs.writeFileSync('output.html', result.html, 'utf-8');
for (const img of result.images) {
  fs.writeFileSync(`./images/${img.filename}`, img.data);
}
```

### assessHtml + generateReport

```typescript
function assessHtml(html: string): AssessResult
function generateReport(result: AssessResult): string

interface AssessResult {
  file: string;
  timestamp: string;
  overall: 'pass' | 'fail';
  checks: CheckResult[];
  summary: { total: number; passed: number; failed: number; warnings: number };
}
```

---

## 退出码与错误

| 退出码 | 含义 |
|--------|------|
| 0 | 成功 |
| 1 | 失败（文件不存在 / 格式错误 / assess 未通过 / 参数冲突） |

### 常见错误

| 错误关键词 | 处理 |
|------------|------|
| `--input and --input-dir are mutually exclusive` | 二者选其一 |
| `either --input or --input-dir is required` | 至少指定一个 |
| `ENOENT: no such file` | 检查路径拼写，文件 I/O 问题可重试 |
| 损坏的 ZIP / 非 .docx | 不重试，检查输入文件 |

---

## 反模式（避免踩坑）

- 对同一 .docx 反复 convert 而不 assess → 先跑一次 assess 看质量
- 把非 convert 产出的 HTML 交给 assess → assess 只认 `<article class="docx-content">` 结构
- `--image-base-url` 用绝对 URL → 必须相对路径（如 `./images`）
- 期望批量模式递归子目录 → 只处理顶层
- 使用 `npx docx2html` → 本 Skill 已统一 bundled CLI，不再依赖外部包


### references/node-install.md

# Node.js ≥ 18 安装脚本（环境准备兜底）

> 仅在 `node -v` 返回 `command not found` 或版本 < 18 时执行。
> 多数环境已具备 Node.js，**请优先用 `node -v` 检测后再决定是否需要本脚本**。

---

## 跨平台自动安装

```bash
OS="$(uname -s 2>/dev/null || echo Windows)"
case "$OS" in
  Linux*)
    if command -v nvm >/dev/null 2>&1; then
      nvm install --lts
    elif command -v fnm >/dev/null 2>&1; then
      fnm install --lts
    else
      curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
      source "$HOME/.nvm/nvm.sh"
      nvm install --lts
    fi
    ;;
  Darwin*)
    if command -v brew >/dev/null 2>&1 && [ "$(brew list node 2>/dev/null)" ]; then
      brew upgrade node
    elif command -v nvm >/dev/null 2>&1; then
      nvm install --lts
    elif command -v fnm >/dev/null 2>&1; then
      fnm install --lts
    else
      curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
      source "$HOME/.nvm/nvm.sh"
      nvm install --lts
    fi
    ;;
  *)
    if command -v fnm >/dev/null 2>&1; then
      fnm install --lts
    elif command -v winget >/dev/null 2>&1; then
      winget install OpenJS.NodeJS.LTS
    else
      echo "❌ 无法自动安装 Node.js，请手动安装: https://nodejs.org/"
      exit 1
    fi
    ;;
esac
```

安装完成后重新执行 `node -v` 验证版本 ≥ 18.0.0。

---

## 为什么强制 Node ≥ 18

bundled CLI 依赖 Node 18+ 的 ESM 与 `structuredClone`、`fetch` 等原生 API。版本过低会在运行时直接抛 `SyntaxError` 或 `ReferenceError`，且这些错误对 Agent 来说难以自动定位根因——因此在进入 CLI 调用前就拦截掉版本问题，能显著降低失败成本。
