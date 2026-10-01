# Web 界面调试技能 —— 改 UI 并当场验证

> 建立日期：2026-09-29
> 适用对象：agent.py 里内嵌的 `WEB_HTML` 界面（控制台 / 设置 / 侧栏 / 对话区）
> 核心工具：`~/.termux-agent/cdp.py`（CDP 客户端，直连华为浏览器）

---

## 一、为什么需要这份文档

这个界面**没有构建流程、没有热重载、没法在本地预览** —— 改完只能让浏览器重新加载。
而它偏偏又是个 CSS 层叠密集的单文件界面（几千行内嵌样式），
**"改一行没生效"和"改一行改坏了"都无法靠肉眼立刻判断**。

所以这里记的是：**怎么用工具看到真实渲染，以及怎么不把用户的对话搞断**。

---

## 二、铁律：绝不在对话中途 reload 页面

**用户就是在这个页面上跟模型对话的。** 页面一刷新，承载对话的 WebSocket 就断了，
正在生成的回复推不出去，`turn.log` 会记成「中断-工具已发起但结果没收全」，
用户看到的是"模型话说一半凭空消失"。

```
❌ 不要：cdp.py "location.reload()"          # 对话进行中执行 → 掐断自己的通信通道
✅ 要  ：让用户自己刷新；或直接读页面已有的计算样式（改了源码也照样能读当前值）
```

2026-09-29 连栽两次（22:49 轮次 2/3）。

---

## 三、标准流程：读 → 改 → 验

### 3.1 读（改之前必须做）

```bash
cd ~/.termux-agent
cat > _x.js <<'JS'
(function(){
  var el = document.querySelector('.item');
  var s = getComputedStyle(el);
  return 'class=' + el.className + '  bg=' + s.backgroundColor + '  圆角=' + s.borderRadius;
})()
JS
timeout 25 python3 cdp.py "$(cat _x.js)" -p "8765" 2>&1 | tail -5
```

**必须先读真实 DOM 和计算样式再动手。** 2026-09-29 的教训：给会话项写选中态样式时
凭印象用了 `.item.on`，实际类名是 `.item.cur` —— 特异性也更高（0,2,0 vs 0,1,1），
导致规则完全空转，改了几轮都没效果。

### 3.2 改

用 `selfupdate`（自动备份 + 语法检查 + 失败回滚）。

### 3.3 验（改完必须做）

**服务重启后**再读一次实际值，确认生效。**不要只看源码就宣称改好了。**

验证要点：
- **服务是否活着**：`curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8765/ --max-time 8`
- **代码是否就位**：`grep -n "改动特征" agent.py`
- **渲染是否生效**：CDP 读实际计算样式

### 3.4 删除 DOM 元素时，必须同步清掉**所有**事件绑定【血泪】

**症状**：删掉某个按钮/面板后，整个界面「死掉」—— 不能对话、按钮全无反应、
看着像浏览器崩了，但刷新也没用（因为页面本身就是坏的）。

**原因**：模块级代码里有 `$('#xxx').onclick = fn`。元素删了，`$()` 返回 `null`，
`null.onclick = fn` 抛 TypeError，**同一个 `<script>` 块里这行之后的全部代码中断** ——
包括紧跟着的 `connect()`（建 WebSocket）。于是页面元素都在、样式也对，
**但根本没连上后端**，表现就是「页面活着，对话死了」。

> **2026-10-01 真实事故**：按用户要求删「电话」按钮时，只给 `phonebtn`/`phonex`
> 两处加了 null 保护，**漏了 `ph-main`/`ph-stop`**。崩溃点就在 `connect()` 前 3 行，
> 界面直接失联，最后靠**另一台电脑 adb** 才把服务救回来。

**规范流程**：

1. 删元素前，先找全引用：
   ```bash
   grep -n 'id="电话按钮id"' agent.py          # 元素本体
   grep -n "\$('#电话按钮id')" agent.py        # 所有 JS 引用（HTML/CSS/JS 一起捞）
   ```
2. 改完跑扫描，确认**没有模块级裸引用指向不存在的 id**：
   ```bash
   python3 - <<'EOF'
   import re, sys
   sys.argv=['p']; import agent
   html = agent.WEB_HTML
   ids = set(re.findall(r'id="([^"]+)"', html))
   bad = []
   for i, ln in enumerate(html.split('\n'), 1):
       m = re.match(r"^\$\(['\"]#([\w-]+)['\"]\)", ln)   # 行首无缩进 = 模块级
       if m and m.group(1) not in ids:
           bad.append((i, m.group(1), ln.strip()[:70]))
   print("危险:", bad if bad else "无 ✓")
   EOF
   ```
3. 函数体内的引用（页面加载时不执行）加个 `if(!el) return;` 即可，不必删。
4. 顺手用 qjs 校验 script 语法（**只能查出语法错，查不出 null 的运行时错**）：
   ```bash
   # 把 <script> 内容抽出来存成 .js，逐个 qjs 跑
   qjs 片段.js 2>&1 | head -3   # 报 ReferenceError: document is not defined = 语法 OK
   ```
5. 关键调用（如 `connect()`）**别放在一长串绑定语句后面**，否则前面任何一处抛错都会连坐。

**旁证**：`~/.termux-agent/access.log` 里若出现 `dbg=ERR:Script error. | line 0:0`，
就是前端真报过错（浏览器对内联脚本错误只肯给这句笼统话，看不到细节）。

---

## 四、验证技巧

### 4.1 量"文字"的边界要用 Range，不能只看盒子

`getBoundingClientRect()` 量的是**边框盒**，**包含 padding**。想知道文字会不会被浮层
（如绝对定位的关闭按钮）压住，必须量**文字本身**：

```js
var range = document.createRange();
range.selectNodeContents(p);
var r = range.getBoundingClientRect();   // 这才是文字实际占的范围
```

2026-09-29 就因此误判过一次：明明 `padding-right` 已生效，却因为量的是盒子右边缘而
得出"仍然重叠"的错误结论。

### 4.2 读伪元素样式

```js
getComputedStyle(el, '::after').content   // 'none' 表示没有这个伪元素
```

### 4.3 测交互状态而不改真实数据

要验证 `:checked`、`:hover` 这类状态，**造一个临时元素**，别去点真实的开关
（可能触发保存逻辑）：

```js
var t = document.createElement('input');
t.type = 'checkbox'; t.className = 'sw'; t.checked = true;
t.style.position = 'absolute'; t.style.left = '-9999px';
document.body.appendChild(t);
var s = getComputedStyle(t);   // 读选中态
document.body.removeChild(t);
```

### 4.4 打开隐藏面板量尺寸

面板是 `display:none` 时量不到尺寸。临时打开、量完还原：

```js
var ctl = document.getElementById('ctl');
var was = ctl.style.display;
ctl.style.display = '';
/* ...测量... */
ctl.style.display = was;
```

---

## 五、CSS 陷阱清单（本界面真实踩过的）

| 陷阱 | 现象 | 正确做法 |
|---|---|---|
| **`background` 简写** | 内联写 `style.background='linear-gradient(...)'` 会把 `background-color` 重置成透明，盖掉 CSS 里的底色 | 只设 `backgroundImage`，底色单独给 `backgroundColor` |
| **两套同元素样式** | `.sw` 同时存在 `body .sw`(0,1,1) 和 `.sw`(0,1,0) 两套，关着看着正常、打开就变小偏上 | 全局搜一遍同类选择器，只留一套 |
| **`order` 打乱顺序** | `.card-h b{order:2}`、`close{order:1}`，新加的 `.q` 没设 order（默认0）就跑到最前面 | 新元素也要显式设 order |
| **`:first-child` vs `:first-of-type`** | `p:first-child` 选不中（前面站着个 `<button>`） | 用 `:first-of-type` |
| **内联优先级最高** | 内联样式会盖掉任何 CSS 规则（含 `!important` 之外的） | 要么走 CSS 类，要么清楚知道自己在覆盖什么 |
| **`min-width` 撑破 flex** | flex 子项默认最小宽度是内容宽度，长文本宁可把别人挤出容器也不缩 | 可收缩的那项加 `flex:1;min-width:0` |
| **`text-overflow:ellipsis` 需配套** | 单独写不生效 | 同时写 `overflow:hidden;white-space:nowrap` |

---

## 六、工程注意

### 6.1 源码可能被别的进程同时修改

`agent.py` 会被**自动进化**、**自愈执行器**、**其它会话**改动。
表现：`selfupdate` 报"补丁未匹配"、行号漂移。

**对策**：
- 打补丁前先 `grep -n` 确认当前原文，不要用凭记忆的行号
- 补丁片段尽量短而独特（定位稳）
- 改完回来验证时重新 grep，别信旧行号

### 6.2 改前先 grep 同类选择器

这个界面有多处样式在不同 `<style>` 块里对同一元素下定义。
**新增规则前先 `grep -n "\.xxx" agent.py`，看是否已有定义、哪条特异性更高。**

### 6.3 JS 语法可离线校验

界面脚本改动后，可把片段交给 qjs 解析（报 `ReferenceError: document is not defined`
说明语法没问题，真语法错会报 `SyntaxError`）：

```bash
qjs _片段.js 2>&1 | head -3
```

---

## 七、一句话总结

**先读真实渲染，再动手；改完必须回读验证；任何时候不要 reload 用户的页面。**
