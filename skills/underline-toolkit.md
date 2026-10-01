# underline-toolkit

> 生成带下划线填空位的 Word 文档（create 模式）或对已有下划线模板回填数据（fill 模式）。适用于合同、协议、申请表、有封面的文档（如毕业论文）等。触发关键词：下划线、填空、下划线模板、合同填空、表单回填、下划线回填。

## 使用说明（重要）

本技能从 WorkBuddy 导入。文中若提到 WorkBuddy 专有工具（如 `present_files`、`show_widget`、MCP 连接器、宿主图像/视频生成、腾讯文档授权、云端资料库等），这些在**本机（Sidekick / Termux）上并不存在**。遇到这类步骤，请改用本机已有能力实现，或直接跳过并说明原因。

**本文档的价值在于方法论与规范**（怎么写、怎么排版、怎么避坑）——照此执行即可，不必强行还原 WorkBuddy 的实现方式。

<goal>
为 Expert 的 docx 生成阶段提供**下划线填空**的统一能力。所有 API 实现在 `src/skills/underline-toolkit/toolkit.py`。
</goal>

<workflow>

### 1. 判断模式

| 模式 | 触发条件 | 详细规则 |
|------|----------|----------|
| **create** | 需要生成**带空白下划线填空位**的新文档 | → 查阅 `src/skills/underline-toolkit/references/create-rules.md` |
| **fill** | 需要对已有下划线模板**回填数据** | → 查阅 `src/skills/underline-toolkit/references/fill-rules.md` |

### 2. 在生成脚本中 import

```python
import sys
sys.path.insert(0, "<workspace_dir>/src/skills/underline-toolkit")
from toolkit import add_underline_run, add_normal_run, add_paragraph_text, setup_a4_page
```

### 3. 按对应 references 文件执行

- **create 模式**：`add_underline_run()` 不传 `value`（或 `value=""`）→ 空白下划线
- **fill 模式**：`add_underline_run(value="实际内容")` → 内容 + 下划线保留

两种模式使用完全相同的函数，唯一区别是 `value` 参数是否传值。

</workflow>

<restrictions>
- ❌ 永远不要使用 `________`（连续下划线字符）制作填空下划线
- ❌ 不得跳过 `rFonts.eastAsia` 设置
- ❌ 不得硬编码绝对路径
</restrictions>


---

## 附：references（详细参考）


### references/create-rules.md

# DOCX 下划线创建规则

## 核心原则

### ⚠️ 绝对禁止

**永远不要**使用 `________`（连续下划线字符）来制作填空下划线！

```python
# ❌ 错误！用户在 Word 中填写后下划线会消失
run = p.add_run("甲方：________________")
```

**原因**：下划线字符本身就是文本内容，用户在 Word/WPS 中点击填写时会删除这些字符，导致下划线消失。

### ✅ 正确方案：字符下划线

统一使用 **字符下划线**（Run 的 underline 属性）实现所有填空下划线，适用于所有场景。

---

## 原理

在段落中添加一个带有 **`underline` 属性**的 Run（内容为空格或实际文本）。

- 空白模板：Run 的 text 为空格，用户看到一段下划线
- 带值时：Run 的 text 为实际内容，underline 属性保留

### XML 结构

```xml
<!-- 带下划线的 Run -->
<w:r>
  <w:rPr>
    <w:rFonts w:eastAsia="仿宋"/>
    <w:u w:val="single"/>
  </w:rPr>
  <w:t xml:space="preserve">深圳市南山区</w:t>
</w:r>
```

---

## 核心 API

- **`add_underline_run(paragraph, ...)`** — 添加带字符下划线的 Run（空格或实际文本），返回 `run`
  > 📍 深入参考：`src/skills/underline-toolkit/toolkit.py` → `add_underline_run()`

- **`add_normal_run(paragraph, text, ...)`** — 添加普通文字 Run（无下划线），用于拼接正文，返回 `run`
  > 📍 深入参考：`src/skills/underline-toolkit/toolkit.py` → `add_normal_run()`

---

## blank_spaces 取值经验（14pt 仿宋 / A4 页面）

> A4 可用宽度 ≈ 15.92cm（左右页边距各 2.54cm），14pt 中文字宽 ≈ 0.49cm，1 个 NBSP ≈ 0.25cm。
> **核心原则：先算文字占宽，再用剩余空间反推 NBSP 数量，宁短勿溢。**

| 场景 | 推荐值 | 说明 |
|------|--------|------|
| 独占一行（如合同编号） | 10-12 | 前面文字短，可适当拉长 |
| 同行两段（如姓名+身份证） | 6-8 | 两段下划线+两段标签，空间紧张 |
| 同行两段（如电话+地址） | 8 | 标签较短时可稍多 |
| 行内嵌入（如年/月/日） | 2-4 | 仅容纳几个数字 |
| 行内短填空（如金额） | 5-6 | 4-5 位数字+少量留白 |

**避坑：**
- 同一行放两个填空时，**每段不超过 8**，否则容易溢出换行
- 能合并到同一行的字段（如姓名+身份证）尽量合并，减少行数、排版更紧凑

---

## 通用辅助函数

以下辅助函数在 `src/skills/underline-toolkit/toolkit.py` 中提供，生成脚本中常用：

- **`add_paragraph_text(doc, text, ...)`** — 添加纯文本段落（不含下划线填空），返回 `Paragraph`
  > 📍 深入参考：`src/skills/underline-toolkit/toolkit.py` → `add_paragraph_text()`

- **`setup_a4_page(doc, ...)`** — 设置 A4 页面尺寸和页边距
  > 📍 深入参考：`src/skills/underline-toolkit/toolkit.py` → `setup_a4_page()`

---

## 注意事项与常见陷阱

### 必须注意

1. **中文字体必须同时设置 `font.name` 和 `eastAsia`**：
   ```python
   run.font.name = "仿宋"
   run._element.rPr.rFonts.set(qn('w:eastAsia'), "仿宋")
   ```
   只设 `font.name` 在 Word 中中文可能显示为宋体。

2. **空白下划线需要空格占位**：`blank_spaces` 不能太小，否则下划线不可见。

### 常见错误
| 错误 | 原因 | 修复 |
|------|------|------|
| 回填后下划线消失 | 用了 `________` 字符方式 | 改用 `add_underline_run()` |
| 中文显示为宋体 | 没设置 `eastAsia` | 同时设置 `font.name` 和 `eastAsia`（见上方第1条） |


### references/fill-rules.md

# DOCX 下划线回填规则 (Underline Fill-in Rules)

## 回填核心思路

所有下划线函数（见 `src/skills/underline-toolkit/toolkit.py`）统一支持 `value` 参数：

- **不传 / `value=""`** → 空白下划线（模板模式）
- **`value="实际内容"`** → 内容 + 下划线保留（回填模式）

回填 = 在原有创建代码的调用处**加上 `value` 参数**，其他参数完全不变。

涉及函数：
`add_underline_run`。


## 注意事项
- 回填时 `blank_spaces` **不影响显示**，但建议保留不变，去掉 `value` 即可回退到空白模板
- 下划线不会消失：下划线是 Run 的 underline 属性，与文字内容解耦
