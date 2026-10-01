# ardot-slides

> Use this skill for Ardot canvas design tasks whose deliverable is a slide deck / presentation design (NOT a PowerPoint .pptx file). Covers presentations, decks, pitch decks, keynote-style slides, laun

## 使用说明（重要）

本技能从 WorkBuddy 导入。文中若提到 WorkBuddy 专有工具（如 `present_files`、`show_widget`、MCP 连接器、宿主图像/视频生成、腾讯文档授权、云端资料库等），这些在**本机（Sidekick / Termux）上并不存在**。遇到这类步骤，请改用本机已有能力实现，或直接跳过并说明原因。

**本文档的价值在于方法论与规范**（怎么写、怎么排版、怎么避坑）——照此执行即可，不必强行还原 WorkBuddy 的实现方式。

# Ardot Slides

Domain workflow and guidelines for **slide deck / presentation** deliverables on the Ardot canvas.

> **The general workflow and hard rules live in `ardot-design-core`**, which is injected alongside this skill. This skill carries the **slide-specific 6-phase workflow and design rules**. The slides workflow is more detailed and **takes precedence over the generic core workflow** for deck tasks — follow `slides-workflow.md` end-to-end, and use core for schema, editing rules, effects, and screenshot verification.

## Slide Workflow (follow strictly — do NOT improvise from the core skeleton)

- **Core slide workflow** → `{SKILL_ROOT}/workflows/slides-workflow.md` — the 6-phase process (Phase 0 Requirement Clarification → Phase 5 Final Review). Phases 0→4 are strictly sequential.
- **Mandatory slide design rules** → `{SKILL_ROOT}/references/guidelines-slides.md` — L01–L20 layout contracts, large typography, rich backgrounds, visual rhythm. This is the **authoritative source** for all slide visual decisions.

> Files referenced by the slide workflow that live in the core skill (`design-rules.md`, `ardot-workflow.md`, `ardot-schema.md`, `effects-guide.md`) are read from the **ardot-design-core** skill root — its absolute path is provided in the same prompt that injected this skill.

## pptx Boundary (read before starting)

This skill produces a **design** (Ardot canvas). If the user explicitly wants a PowerPoint `.pptx` file as the deliverable — "导出 pptx", "生成 PPT 文件", "export to PowerPoint", or references an existing `.pptx` filename — that is **not** this skill's job; defer to the `pptx` skill. If the user says "设计稿", "design", "Ardot", "canvas", "mockup", or gives no file-format constraint, stay here.


---

## 附：references（详细参考）


### references/guidelines-slides.md

ROLE: You are a professional slide deck designer.
GOAL: Produce slides that are readable in real conditions (projector, Zoom, mobile).
PRIORITY: Clarity > Readability > Hierarchy > Simplicity.

CRITICAL — FIRST PRIORITY
INPUT: Brand guidelines will be given but are NOT slide-optimized.
RULE: Always adapt brand for slides (bigger fonts, more spacing, change more if needed). Never sacrifice readability.

CORE RULES:
- One idea per slide.
- Slides are visual aids, not documents.
- If content doesn't fit at required sizes: split or remove. Never shrink fonts.
- Consistency > creativity. Reduce cognitive load.

CRITICAL – TYPOGRAPHY:
- Max 2 font families.
- Body >=28px (prefer 28–36).
- Titles >=56px (prefer 56–80).
- Section headings >=40px (prefer 40–56).
- Captions/labels >=22px. NEVER below 22px — if content doesn't fit, split or remove.
- Key data numbers 56–96px (prefer dramatically large, up to 120+ for single hero KPI).
- Use weight, not many sizes.
- Avoid ALL CAPS except labels.
- Line-height: prefer `lineHeight: "AUTO"`. If explicit, use PIXEL values (e.g. Body 28 → `lineHeight: 36`, Title 56 → `lineHeight: 64`). NEVER use unit-less multipliers like `1.2` / `1.4` / `1.6`.
- High contrast always.

LAYOUT & SPACING:
- Use grid. Align everything.
- Generous whitespace.
- No clutter.
- Apply CRAP: Contrast, Repetition, Alignment, Proximity.

COLOR:
- 2–3 core colors + neutrals.
- High contrast text/bg mandatory.
- Accent only for emphasis.
- Body text neutral.
- Colorblind-safe if possible.

VISUALS & DATA:
- Visuals support meaning, not decoration.
- Prefer custom visuals to stock.
- Charts > text for data.
- One insight per chart.
- Simplify charts (no junk).
- Highlight key datapoint.
- Icons consistent style/size.

FORMAT:
- 16:9, 1920x1080.
- Keep content >=100px from edges.

CONTENT DENSITY:
- One message per slide.
- Short phrases > sentences.
- No paragraphs.
- Title states takeaway.
- Details go to notes/appendix.

CONTEXT:
- Corp=structured.
- Startup=minimal, bold.
- Marketing=benefit-driven.
- Internal=slightly denser.
- Keynote=very visual.
(Rules above always apply.)

LAYOUT CONTRACTS (use IDs, follow strictly):

L01:
Intent=Cover
Grid=CenterStack
Content=Title(64-96,Bold); Subtitle(32-40); Meta(24-28)
Rules=CenterXY; PlentySpace; NoExtras

L02:
Intent=BoldCover
Grid=LeftBlock
Content=Title(72-96,Max2Lines); Subtitle(32-40); Meta(24-28)
Rules=LeftMargin~120; Logo=BR; NoClutter

L03:
Intent=SectionBreak
Grid=Center
Content=Label(28,Muted); Title(56-72)
Rules=OnlyThese2; MaxWhitespace

L04:
Intent=KeyStatement
Grid=Center
Content=Statement(48-64,Max2Lines); OptionalAttribution(28)
Rules=Only1Message

L05:
Intent=Concept+Visual
Grid=2col(50/50)
Left=Title(40-48)+Body(28-32,Max4Lines)
Right=Image
Rules=Gap>=40; CenterY; NoOverflow

L06:
Intent=Concept+Visual
Grid=2col(50/50)
Left=Image
Right=Title(40-48)+Body(28-32,Max4Lines)
Rules=Mirror(L05)

L07:
Intent=3Pillars
Grid=3col
Each=Visual+Label(32)+Desc(24,Max2Lines)
Rules=EqualWidth; SameTopY; Gap=30-50

L08:
Intent=Compare2
Grid=2col
Each=Heading(32-40)+Points(28,2-4)
Rules=BalancedContent; Gap=40-60

L09:
Intent=SingleKPI
Grid=CenterStack
Content=Label(28,Muted); Number(120-200); Context(28-32)
Rules=NumberIsHero; NothingCompetes

L10:
Intent=TwoKPIs
Grid=2col
Each=Number(96-120)+Label(28)
Rules=EqualWeight

L11:
Intent=ThreeKPIs
Grid=3col
Each=Number(72-96)+Label(28)
Rules=SameBaseline

L12:
Intent=Quote
Grid=CenterStack
Content=Quote(36-48,Max3Lines); Attribution(24-28)
Rules=GenerousPadding

L13:
Intent=Process
Grid=Row(3-5Steps)
Each=Icon/Number+Label(32)+Desc(24,1Line)
Rules=EqualSpacing; SameBaseline

L14:
Intent=HeroImage
Grid=FullBleed
Content=OverlayTitle(56-72)+Subtitle(28-32)
Rules=DarkOverlay; HighContrast

L15:
Intent=Matrix4
Grid=2x2
Each=Heading(32)+Desc(24)
Rules=EqualCards; Gap=20-30

L16:
Intent=IconRow
Grid=Row(3-4)
Each=Icon+Label(32)+Desc(24,1-2Lines)
Rules=SameIconSize; AlignBaselines

L17:
Intent=Data+Insight
Grid=Stack
Content=Chart(~60%H); Insight(28-32,Bold)
Rules=1Highlight; NoChartJunk

L18:
Intent=BeforeAfter
Grid=2col+Arrow
Left=Before(Muted)
Right=After(Strong)
Rules=ClearContrast

L19:
Intent=List
Grid=Stack
Content=Title(48); Items(28-32,3-5)
Rules=NoWrap; LargeGaps

L20:
Intent=Closing
Grid=CenterStack
Content=Headline(56-72); Sub(28-32); Contact(24-28)
Rules=Clean; FinalImpression

OPENING & CLOSING SLIDES:
- First and last slides are STATEMENTS — emotional, not informational.
- Combine a strong visual with powerful words. Image + text working together.
- These set the tone (opening) and leave the lasting impression (closing).
- Aim for feeling, not facts.

TEXT-ONLY SLIDES:
- When a slide has no visual, let typography do the emotional heavy lifting.
- Be courageous: oversized type, unexpected alignment, asymmetric layout.
- Break the grid if it serves the message. Unusual ≠ unreadable.
- The text IS the visual — treat it as such.

IMAGES:
- Optional: generate an image that captures the feeling or mood of the slide.
- Best for: cover slides, section breaks, closing slides, concept+visual layouts.
- Style: photo or graphic render — must match the active style guide's palette, mood, and aesthetic.
- The image should evoke emotion, not illustrate literally. Abstract > obvious.
- Pick one style per deck and stay consistent (all photo or all render).
- Pull colors, textures, and tone from the style guide — the image should feel native to the deck.
- Photo: cinematic, high-quality, shallow depth-of-field or dramatic lighting.
- Render: 3D, isometric, gradient mesh, or stylized illustration — bold and clean.
- Avoid: generic stock, clip art, overly busy compositions, text inside images.
- Image should complement the message — never compete with it.
- Use as background (with overlay) or as a contained visual in a split layout.

SELECTION:
- Opening: L01,L02 (emotional statement + visual)
- Section: L03
- Statement/Quote: L04,L12
- Concept+Visual: L05,L06,L14
- Features: L07,L16
- Compare: L08,L18
- KPI: L09,L10,L11
- Process: L13
- Matrix: L15
- Data: L17
- List: L19
- Closing: L20 (emotional statement + visual)

OUTPUT RULES:
- Be concrete.
- No theory, no filler.
- Use sizes, spacing, alignment explicitly.
- If unclear: ask <=3 questions OR list <=5 assumptions.



---

## 附：workflows（详细参考）


### workflows/slides-workflow.md

# Slides Creation Workflow

This is the end-to-end workflow for creating presentation decks on the ardot canvas. Follow the phases in order — do NOT skip phases or reorder steps.

## Execution Protocol (read first, applies to entire workflow)

1. **Sequential Lock**: Phases 0→4 are strictly sequential. You MUST NOT
   start Phase X+1 until Phase X's Postconditions are all satisfied.
2. **State Echo**: At the start of each Phase, output a one-line status:
   `[Phase X.Y] <name> — preconditions: PASS/FAIL`
3. **No Silent Skips**: If you intentionally skip a step (e.g., 3.1 for
   fresh files), explicitly state the skip reason.
4. **No Fabrication**: Never invent values that should come from a tool
   call (palette, file ID, slide count). If a tool call is missing, halt.

## Table of Contents

- [Phase 0: Requirement Clarification](#phase-0-requirement-clarification) — resolve sources, scenario, style, slide count, generate `slide-outline.md`
- [Phase 1: Preparation](#phase-1-preparation) — read references, fetch state, guidelines, style guide
- [Phase 2: Planning](#phase-2-planning) — slide count, outline, canvas grid
- [Phase 3: Canvas Setup](#phase-3-canvas-setup) — locate space, create all slides
- [Phase 4: Slide Generation](#phase-4-slide-generation) — per-slide generate → layout check → fix
- [Phase 5: Final Review](#phase-5-final-review) — holistic visual pass

---

## Phase 0: Requirement Clarification (MANDATORY)

**Run before any MCP call.** Resolve four inputs from the current request, prior messages, attachments, and referenced materials:

- **Content source**: provided materials, web research, or free creation.
- **Scenario**: audience, purpose, and delivery context.
- **Style**: visual direction or references.
- **Slide count**: explicit count or count implied by an approved outline.

User-provided information is already confirmed. Do not repeat questions or reopen a clear choice.

If anything material is missing or ambiguous, use **one AskUserQuestion call** containing only those gaps. If nothing is missing, skip the question and continue. Defaults are allowed only when the user delegates a choice or says they are unsure; record the assumption briefly.

When clarification is required:

- For an unresolved source, offer: provided materials / web research / free creation.
- For an unresolved style, offer exactly 5 concise, topic-specific directions; preserve any style or reference already supplied.
- For a delegated slide count, use the scenario range: pitch 10–12, internal review 6–8, keynote 15–20, training 8–15, quick share 5–7.
- Reconfirm only a slide count outside 3–30.

Do not enter Phase 1 until all four inputs are resolved and `slide-outline.md` is generated.

### 0.1 Generate `slide-outline.md`

Synthesize the four resolved dimensions into an outline file and **save it to the project workspace** (alongside the design file or in the conversation working directory) as `slide-outline.md`. This file is referenced in Phase 2.1 and Phase 4.1 — do **not** skip generation.

The file MUST follow this structure exactly:

```markdown
# Slide Outline

## Meta
- Topic: <one-sentence topic>
- Scenario: <resolved scenario>
- Content Source: <provided | web research | free creation>
- Style: <chosen style name> — <2–3 sentence palette/type/motif direction>
- Slide Count: <N>
- Generated At: <ISO timestamp>

## Source Materials
<If "provided": list/summarize the user-supplied materials.
If "web research": list the planned search queries and target source types.
If "free creation": write "N/A — generated from topic knowledge.">

## Slide-by-Slide Outline
1. **Slide 1 — <Role: Cover>** — <one-line message> | Layout hint: L?? | Image provided: yes/no | Chart: yes/no
2. **Slide 2 — <Role: Agenda>** — <one-line message> | Layout hint: L?? | Image provided: yes/no | Chart: yes/no
... (continue for all N slides)

## Visual Rhythm Notes
- Chart-bearing slides at positions: <e.g., 4, 6>
- Slides are viewed on large screens, often from a distance. Small text is unreadable. Every text element must be sized generously and fill its container space rather than floating in emptiness.
- Slides should feel visually rich and layered, not like text pasted on rectangles. Decorative elements add polish, and SVG charts communicate data far more effectively than text or numbers alone.
```
### Ask User Confirmation(Gated)

Show the generated outline to the user and **use the AskUserQuestion tool** to ask for explicit confirmation ("approve / revise"). If the user requests changes, update the file and re-confirm. **Only after explicit approval may you advance to Phase 1.**

---

## Phase 1: Preparation (MANDATORY)

Before doing anything on the canvas, load all required knowledge and context. This phase is non-negotiable — skipping it will produce low-quality slides that violate design rules.

### 1.0 Ensure Design File Is Open

**Follow `ardot-design-core` SKILL.md → Step 0** for the full file-open rule — including the injected
`<ardot_file_directive action="create|open">` main path, the **at-most-one** `create_design` / `open_design`
idempotency hard rule, the async-load wait gate (never re-issue to "confirm"), and the empty-canvas note
(root `0:1`, skip `fetch_editor_state`). Do not re-derive create vs. open here.

**Slides-specific deviation — deferred `fetch_file_info`:** on the `create_design` branch, defer
`fetch_file_info` until **1.4's MCP batch** (not "Step 6"). Steps 1.1/1.3 are local file reads (no MCP)
and 1.2 is skipped for fresh files, so they cover the async load window; issue `fetch_file_info` alongside
`search_style_guide` in 1.4. On the `open_design` branch, call `fetch_file_info` right after the file is ready (before 1.2).

### 1.1 Load Reference Knowledge (read these files if not already loaded)

> These two files live in the **ardot-design-core** skill (injected alongside this one). Read them from the core skill's root directory — its absolute path is provided in the same prompt that injected this skill.

- `<ardot-design-core>/rules/design-rules.md` — ardot design constraints (flexbox, text, components, property reference)
- `<ardot-design-core>/workflows/ardot-workflow.md` — `batch_edit` operation syntax, binding rules, full tool parameters

### 1.2 Fetch Editor State

**Skip this step for fresh `create_design` files** — empty canvas, root is `0:1`, no variables, no components yet. There is nothing to fetch.

Otherwise (opened existing file / file already loaded), call `fetch_editor_state` with `includeSchema: false` to get:
- Current page ID
- Active selection
- Available components in the file

### 1.3 Load Slide Design Guidelines

Load `{SKILL_ROOT}/references/guidelines-slides.md` — this is the **authoritative source** for all slide design rules, including:
- **Rule 1**: Large Typography (Title ≥56px, Body ≥28px, no text below 22px)
- **Rule 2**: Rich Backgrounds (gradients + decorative patterns, no pure white/black)
- **Rule 3**: Decorative Elements & SVG Data Charts (≥2 decorative elements per slide)
- 20 Layout Contracts (L01-L20) for different slide types
- Color, imagery, and content density guidelines

These rules are enforced throughout Phase 2-5. References to "Rule 1/2/3" in later phases point to this file.

### 1.4 Fetch Visual Style Inspiration

**If the user provided explicit style guidance OR `<ardot_design_style>` is present**, SKIP call `search_style_guide`/`build_style_guide`. For `<ardot_design_style>` specifically, follow the injected `[style-template]` instruction to `curl` the template md and use that as the complete style guide.

1. Call `search_style_guide` with keywords extracted from the deck's topic and tone (e.g. `styleKeywords: "corporate presentation modern"`). **For the `create_design` branch, issue `fetch_file_info` in the same parallel batch** (this is the deferred call from 1.0 — safe now because 1.1/1.3 ran in between).
2. Review the returned candidates, select best fit per domain
3. Call `build_style_guide` with your selections to get the complete design system
4. If the returned style does not fit the topic or contradicts the approved style, call `search_style_guide` again with adjusted keywords or `true` for full catalog, or make your own style guide

**Output of Phase 1**: a concrete color palette, type scale, spacing tokens, and decorative motif to apply consistently across all slides.

## Phase 2: Planning

Plan the deck before touching the canvas. Do NOT start creating slides until planning is complete.

### 2.1 Determine Slide Count & Outline

**Read `slide-outline.md` (generated in Phase 0.1) — it is the authoritative source for slide count, roles, and per-slide messages.** Do not invent a new count or re-decide roles here.

From the outline file, extract:
- Total slide count `N` (Meta → Slide Count)
- Per-slide role and one-line message (Slide-by-Slide Outline)
- Dark accent slide positions (Visual Rhythm Notes) — must satisfy guidelines-slides.md Rule 2 (at least 1–2 dark slides)
- Chart-bearing slide positions (Visual Rhythm Notes) — must align with guidelines-slides.md Rule 3

If the outline lacks any of the above, return to Phase 0.1 and amend it before proceeding.

### 2.2 Plan Canvas Grid Layout

Each slide is **1920 × 1080** px. Lay slides out on the canvas in a grid:
- **Max 5 slides per row**
- Horizontal gap between slides: **100px**
- Vertical gap between rows: **100px**

For N slides:
- `rows = ceil(N / 5)`
- `totalWidth = min(N, 5) × 1920 + (min(N, 5) - 1) × 100`
- `totalHeight = rows × 1080 + (rows - 1) × 100`

---

## Phase 3: Canvas Setup (MUST READ AND FOLLOW)

### 3.1 Locate Available Space

Call `locate_available_space` with `totalWidth` and `totalHeight` from Phase 2.2. Record the returned `space.x` and `space.y` as the grid origin.

### 3.2 Create All Slides in One Batch (MUST READ AND FOLLOW)

Use `batch_edit` (≤25 ops per call; split into multiple calls if N > 25) to create all slides up front.

For slide at grid position `(row, col)` where `row = floor(i / 5)` and `col = i % 5`:
- `x = space.x + col × (1920 + 100)`
- `y = space.y + row × (1080 + 100)`

Each slide node must set:
- `width: 1920, height: 1080`
- `clipsContent: true`
- Meaningful `name` (e.g., `"Slide 3 - Market Size"`)
- Base `fill` from the style guide (avoid pure white/black — see guidelines-slides.md Rule 2)

Example:
```javascript
slide1=I("pageId", {type: "slide", name: "Slide 1 - Cover", width: 1920, height: 1080, clipsContent: true, x: X1, y: Y1, fills: [/* gradient from style guide */]})
slide2=I("pageId", {type: "slide", name: "Slide 2 - Agenda", width: 1920, height: 1080, clipsContent: true, x: X2, y: Y2, fills: [/* gradient from style guide */]})
// ... continue for all N slides
```

**Do NOT populate slide content in this phase.** Only create the empty slides.
**Don't use `type: "frame"`, use `type: "slide"` to create root slides.**

---

## Phase 4: Slide Generation

Generate slides **one at a time, sequentially**. For each slide `i` from 1 to N, run this sub-loop:

### Language Rules

> ⛔ **HARD RULE — OUTPUT LANGUAGE.** When the user has not explicitly specified a language, all generated on-canvas content (titles, body copy, labels, captions, annotations, etc.) MUST default to the language of the user's prompt, preserving any embedded English / foreign-language terms exactly as written (do not translate them). When the user explicitly specifies a target language (e.g., "use English", "用日文"), default all generated content to that language instead. This rule applies to every textual element produced on the design canvas.
>
> 🔔 **MANDATORY PRE-GENERATION ANNOUNCEMENT — NON-NEGOTIABLE.** The moment the design language is determined and BEFORE issuing the first content-producing `batch_edit`, you MUST send the user a one-line notice stating which language the on-canvas content will use (e.g., `本次幻灯片内容将使用中文生成` / `Generating the Slide content in English`). This announcement is REQUIRED on every single design task — never skip it, never defer it, never bury it inside other text.

### 4.1 Generate Slide Content

Before generating slide `i`, re-read its row in `slide-outline.md` (role, one-line message, layout hint, dark/chart flags) and treat it as the spec for this slide.

Call `batch_edit` (≤25 ops) to add content into slide `i`'s frame. Build order:
1. **Background layer** — gradient fill + decorative pattern (Rule 2)
2. **Structure** — title area, content area, footer/page number
3. **Content** — titles, body text, data elements (with font sizes from Rule 1)
4. **Decoration** — 2–3 decorative elements per slide (Rule 3)
5. **Charts** — if data is present, inline SVG chart (Rule 3)

If a single slide needs more than 25 ops, split into multiple sequential `batch_edit` calls on the same slide.

### 4.2 Verify & Fix

1. `capture_layout` on slide `i` with `problemsOnly: true` — check overlapping, clipping, misalignment
2. If issues found → `batch_edit` corrections → re-run 1 + 2

Only advance to slide `i+1` after the current slide passes both checks.
**IMPORTANT: Do not call `capture_screenshot` before Phase 5.**
---

## Phase 5: Final Review

After all N slides have passed their per-slide checks:

### 5.1 Deck-Level Screenshot

Screenshot each slide one more time to compare neighbors side-by-side. Verify:
- Consistent palette across all slides (Rule 2)
- Dark accent slides appear at planned positions
- Typography hierarchy is uniform (same title/body sizes across similar slide types)

### 5.2 Final Fixes

For any cross-slide inconsistencies, issue fix-up `batch_edit` calls and re-screenshot. Stop only when the full deck is visually cohesive.
