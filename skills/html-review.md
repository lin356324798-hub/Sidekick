# html-review

> HTML 质量门禁 skill（路线 A + 路线 B 共享）。对 doc-typeset（路线 A）或 doc-edit（路线 B）输出的 HTML 进行 5 维度质量检测（design-token 合规性、结构完整性、排版合理性、文体契合度、装饰使用合理性），输出结构化检测报告，不通过则打回上游做一次定向修正（不循环）。 在 doc-formatter 路线 A 和路线 B 流水线中，每次 H

## 使用说明（重要）

本技能从 WorkBuddy 导入。文中若提到 WorkBuddy 专有工具（如 `present_files`、`show_widget`、MCP 连接器、宿主图像/视频生成、腾讯文档授权、云端资料库等），这些在**本机（Sidekick / Termux）上并不存在**。遇到这类步骤，请改用本机已有能力实现，或直接跳过并说明原因。

**本文档的价值在于方法论与规范**（怎么写、怎么排版、怎么避坑）——照此执行即可，不必强行还原 WorkBuddy 的实现方式。

# html-review Skill

## 何时使用本 Skill

在 `doc-formatter` 的**路线 A** 和**路线 B** 流水线中，每次 HTML 输出后调用本 skill 执行质量门禁：
- **路线 A**：每次 `doc-typeset` 输出 HTML 后调用
- **路线 B**：每次 `doc-edit` 输出 HTML 后调用

若检测不通过，将修正反馈返回对应 skill（路线 A → `doc-typeset`；路线 B → `doc-edit`）做**一次**定向修正，修正后直接输出，不再复检、不循环。

## 职责

执行 6 维度 HTML 质量门禁检测，输出结构化 `HtmlReviewReport`。安全性维度（SC-01~SC-06）为一票否决维度；其余 5 维度参与综合分计算。检测不通过时，生成可操作的修正建议返回给上游 skill。

## 执行方式（脚本化，0 次 LLM 往返）

6 个维度的检测项全部为确定性规则（正则匹配、标签计数、结构解析、class/属性存在性判断），**已由 `scripts/review_html.py` 完整实现**（纯 Python 3 标准库，零第三方依赖）。本 skill **直接运行脚本并解析其 JSON 输出**作为 `HtmlReviewReport`，**无需读取 `references/` 逐条人工检测，无需任何 LLM 推理往返**。

```bash
python3 scripts/review_html.py --html <html_path> --genre <genre>
# 或从 stdin 读取：
cat <html_path> | python3 scripts/review_html.py --stdin --genre <genre>
```

- stdout 输出即为完整 `HtmlReviewReport`（JSON）。
- 退出码：`0` = 通过（`passed=true`）；`1` = 不通过（`passed=false`）；`2` = 运行错误（输入缺失/为空）。
- 脚本失败（退出码 2 或异常）→ 视为 `review_skill_failed`，由 doc-formatter 输出当前最佳 HTML（见 doc-formatter §7）。

`references/` 目录下的规则文件是脚本实现的**规则来源与说明**，仅供维护脚本时对照，**运行时无需加载**。

## 输入契约

```typescript
interface HtmlReviewInput {
  html: string;           // doc-typeset 输出的完整 HTML 字符串
  design_tokens: object;  // 与排版时相同的 design token 对象
  genre: string;          // 文档类型（government-doc / legal-contract / academic-paper 等）
}
```

## 输出契约

```typescript
interface HtmlReviewReport {
  passed: boolean;          // 整体是否通过（score ≥ 80 且所有维度 passed）
  score: number;            // 综合质量分 0-100（安全性维度不参与计算）
  dimensions: {
    design_token_compliance: DimensionResult;
    structural_integrity:    DimensionResult;
    typographic_quality:     DimensionResult;
    genre_fit:               DimensionResult;
    decoration_usage:        DimensionResult;
    security:                DimensionResult;  // 一票否决维度
  };
  actionable_feedback: string[];  // 可操作的修正建议列表（优先级排序）
}

interface DimensionResult {
  passed: boolean;
  score: number;    // 该维度得分 0-100
  issues: string[]; // 发现的问题列表
}
```

## 6 维度检测规则

各维度的详细检测项、评分标准和修正建议格式见 `references/` 目录下的对应文件——这些是 `scripts/review_html.py` 的**实现依据**，运行时由脚本统一执行，无需人工读取：

| 维度 | 权重 | 规则文件（脚本实现依据） |
|------|------|---------|
| design-token 合规性 | 25% | `references/design-token-compliance.md` |
| 结构完整性 | 25% | `references/structural-integrity.md` |
| 排版合理性 | 20% | `references/typographic-quality.md` |
| 文体契合度 | 20% | `references/genre-fit.md` |
| 装饰使用合理性 | 10% | `references/decoration-usage.md` |
| 安全性审查（XSS 防护） | 一票否决 | `references/security-check.md` |

综合分 = Σ(前 5 维度分 × 权重)（安全性维度不参与综合分计算）

## 通过门槛

- `score ≥ 80`
- 所有维度的 `passed` 均为 `true`

任一维度 `passed = false` 则整体 `passed = false`，无论综合分高低。

安全性维度（SC-01~SC-06）任意触发 → 整体 `passed = false`，不受 score 影响。

## 打回修正逻辑（一次，不循环）

```
result = review(html)            # 只检测一次
if result.passed:
    return html                  # 通过 → 直接输出

# 不通过 → 回上游做一次定向修正后直接输出，不再复检
#   路线 A: doc_typeset.revise(html, result.actionable_feedback)
#   路线 B: doc_edit.revise(html, result.actionable_feedback)
html = upstream_skill.revise(html, result.actionable_feedback)
return html
```

## 不可还原 inline badge 检测（SI-07）

`<span>` + `background` + `padding`/`border-radius`/`inline-block` 组合在 docx 中不可还原。检测到即报 Error，改用 `<p>` 纯字体属性或 `<table><td>` 承载底纹。

## actionable_feedback 格式规范

每条反馈须满足：
1. 指明**问题位置**（CSS 属性名、HTML 元素、class 名等）
2. 给出**具体修正方式**（替换为什么、添加什么）
3. 使用中文，简洁明了

示例：
- ✅ `style="font-size: 14px" 中存在裸字号，请替换为 style="font-size: var(--fs-body)"`
- ✅ `<h1> 后直接出现 <h3>，标题层级跳级，请补充 <h2> 层级`
- ❌ `存在样式问题` （过于模糊）


---

## 附：references（详细参考）


### references/decoration-usage.md

# 装饰使用合理性检测规则

**维度权重：10%**

## 检测目标

确保装饰组件（callout / divider / section-marker / data-card）使用合理，不过度装饰，语义匹配正确。

## 检测项列表

### DU-01：callout 频率控制

全文 `data-component="callout"` 数量不得超过 **5 个**。

**违规模式：** 文档中有 6 个及以上 callout 组件

**修正方向：** 合并相邻同类 callout，或将普通说明改为正文段落，只保留最重要的提示。

---

### DU-02：data-card 内容非空

所有 `data-component="data-card"` 内必须包含实际内容（`.card-value` 或 `.card-kv-list`），不得为空壳。

**违规模式：**
```html
<div data-component="data-card" data-title="指标">
  <!-- 空内容 ❌ -->
</div>
```

**修正方向：** 填充实际数据值，或删除无内容的 data-card。

---

### DU-03：callout-danger 语义匹配

`data-variant="danger"` 的 callout 只能用于真正的危险/禁止内容（含"禁止"、"严禁"、"不得"、"危险"等关键词），不得用于普通提示。

**违规模式：**
```html
<div data-component="callout" data-variant="danger">
  请注意保存文件。  ❌（普通提示不应用 danger）
</div>
```

**修正方向：** 将普通提示改为 `data-variant="info"` 或 `data-variant="warning"`。

---

### DU-04：禁止连续使用 divider

相邻两个 `data-component="divider"` 之间必须有实质内容（至少一个非空 `<p>`、标题或其他内容元素）。

**违规模式：**
```html
<div data-component="divider"></div>
<div data-component="divider"></div>  ❌（连续分隔线）
```

---

### DU-05：section-marker 层级对齐

`data-component="section-marker"` 的 `data-level` 属性应与紧邻的标题层级一致。

**违规模式：**
```html
<div data-component="section-marker" data-level="h2" data-number="1">
  <!-- 但实际内容只是 h3 级别的小节 ❌ -->
</div>
```

**检测方式：** section-marker 的 data-level 值应与其后紧跟的标题元素（h2/h3/h4）一致。

---

### DU-06：装饰组件总密度

装饰组件总数（callout + section-marker + data-card + divider）不得超过文档 `<p>` 数量的 **50%**（装饰组件过多会压过正文）。

**计算公式：**
```
decoration_density = (callout数 + section-marker数 + data-card数) / p元素数
```

`decoration_density > 0.5` 时发出 WARNING（不影响 passed，但计入 issues）。

divider 不计入分子，因为 divider 是结构性而非内容性装饰。

---

## 评分标准

| 违规类型 | 扣分 |
|---------|------|
| DU-01 callout 超限（每超出 1 个） | -10 |
| DU-02 data-card 空内容（每处） | -15 |
| DU-03 danger 语义不匹配（每处） | -15 |
| DU-04 连续 divider（每处） | -10 |
| DU-05 section-marker 层级不对（每处） | -10 |
| DU-06 装饰密度过高（WARNING） | -0 |

初始分 100，扣分后最低 0。综合分 < 70 则该维度 `passed = false`（装饰维度容忍度略高于结构维度）。

## 修正建议格式

```
[DU-0X] {问题描述}，建议 {具体修正方式}
```

示例：
- `[DU-01] 文档包含 7 个 callout，超出上限 5 个，请将第 4-5 个普通 info callout 合并或改为正文`
- `[DU-03] 第 2 个 callout 使用了 danger 变体但内容为普通注意事项，请改为 data-variant="info"`
- `[DU-04] 第 15 行和第 16 行出现连续两个 divider，请删除其中一个`


### references/design-token-compliance.md

# design-token 合规性检测规则

**维度权重：25%**

## 检测目标

确保 HTML 中所有样式属性均通过 CSS 变量 `var(--)` 引用 design token，禁止出现裸字面量值。

## 检测项列表

### DT-01：禁止裸色值

style 属性或 `<style>` 标签中不得出现裸色值。

**违规模式：**
- `style="color: #333333"`
- `style="background: #f5f7fa"`
- `style="border-color: rgb(0, 0, 0)"`
- `style="background: rgba(0, 0, 0, 0.5)"`

**合规示例：**
- `style="color: var(--color-text)"`
- `style="background: var(--color-highlight)"`

**例外：** `var(--token, #fallback)` 形式中的 fallback 值允许出现。

---

### DT-02：禁止裸字号

style 属性或 `<style>` 标签中不得出现裸字号。

**违规模式：**
- `style="font-size: 14px"`
- `style="font-size: 12pt"`
- `style="font-size: 1.2em"` （直接在 style 属性中）
- `style="font-size: 1rem"`

**合规示例：**
- `style="font-size: var(--fs-body)"`
- `style="font-size: var(--fs-h2)"`

---

### DT-03：禁止裸间距

style 属性或 `<style>` 标签中不得出现裸 margin/padding 值。

**违规模式：**
- `style="margin: 10px 0"`
- `style="padding: 16px"`
- `style="margin-top: 2em"`

**合规示例：**
- `style="margin-bottom: var(--spacing-paragraph)"`
- `style="padding: var(--spacing-block)"`

---

### DT-04：禁止裸字体族

style 属性中不得直接指定字体族字符串。

**违规模式：**
- `style="font-family: 'SimHei', sans-serif"`
- `style="font-family: 宋体"`

**合规示例：**
- `style="font-family: var(--ff-heading)"`

---

### DT-05：`:root` 变量块必须存在

输出 HTML 的 `<style>` 标签内必须包含 `:root { }` 变量声明块，且至少包含 `--fs-body`、`--color-text`、`--ff-body` 三个基础变量。

**违规模式：**
- HTML 中无 `<style>` 标签
- `<style>` 内无 `:root { }` 块
- `:root` 块为空

---

## 评分标准

| 问题数量 | 得分 |
|---------|------|
| 0 个违规 | 100 |
| 1-2 个违规 | 80 |
| 3-5 个违规 | 60 |
| 6-10 个违规 | 40 |
| >10 个违规 | 20 |

缺少 `:root` 块（DT-05）直接判定该维度 `passed = false`。

## 修正建议格式

```
[DT-0X] {元素描述} 中 {属性名} 使用了裸值 "{裸值}"，请替换为 var(--{对应token名})
```

示例：
- `[DT-01] <p class="summary"> 的 style 属性中 color 使用了裸值 "#333"，请替换为 var(--color-text)`
- `[DT-02] <h2> 的 style 属性中 font-size 使用了裸值 "16pt"，请替换为 var(--fs-h2)`


### references/genre-fit.md

# 文体契合度检测规则

**维度权重：20%**

## 检测目标

根据文档 genre 检查是否包含该文体的必需结构元素。缺少必需元素直接判定该维度 `passed = false`。

## 必需结构元素表

| genre | 必需元素 | 检测方式 |
|-------|---------|---------|
| `government-doc` | 发文字号区、红头标题、落款区 | 见 GF 规则 |
| `legal-contract` | 当事人信息区、条款编号区、签章区 | 见 LC 规则 |
| `academic-paper` | 摘要区、参考文献区 | 见 AP 规则 |
| `stock-research` | 摘要框、风险提示区、免责声明区 | 见 SR 规则 |
| `business-report` | 执行摘要区、至少一个数据表格 | 见 BR 规则 |
| `meeting-minutes` | 出席人列表、议题列表、决议区 | 见 MM 规则 |
| `general` / 其他 | 无强制要求 | 直接通过，score=100 |

---

## GF — government-doc 规则

### GF-01：发文字号区
HTML 中必须含有 `.doc-number` 或包含"〔"+"〕"+"号"文字模式的元素。

### GF-02：红头标题
必须含有 `.doc-issuer` 或 `.gov-doc-header`，且其文字颜色应引用 `var(--color-gov-red)`。

### GF-03：落款区
必须含有 `.doc-footer-sign` 或 `.issuer-sign` 元素，且位于文档末尾。

### GF-04：公文标题居中
`<h1>` 或 `.gov-doc-title` 必须有 `text-align: center` 或对应 CSS class。

---

## LC — legal-contract 规则

### LC-01：当事人信息区
必须含有 `.party-info` 表格，且包含甲方/乙方相关内容。

### LC-02：条款编号
正文中必须含有"第"+"条"或"第"+"章"的条款编号结构（`<h2>/<h3>` 内）。

### LC-03：签章区
必须含有 `.signature-block` 和 `.signature-party` 元素。

---

## AP — academic-paper 规则

### AP-01：摘要区
必须含有 `.abstract` 或 `<section aria-label="摘要">` 且包含 `.abstract-text`。

### AP-02：参考文献区
必须含有 `.references` 或 `<section aria-label="参考文献">` 且包含 `.reference-list`。

### AP-03：IMRaD 结构建议
建议（非强制）含有 `#section-introduction`、`#section-conclusion` 等 IMRaD 节点。缺少时记入 issues 但不降低 passed。

---

## SR — stock-research 规则

### SR-01：摘要框（核心观点）
必须含有 `.abstract-box` 或 `.research-abstract` 元素。

### SR-02：风险提示区（强制）
必须含有 `data-component="callout" data-variant="warning"` 且内含"风险"二字。

**缺少风险提示直接判定该维度 `passed = false`，无论其他项得分如何。**

### SR-03：免责声明区
必须含有 `.disclaimer` 元素，位于文档末尾。

---

## BR — business-report 规则

### BR-01：执行摘要区
必须含有 `.executive-summary` 元素。

### BR-02：数据表格
文档中至少含有 1 个 `<table>` 或 `data-component="data-card"` 元素。

---

## MM — meeting-minutes 规则

### MM-01：出席人列表
必须含有 `.attendee-table` 或包含"姓名"/"部门"列头的表格。

### MM-02：议题列表
必须含有 `.agenda-list` 或 `.agenda-item` 元素。

### MM-03：决议区
必须含有 `.resolution-list` 或 `.resolution-item` 元素，或包含"决议"文字的节区。

---

## 评分标准

| 情况 | 得分 | passed |
|------|------|--------|
| 所有必需元素均存在 | 100 | true |
| 缺少 1 个非关键必需元素 | 70 | false |
| 缺少 2 个必需元素 | 50 | false |
| 缺少关键必需元素（SR-02 风险提示等） | 0 | false |
| genre = general | 100 | true |

## 修正建议格式

```
[GF/LC/AP/SR/BR/MM-0X] 缺少 {元素名称}，请按 {规则说明} 补充对应结构
```

示例：
- `[SR-02] 缺少风险提示区（callout warning），研报必须包含风险提示，请在免责声明前添加 <div data-component="callout" data-variant="warning">风险提示：...</div>`
- `[AP-01] 缺少摘要区，请在正文前添加 <section class="abstract" aria-label="摘要">...</section>`


### references/security-check.md

# 安全性审查（XSS 防护）检测规则

**维度权重：独立维度，不参与综合分计算。任意检测项触发 → 整体 `passed = false`（一票否决）**

## 检测目标

确保输出 HTML 不包含任何可被浏览器执行的恶意代码或外部资源引用，防止 XSS 注入风险。

## 检测项列表

### SC-01：禁止 `<script>` 标签

HTML 中不得出现任何 `<script>` 标签，包括内联脚本和外部引用。

**违规模式：**
- `<script>alert(1)</script>`
- `<script src="evil.js"></script>`
- `<script type="text/javascript">...</script>`

**例外：** 无例外，一律禁止。

---

### SC-02：禁止内联事件处理器

HTML 元素不得含有任何 `on*` 事件属性。

**违规模式：**
- `<img src="x" onerror="alert(1)">`
- `<div onclick="doSomething()">`
- `<body onload="init()">`
- `<a onmouseover="...">`

**检测方式：** 正则 `\son\w+\s*=`

---

### SC-03：禁止 `javascript:` 伪协议

`href`、`src`、`action` 等属性不得使用 `javascript:` 开头的值。

**违规模式：**
- `<a href="javascript:void(0)">` — 应改用 `href="#"` 或 `href="#section-N"`
- `<iframe src="javascript:...">`
- `<form action="javascript:...">`

**例外：** `href="#"` 锚点和 `href="#section-N"` 文档内锚点允许。

---

### SC-04：禁止危险嵌入标签

不得出现以下标签（在排版文档中无合理使用场景）：
- `<iframe>`
- `<object>`
- `<embed>`
- `<applet>`
- `<base>`

**违规模式：**
```html
<iframe src="https://example.com"></iframe>   ❌
<object data="file.swf"></object>              ❌
<base href="https://attacker.com">             ❌
```

---

### SC-05：禁止 CSS 危险表达式

`<style>` 块和 `style` 属性中不得出现以下内容：

- `expression(...)` — IE 遗留 CSS 表达式，可执行任意 JS
- `url(javascript:...)` — CSS 中的 JS 伪协议
- `-moz-binding` — Firefox XBL 绑定，可加载外部脚本

**违规模式：**
```css
width: expression(document.body.offsetWidth);   ❌
background: url(javascript:alert(1));            ❌
-moz-binding: url(http://attacker.com/xss.xml);  ❌
```

---

### SC-06：禁止外部资源引用

`<img>`、`<link>`、`<script>`、`<form>` 等标签的 `src`/`href`/`action` 属性不得引用外部 URL。

**违规判定（FAIL）：**
- `<img src="https://tracker.example.com/pixel.gif">` — 外部图片
- `<link href="https://unknown.cdn.com/style.css">` — 非字体的外部样式
- `<form action="https://external.com/submit">` — 外部表单提交

**警告判定（WARNING，不影响 passed）：**
- `<link rel="stylesheet" href="https://fonts.googleapis.com/...">` — Google Fonts 等知名字体服务
- `<link rel="stylesheet" href="https://fonts.bunny.net/...">` — 同类字体 CDN

文档应为自包含 HTML，所有资源通过 CSS 变量或内联方式提供。

---

## 评分标准

| 触发项 | 影响 |
|--------|------|
| SC-01 存在 `<script>` 标签 | 整体 `passed = false`，`score = 0` |
| SC-02 内联事件处理器 | 整体 `passed = false`，`score = 0` |
| SC-03 `javascript:` 伪协议 | 整体 `passed = false`，`score = 0` |
| SC-04 危险嵌入标签 | 整体 `passed = false`，`score = 0` |
| SC-05 CSS 危险表达式 | 整体 `passed = false`，`score = 0` |
| SC-06 外部资源（非字体服务） | 整体 `passed = false`，`score = 0` |
| SC-06 外部字体服务（Google Fonts 等） | WARNING，计入 `issues`，不影响 `passed` |

安全性维度不参与综合分计算，但任意 SC-01~SC-06（字体服务除外）触发均导致整体 `passed = false`。

## 修正建议格式

```
[SC-0X] {元素描述} 包含 {违规内容}，存在 XSS/注入风险，请删除该属性/标签或替换为安全写法
```

示例：
- `[SC-01] 文档末尾存在 <script> 标签，内含内联脚本，请完全删除该标签`
- `[SC-02] <img> 元素含有 onerror 事件属性，请删除该属性`
- `[SC-03] <a href="javascript:void(0)"> 使用了 javascript: 伪协议，请改为 href="#" 或有效锚点`
- `[SC-04] 文档中存在 <iframe> 标签，排版文档不允许嵌入框架，请删除`
- `[SC-06] <img src="https://tracker.example.com/pixel.gif"> 引用了外部资源，请改为内联或删除`


### references/structural-integrity.md

# 结构完整性检测规则

**维度权重：25%**

## 检测目标

确保 HTML 文档结构合法，元素嵌套正确，必需元素完整存在。

## 检测项列表

### SI-01：标题层级连续性

标题层级必须连续递进，不得跳级。

**违规模式：**
- `<h1>` 后直接出现 `<h3>`（跳过 h2）
- `<h2>` 后直接出现 `<h4>`（跳过 h3）
- 正文中出现 `<h6>`（层级过深，通常为误用）

**合规示例：**
```
h1 → h2 → h3 → h4  ✅
h1 → h2 → h2 → h3  ✅（同级出现多次无问题）
h1 → h3            ❌（跳级）
```

---

### SI-02：表格结构完整性

所有 `<table>` 必须包含 `<thead>` 和 `<tbody>`。

**违规模式：**
- `<table>` 内直接放 `<tr>`，无 `<thead>/<tbody>` 包裹
- `<thead>` 内使用 `<td>` 而非 `<th>`

**合规示例：**
```html
<table>
  <thead><tr><th>列1</th><th>列2</th></tr></thead>
  <tbody><tr><td>值1</td><td>值2</td></tr></tbody>
</table>
```

---

### SI-03：列表结构完整性

`<ol>` 和 `<ul>` 内只能直接包含 `<li>`，不得直接包含 `<p>` 或其他块级元素。

**违规模式：**
```html
<ul>
  <p>这不是列表项</p>  ❌
</ul>
```

**合规示例：**
```html
<ul>
  <li>这是列表项</li>
</ul>
```

---

### SI-04：禁止非法块级嵌套

`<p>` 内不得嵌套块级元素（`<div>`、`<table>`、`<ul>`、`<ol>`、`<h1>`-`<h6>` 等）。

**违规模式：**
```html
<p>
  文本
  <div>这是非法嵌套</div>  ❌
</p>
```

---

### SI-05：图片必须有 alt 属性

所有 `<img>` 必须包含非空的 `alt` 属性。

**违规模式：**
- `<img src="...">` （缺少 alt）
- `<img src="..." alt="">` （alt 为空）

**合规示例：**
- `<img src="..." alt="图表：2024年营收趋势">`

---

### SI-06：链接 href 不得为空

`<a>` 标签的 `href` 属性不得为 `#` 占位（锚点链接除外）或空字符串。

**例外：** `href="#section-N"` 等文档内锚点链接允许。

---

### SI-07：禁止不可还原的 inline badge

`<span>` 元素上的 `background` + `padding` / `border-radius` / `display:inline-block` 组合在 docx 中完全不可还原（`<span>` 不对应独立段/行/单元格，无载体承载底纹）。

**违规模式（满足任一组合即触发）：**
- `<span>` 同时含 `background`（非 transparent/none）+ `padding`
- `<span>` 含 `background` + `border-radius`
- `<span>` 含 `background` + `display: inline-block`

**替代方案：**
- 纯文字标签 → 改为 `<p class="cover-category">` + 可还原属性（color/font-size/font-weight/letter-spacing）
- 需要底纹效果 → 改为单行 `<table>` 单元格（`<td>` 的 background 在 docx 中可还原为单元格底纹）

**合规示例：**
```html
<!-- 方案 A：纯字体属性 -->
<p class="cover-category" style="color:var(--color-primary);font-weight:700;font-size:14pt">行业深度研究</p>

<!-- 方案 B：table 单元格承载底纹 -->
<table class="cover-category-badge"><tr>
  <td style="background:var(--color-primary);color:#fff;font-weight:700;padding:4px 12px">行业深度研究</td>
</tr></table>
```

---

### SI-08：禁止 table-in-table 嵌套

`<table>` 的子树内不得再出现 `<table>`。html-to-docx 转换器（`html4docx` + `python-docx`）的表格样式后处理依赖 `document.tables`，**该 API 只返回顶层表格**，嵌套在单元格里的内层表格无法被应用边框、单元格对齐、底纹等样式，导致渲染严重偏差。此外 `abstract-card` 类单列表格（见 `doc-typeset/SKILL.md §(d)`）按规则只承载**多段落 `<p>` 富文本**，不得塞入子表格。

**违规模式：**
```html
<table class="abstract-card">
  <tr><td>
    <p>核心指标速览</p>
    <table class="three-line-table">   <!-- ❌ table-in-table -->
      <thead>...</thead><tbody>...</tbody>
    </table>
  </td></tr>
</table>
```

**合规示例（内层表格移出作为顶层独立表格）：**
```html
<h3>核心指标速览</h3>
<table class="three-line-table">
  <thead>...</thead><tbody>...</tbody>
</table>
```

> 若确需卡片视觉容器，`abstract-card` 内只放 `<p>` 段落；指标矩阵本身必须作为顶层独立 `<table>`。

---

## 评分标准

| 违规严重程度 | 扣分 |
|------------|------|
| SI-01 跳级（每处） | -15 |
| SI-02 表格缺结构 | -20 |
| SI-03 列表非法子元素 | -10 |
| SI-04 非法块嵌套 | -15 |
| SI-05 缺 alt（每处） | -5 |
| SI-06 空链接（每处） | -5 |
| SI-07 不可还原 badge（每处） | -20 |
| SI-08 table-in-table 嵌套（每处） | -20 |

初始分 100，扣分后最低 0。综合分 < 75 则该维度 `passed = false`。

## 修正建议格式

```
[SI-0X] {问题描述}，建议 {具体修正方式}
```

示例：
- `[SI-01] <h1> 后直接出现 <h3>，标题层级跳级，请在中间补充 <h2> 层级章节`
- `[SI-02] 第 3 个 <table> 缺少 <thead>，请将第一行 <tr> 移入 <thead> 并将 <td> 改为 <th>`


### references/typographic-quality.md

# 排版合理性检测规则

**维度权重：20%**

## 检测目标

确保文档排版合理，段落长度适当，空白分布正常，视觉层次清晰。

## 检测项列表

### TQ-01：段落长度控制

单个 `<p>` 内文字不得超过 **500 个字符**。超过应拆分为多段。

**违规模式：**
- 单段落包含 600+ 字符的连续文字

**修正方向：** 按语义在合适位置断段，每段 150-300 字为宜。

---

### TQ-02：禁止过多连续空段落

连续出现 **3 个或以上**内容为空（或仅含空白符）的 `<p>` 标签为异常。

**违规模式：**
```html
<p>&nbsp;</p>
<p></p>
<p> </p>
```

**修正方向：** 删除多余空段，用 CSS `margin` 控制间距。

---

### TQ-03：标题后必须有正文

两个相邻标题之间（同级或降级）必须有至少一段正文内容。不允许 `h2` 紧接 `h3` 而中间无内容。

**违规模式：**
```html
<h2>第一章</h2>
<h3>第一节</h3>  <!-- 中间无任何内容 ❌ -->
```

**合规示例：**
```html
<h2>第一章</h2>
<p>本章概述...</p>
<h3>第一节</h3>
```

**例外：** `<h1>` 紧接 `<h2>` 允许（文档开头可以直接分章）。

---

### TQ-04：h1 最多出现 1 次

文档中 `<h1>` 不得超过 1 个（每个文档只有一个主标题）。

**违规模式：** 文档中出现 2 个及以上 `<h1>`

---

### TQ-05：h2 数量建议不超过 10 个

`<h2>` 超过 10 个时发出警告（不强制降分，但建议检查文档是否过于碎片化）。

严重程度：WARNING（不影响 passed 判定，但计入 issues）

---

### TQ-06：正文字号一致性

`<p>` 元素的 font-size 应统一使用 `var(--fs-body)`，不得在不同段落间使用不同的字号 token（特殊标注如摘要、小字注释除外）。

---

### TQ-07：表格标题（caption）位置

有 `<caption>` 的表格，`<caption>` 必须是 `<table>` 的第一个子元素，不得放在 `<tbody>` 后。

---

### TQ-08：封面块级元素必须显式声明 text-align

若文档存在 `.cover` 容器，其**直接后代**中的块级封面元素（`<h1>`、`class` 含 `subtitle` / `report-tag` / `report-title` / `cover-*` 的块级元素）必须在 `style` 属性内**显式包含 `text-align`** 声明。

**违规模式：**
```html
<style>
  p { text-align: justify; }  /* 常见默认 */
  .cover { text-align: center; }
</style>
<section class="cover">
  <h1 style="font-size: var(--fs-cover-title);">模拟人生 4</h1>  <!-- ❌ 未显式声明 text-align -->
  <div class="subtitle" style="color: var(--color-muted);">副标题</div>  <!-- ❌ -->
</section>
```

**为什么严重：** `text-align` 不从 `.cover` 继承到子块。当 CSS 里存在 `p { text-align: justify }`（正文两端对齐是常见默认），封面 `<h1>` / `.subtitle` 若未自己写对齐，转 docx 后会被 justify 兜底，出现 "模 拟 人 生 4"（字距被强行撑开）的严重视觉 bug。

**合规示例：**
```html
<h1 style="font-size: var(--fs-cover-title); text-align: center;">模拟人生 4</h1>
<div class="subtitle" style="color: var(--color-muted); text-align: center;">副标题</div>
```

**修正方向：** 在每个封面块级元素的 `style` 内补 `text-align: center`（或 left/right 视设计而定），不允许"靠父容器继承"。

---

## 评分标准

| 违规类型 | 扣分 |
|---------|------|
| TQ-01 超长段落（每处） | -10 |
| TQ-02 连续空段落 | -15 |
| TQ-03 相邻标题无正文（每处） | -10 |
| TQ-04 多个 h1 | -20 |
| TQ-05 h2 > 10（仅警告） | -0 |
| TQ-06 字号不一致 | -10 |
| TQ-07 caption 位置错误 | -5 |
| TQ-08 封面块级元素缺 text-align（每处） | -10 |

初始分 100，扣分后最低 0。综合分 < 75 则该维度 `passed = false`。

## 修正建议格式

```
[TQ-0X] {问题描述}，建议 {具体修正方式}
```

示例：
- `[TQ-01] 第 2 个 <p> 包含 650 字符，超出 500 字上限，请在第 3 句后断段`
- `[TQ-03] <h2 id="section-3"> 后直接出现 <h3>，中间无正文，请补充章节导语`
- `[TQ-08] 封面 <h1> 未显式声明 text-align，可能被 p{text-align:justify} 兜底导致字距撑开，请补 style="... text-align: center;"`
