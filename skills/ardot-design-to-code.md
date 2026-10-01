# ardot-design-to-code

> Use this skill for Ardot canvas tasks that convert a design into frontend code, or extract a design system / style guide from a website. Covers: design-to-code, design → HTML/CSS/JS, export as webpage

## 使用说明（重要）

本技能从 WorkBuddy 导入。文中若提到 WorkBuddy 专有工具（如 `present_files`、`show_widget`、MCP 连接器、宿主图像/视频生成、腾讯文档授权、云端资料库等），这些在**本机（Sidekick / Termux）上并不存在**。遇到这类步骤，请改用本机已有能力实现，或直接跳过并说明原因。

**本文档的价值在于方法论与规范**（怎么写、怎么排版、怎么避坑）——照此执行即可，不必强行还原 WorkBuddy 的实现方式。

# Ardot Design-to-Code & Style Extraction

Domain workflows for **design → frontend code** conversion and **website → style-guide** extraction on the Ardot canvas.

> **The general workflow and hard rules live in `ardot-design-core`**, which is injected alongside this skill. Follow `ardot-design-core` for the step sequence, schema, editing rules, effects, and screenshot verification. This skill carries the **specialized conversion/extraction workflows and implementation guidelines**.

## Specialized Workflows (follow strictly — do NOT improvise the procedure)

- **Design → frontend code** → `{SKILL_ROOT}/workflows/design-to-code-workflow.md` — design → HTML/CSS/JS, generate Application, to code, slide transitions, responsive scaling.
- **Website → style guide extraction** → `{SKILL_ROOT}/workflows/extract-style-guide-from-web.md` — pull a design guide / tokens from an existing website.

## Implementation Guidelines (load alongside a design-type guideline when generating code)

- `{SKILL_ROOT}/references/guidelines-code.md` — design-to-code implementation rules.
- `{SKILL_ROOT}/references/guidelines-tailwind.md` — Tailwind v4 implementation (load alongside `guidelines-code.md`).

> Files referenced by these workflows that live in the core skill are read from the **ardot-design-core** skill root — its absolute path is provided in the same prompt that injected this skill. This includes `design-rules.md` etc. and the shared **tool-usage guides** (`tool-usage/batch-edit.md`, `tool-usage/apply-variables.md`), which now live in `ardot-design-core` because `batch_edit` / `apply_variables` are used by every Ardot task.


---

## 附：references（详细参考）


### references/guidelines-code.md

# Instructions when generating code from design files

- IMPORTANT: Make sure to use the frontend frameworks that are already used in the project. For example, if the project is using React, always generate compliant React code.
- IMPORTANT: After generating code, DO NOT output Markdown files of the changes. Just stick to generating code and nothing else.
- IMPORTANT: Make sure to use and leverage the CSS libraries, design systems and other UI coding utilities that are already used in the project. For example, if the project is using Tailwind, make sure to style your code using Tailwind.
- IMPORTANT: Make sure when using CSS libraries and frameworks that you identify the installed version and always use the correct APIs that are supported by the installed versions.
- IMPORTANT: When generating code from .ardot designs, always make sure to use the same text labels, icons ans spacing as what is in the design.
- DO NOT create documentations for the changes when generating code from design.
- Explore the workspace to find if the design elements you are translating to code are already exist in the code base.
- Make sure to awlays use the correct font, icons, and UI details like border radius when generating the code from a design.
- If you are not sure what frontend frameworks and UI libraries are used in the project, explore it in the workspace.
- If the UI design element you are turning code into already exist in the codebase, update it, not generate a new one.
- When changing existing components and UI elements in the code, make sure to not break the functionality.

## Initial Setup

### Project Initialization

- Identify the frontend framework and language used in the project (e.g., React, Vue, Angular, Svelte, etc.)
- Use the same framework, language, and conventions as the existing project
- Identify the styling approach (e.g., Tailwind, CSS Modules, styled-components, etc.)
- If using Tailwind, refer to 'tailwind' topic for implementation details

### Pre-Implementation Verification

- Ensure CSS/styles compile without errors
- Verify all CSS variables are accessible (if using CSS custom properties)
- Confirm styling system is properly configured and loaded

## Component Implementation Workflow

### Step 1: Component Analysis and Extraction

#### 1A. Identify Required Components

- Read the target frame/design
- Identify which reusable components (refs) are used in this specific frame
- **IMPORTANT**: Only process components that appear in the current frame
- Count instances of each component (helps catch missing instances later)
- Document: "Component X used N times"

#### 1B. Extract Component Definitions

- Use `batch_read` to get component structure
- Extract full component tree with all nested children
- Process components ONE AT A TIME:
  1. Extract component with full depth
  2. Recreate in React (Step 2)
  3. Validate (Step 3)
  4. Move to next component only after validation passes

#### 1C. Map Component Instances

- Read the target frame structure
- For each component, identify ALL instances
- Document for each instance:
  * Instance ID and location
  * Nested component overrides (`descendants` map)
  * Props/values being passed
- **Nested Component Analysis**:
  * Check base component definition: Does it always include nested components?
  * Check all instances: Do any override/hide nested components?
  * **Decision Rule**:
    - If NO instances override away → Nested component is REQUIRED (always render)
    - If ANY instances override away → Nested component is OPTIONAL (conditional render)
  * Verify each nested component ref in base definition against all instances
- **Visual Verification**:
  * Use `capture_screenshot` on instances in context (not just base definition)
  * Verify visible elements (borders, backgrounds, shadows)
  * Check if styling should be on outer container or nested elements
  * Match visual appearance in frame context

### Step 2: React Component Creation

#### Component Structure

- Create `.tsx` file in `src/components/` with component name
- Use named exports
- Define TypeScript interfaces for all props

#### Props Interface Design

- Review ALL instances from Step 1C mapping
- Support all properties used by any instance (including optional ones)
- **Nested Component Rendering**:
  * Apply decision rule from Step 1C:
    - NO instances override away → Always render (required)
    - ANY instances override away → Conditional render (optional)
  * Verify against instance mapping before making props optional
- Document required vs optional props based on actual usage
- Cross-reference with instance mapping to ensure completeness

#### Style Implementation

- Use Tailwind classes exclusively (NO inline styles)
- Refer to guidelines-tailwind.md sections: "Layout Conversion", "Style Implementation", "CSS Custom Properties and Font Stacks"
- Match design values exactly (use arbitrary values when needed)
- Use CSS variables for colors (no hardcoded values)

#### SVG Path Implementation

When implementing SVG elements from the design:

**1. Extract Exact Geometry**
- Use `batch_read` with `includePathGeometry: true`
- NEVER approximate paths - extract exact `geometry` property from design

**2. Properties to Extract**
- `geometry` - use as `d` attribute in `<path>`
- `fill` - convert design variables to CSS variables (e.g., `$primary` → `var(--primary)`)
- `stroke` properties if present (`strokeColor`, `strokeThickness`)
- `width` and `height` for viewBox calculation

**3. Implementation**
- Use exact geometry string in `d` attribute
- Set `viewBox="0 0 {width} {height}"`
- Preserve all stroke properties
- For styling, see guidelines-tailwind.md "SVG Styling" section for Tailwind-specific syntax

**4. Logos and Complex Icons**
- Extract complete geometry even if very long
- Don't simplify or approximate
- Maintain precision for brand assets

### Step 3: Component Validation

1. **Visual Verification**:
   - Use `capture_screenshot` on design component
   - Compare with rendered React component
   - Verify pixel-perfect match

2. **Style Verification**:
   - Inspect computed CSS properties
   - Verify dimensions, spacing, colors, typography match design
   - Ensure CSS variables resolve correctly

3. **Behavior Verification**:
   - Test fill_container elements expand properly
   - Test hug_contents elements size to content
   - Verify no overflow issues

4. **Iterative Fixing**:
   - Fix discrepancies immediately
   - Re-validate after each fix
   - Only proceed to next component when current is perfect

### Step 4: Frame Integration

#### Pre-Integration Analysis

- Read complete target frame with `maxDepth: 10`
- Map component tree structure
- Identify all component instances

#### Instance Configuration

- Document all property overrides for each instance
- Verify nested component overrides
- Create instance mapping with exact props
- **Layout Context**:
  * Check parent container layout mode
  * If flex container with multiple `fill_container` children → each needs `flex-1`
  * Document which components need `flex-1` based on parent layout

#### Completeness Verification

- Count component instances in design vs implementation
- Verify all props match design overrides
- Confirm nested components follow required/optional decision from Step 1C
- Use checklist:
  * [ ] All instances accounted for
  * [ ] All props match overrides
  * [ ] Nested components render correctly (always vs conditional)
  * [ ] Layout classes applied correctly (`flex-1`, etc.)

### Step 5: Final Validation

- Verify component positions and spacing match design
- Verify colors resolve correctly
- Verify typography matches
- Verify responsive behavior:
  * Layout adapts to different viewport sizes
  * Scrollable areas work when content exceeds space
  * No horizontal overflow
  * `fill_container` elements expand properly
  * `hug_contents` elements size to content
- Verify no console errors
- Verify all interactive elements function correctly

## Key Principles

- Use the project's styling system consistently (avoid inline styles when possible)
- If using Tailwind, see guidelines-tailwind.md for Tailwind-specific implementation details
- Match design values exactly
- Use the project's color system (CSS variables, design tokens, theme files, etc.) - avoid hardcoded values
- Process components one at a time with validation
- Verify nested component rendering requirements
- Ensure proper styling and layout based on parent context


### references/guidelines-tailwind.md

# Tailwind v4 Implementation Guidelines

This document provides Tailwind v4 specific guidelines for implementing .ardot designs in code.

**NOTE**: These guidelines are specific to Tailwind v4. If you are deliberately using an older version of Tailwind (v3 or earlier), you may bypass the v4-specific syntax rules (such as `@import "tailwindcss";` vs `@tailwind` directives) and adapt accordingly.

## Core Principle

**Use Tailwind classes exclusively throughout - NEVER use inline styles for any property (sizing, colors, spacing, typography, etc.).**

## CSS Variables Setup

### Structure of globals.css

Your `globals.css` should follow this structure:

```css
@import "tailwindcss";

:root {
  /* Design variables from .ardot file - ONLY single values */
  --color-primary: #3b82f6;
  --color-secondary: #8b5cf6;
  --spacing-base: 16px;
  /* DO NOT store font stacks here */
}

@layer base {
  html, body {
    height: 100%;
  }

  /* Font family utilities - Define font stacks directly here */
  .font-primary {
    font-family: "Inter", sans-serif;
  }

  .font-secondary {
    font-family: "JetBrains Mono", monospace;
  }
}
```

### Guidelines

- Read design variables using `fetch_variables`
- Convert to CSS custom properties in `:root` block for single values only (colors, numbers, keywords)
- Map all design variables using exact names from design file
- **IMPORTANT**: Use `:root` block for design variables (NOT `@theme` - Tailwind v4's `@theme` only supports custom properties and `@keyframes`)
- **DO NOT add manual resets** - `@import "tailwindcss";` includes Preflight automatically
- **CRITICAL for Next.js projects**: If using `next/font` loaders, DO NOT re-wrap their CSS variables (like `--font-geist`) in your `:root` block. Instead, reference them directly in `@layer base` utility classes (see Font Loading section)

## Font Implementation

### Core Rules

**CSS variables work for single values only** (colors, numbers, keywords). **DO NOT use them for font stacks.**

❌ **WRONG**:
```css
:root {
  --font-primary: "JetBrains Mono", monospace;  /* Breaks with comma-separated values */
}
```

✅ **CORRECT**: Define fonts in `@layer base` utility classes:
```css
@layer base {
  .font-primary {
    font-family: "JetBrains Mono", monospace;
  }
  
  .font-secondary {
    font-family: "Inter", sans-serif;
  }
}
```

### Next.js Font Loaders

When using `next/font/google` or `next/font/local`:

❌ **NEVER wrap Next.js font variables in `:root`**:
```css
/* WRONG - nested var() references break */
:root {
  --font-primary: var(--font-geist);
}
```

✅ **DO reference them directly in utility classes**:
```css
@layer base {
  .font-primary {
    font-family: var(--font-jetbrains-mono), "JetBrains Mono", monospace;
  }
}
```

### Implementation Workflow

1. Read font names from design using `fetch_variables`
2. Load fonts via `<link>` tags OR Next.js font loaders in layout.tsx
3. Create utility classes in `@layer base` (`.font-primary`, `.font-secondary`)
4. Use classes in components: `className="font-primary"`
5. **NEVER use** `font-[var(--font-name)]` or inline styles for fonts

## Font Loading

### Tailwind v4 Requirements

❌ **NEVER in Tailwind v4**:
- `@import url()` in CSS files
- `font-[family-name:var(...)]` syntax
- `--turbopack` flag

✅ **Load fonts via**:
- `<link>` tags in layout.tsx `<head>`, OR
- Next.js font loaders (`next/font/google`, `next/font/local`)

### Examples

**Option 1: Manual loading**
```tsx
// layout.tsx
<head>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet" />
</head>
```

**Option 2: Next.js font loaders**
```tsx
// layout.tsx
import { JetBrains_Mono } from "next/font/google";

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
});

export default function RootLayout({ children }) {
  return (
    <html>
      <body className={jetbrainsMono.variable}>
        {children}
      </body>
    </html>
  );
}
```

```css
/* globals.css */
@layer base {
  .font-primary {
    font-family: var(--font-jetbrains-mono), "JetBrains Mono", monospace;
  }
}
```

## Icon Font Setup

- **If design uses `icon_font` nodes**:
  1. Add Google Fonts link in layout.tsx `<head>` (following the Font Loading Rules above)
  2. Add utility class in `@layer base` section of globals.css with appropriate font-feature-settings
  3. Render as `<span>` elements with icon name as text content
  4. Use inline styles for font-weight if needed (e.g., `style={{ fontWeight: 100 }}`)
  5. **NEVER use `@font-face`** - Always use CDN links

## Viewport Setup

- Add `height: 100%` to `html` and `body` in `@layer base` section of globals.css (as shown in CSS Variables Setup example)
- Add `h-full` class to `<html>` and `<body>` in layout.tsx
- Ensures viewport-relative sizing works throughout app
- Design dimensions are specifications, not fixed constraints
- **DO NOT use wildcard selectors** - use the `@layer base` approach shown above

## Tailwind v4 Import and Preflight

### Correct Import Syntax

**Tailwind v4 uses a simplified import syntax** in `globals.css`:

```css
@import "tailwindcss";
```

This single import automatically includes:
- Base styles (Preflight reset)
- Component classes
- Utility classes

**DO NOT use the old v3 syntax**:
```css
/* ❌ WRONG - This is v3 syntax */
@tailwind base;
@tailwind components;
@tailwind utilities;
```

### Preflight Reset Behavior

The `@import "tailwindcss";` automatically includes Preflight, which:
- Removes margins and padding from all elements
- Sets `box-sizing: border-box` on all elements
- Resets headings and lists (they inherit font properties)
- Makes images block-level with responsive sizing

### Critical Rules

- **NEVER manually add global resets** - Preflight handles everything
- **NEVER use wildcard selectors** like `* { margin: 0; padding: 0; }` in globals.css
- **DO NOT duplicate Preflight functionality** - it's already included
- Use `@layer base { ... }` ONLY for additional custom base styles that don't conflict with Preflight

## Layout Conversion

### Container Sizing

- Root containers: `h-full w-full` or `h-screen w-screen` (NOT fixed dimensions)
- Fixed dimensions only for specific elements (e.g., sidebar: `w-[280px]`)

### fill_container Translation

- In flex containers: use `flex-1`
- For explicit sizing: `w-full` (width), `h-full` (height)
- **IMPORTANT**: `h-full` requires parent chain has height set
- For scrollable containers: `flex-1 overflow-auto`
- **NEVER use inline styles** for sizing
- **Multiple fill_container Children**:
  * In flex containers, multiple children with `fill_container` → each needs `flex-1`
  * Applies to both horizontal and vertical flex layouts
  * Distributes space equally among children
- **Height fill_container**:
  * ANY component with `height: "fill_container"` MUST have `h-full` class
  * Applies universally regardless of component type or parent layout
  * Verify every `fill_container` height has corresponding `h-full` class

### hug_contents Translation

- Use `w-fit` (width), `h-fit` (height)
- NEVER use inline styles

### Flex Context

- Parent must be flex container for `flex-1` to work
- Use `min-h-0` on flex children that need to shrink below content size
- Scrollable flex children: `flex-1 overflow-auto`

### Verification

- Check ALL `fill_container`/`hug_contents` converted to Tailwind classes
- Ensure NO inline styles for width/height

## Style Implementation

Use **Tailwind classes exclusively** (NO inline styles) for all styling:

### 1. Layout

- Position: `relative`, `absolute`, `fixed`, `sticky`
- Display: `flex`, `flex-col`, `grid`, `block`, `inline-block`
- Alignment: `items-center`, `justify-between`, etc.
- Gap: `gap-4`, `gap-[16px]` (match design exactly)

### 2. Spacing

Match design values exactly:
- Padding: `p-4`, `px-6`, `pt-[12px]`, etc.
- Margin: `m-4`, `mx-auto`, `mt-[8px]`, etc.
- Use arbitrary values `[Npx]` when needed

### 3. Dimensions

- Width: `w-[280px]` (fixed), `w-full` or `flex-1` (fill_container), `w-fit` (hug_contents)
- Height: `h-[48px]` (fixed), `h-full` or `flex-1` (fill_container), `h-fit` (hug_contents)
- Min/max: `min-w-[200px]`, `max-h-[600px]`
- **CRITICAL**: Never use inline styles for dimensions

### 4. Colors and Borders

- Background: `bg-[var(--color-name)]` - NO hardcoded hex values
- Border: `border`, `border-2`, `border-[var(--color-border)]`
- Border radius: `rounded`, `rounded-lg`, `rounded-[12px]`
- Text: `text-[var(--color-text)]`
- Shadows: `shadow-sm`, `shadow-[custom]`

### 5. Typography

- **Font family**: Use utility classes defined in `@layer base` (see "CSS Custom Properties and Font Stacks" section above)
  - ✅ Correct: `className="font-primary"`
  - ❌ NEVER: `font-[var(--font-primary)]` (arbitrary value syntax doesn't work with CSS variables)
  - ❌ NEVER: `style={{ fontFamily: 'var(--font-primary)' }}` (avoid inline styles unless necessary)
  - For Next.js font loaders: Create utility classes that reference the Next.js variables, then use those classes
- Font size: `text-sm`, `text-[14px]`
- Font weight: `font-medium`, `font-[500]`
- Line height: `leading-normal`, `leading-[24px]`
- Letter spacing: `tracking-normal`, `tracking-[0.02em]`

### 6. Interactive States

- Hover: `hover:bg-[var(--color-hover)]`, `hover:opacity-80`
- Active: `active:scale-95`
- Disabled: `disabled:opacity-50`, `disabled:cursor-not-allowed`
- Focus: `focus:outline-none`, `focus:ring-2`

## SVG Styling

For SVG path extraction and implementation workflow, see guidelines-code.md "SVG Path Implementation" section.

### Tailwind-Specific SVG Styling

When styling SVG elements with Tailwind:

- **Fill colors**: Use `fill-[var(--color-name)]` with CSS variables
  - Example: `fill-[var(--primary)]`
- **Stroke colors**: Use `stroke-[var(--color-name)]`
  - Example: `stroke-[var(--border)]`
- **Stroke width**: Use `stroke-[2]` or arbitrary values `stroke-[1.5px]`
- **SVG sizing**: Use standard sizing classes `w-6 h-6` or arbitrary `w-[24px] h-[24px]`
- **NEVER use inline styles** - always use Tailwind classes or className with CSS variables

Example:
```tsx
<svg className="w-6 h-6 fill-[var(--icon-primary)]" viewBox="0 0 24 24">
  <path d="M12 2L2 7l10 5 10-5-10-5z" />
</svg>
```



---

## 附：workflows（详细参考）


### workflows/design-to-code-workflow.md

# Ardot Design-to-Frontend-Code Workflow

This document covers the complete workflow for pixel-perfect conversion of .ardot design files into frontend HTML/CSS/JS code. It applies to slide presentations, landing pages, showcase pages, and other multi-page/multi-screen designs.

## Workflow Overview

```markdown
Phase 0: Confirm target platform & stack (mandatory dialog)
    ↓
Phase 1: Design Analysis (read design file structure and style data)
    ↓
Phase 2: Asset Export (export image and SVG vector resources)
    ↓
Phase 3: Code Generation (HTML structure + CSS styles + JS interactions)
```

**After Code Generation, Do Not try to run the generated code, or run a server preview.**

---

## Phase 0: Platform Clarification (MANDATORY)

Before calling any Ardot tool, you **MUST** confirm the output strategy with the user through a structured multiple-choice dialog. Free-form questions are **forbidden** in this phase — always present concrete options.

### Default Strategy (Highest Priority)

**The default and ALWAYS preferred output is a single self-contained HTML file** (one `.html` file with inline `<style>` and `<script>`, external assets only for exported images/SVGs). Unless the user explicitly opts into a framework or multi-file structure, you MUST default to single-HTML output.

### Mandatory Rules

1. **MUST use the AskUserQuestion tool** — never output the questions as plain text.
2. **Ask all questions in a single call**: content source / use case / visual style / page count / (...).
3. **No default fallbacks** — unless the user explicitly says "you decide", any unanswered item blocks entry into Phase 0; do NOT silently fall back to "use Free creation if unsure".
5. **After the AskUserQuestion call**, supplement with a plain-text note describing your assumptions/recommendation rationale (for transparency), then wait for the user's reply.

### Step 0.1: Ask via Multiple-Choice Questions (MANDATORY)

You MUST use the tool (or the platform's equivalent structured-options UI) to present the following questions. **Do NOT ask these as free-text questions.** Each question must list explicit options with the recommended default clearly marked.

Ask the following four questions in a single batch:

**Q1. Output format?** (single-select)
- `单 HTML 文件（推荐 / 默认）` — one self-contained `.html` with inline CSS & JS
- `多文件 Web（HTML + 独立 CSS/JS）`
- `React 项目`
- `Vue 项目`
- `微信小程序`
- `Flutter`
- `React Native`
- `SwiftUI`
- `Jetpack Compose`
- `其他（请说明）`

**Q2. Target device / viewport?** (single-select)
- `跟随设计稿原始画布（推荐 / 默认）`
- `Desktop 1920×1080`
- `Desktop 1440×900`
- `iPad 1024×768`
- `Mobile iOS (375×812)`
- `Mobile Android (360×800)`

**Q3. Canvas strategy?** (single-select)
- `保留原始像素画布 + 等比缩放（推荐 / 默认，最像设计稿）`
- `自适应目标设备宽度（响应式重排）`
- `固定像素，不缩放`

**Q4. Stack details** (only show when Q1 is NOT `单 HTML 文件`; otherwise skip)
- Language: `TypeScript` / `JavaScript`
- Styling: `Tailwind` / `CSS Modules` / `SCSS` / `原生 CSS`
- UI library: `无` / `Ant Design` / `Material UI` / `其他`
- Output directory: ask user to specify path

### Step 0.2: Confirm and Lock Decisions

After collecting answers:

1. Summarize the locked decisions in **one line**, e.g.:
   > 已确认：单 HTML 文件 · 跟随设计稿原始画布 · 保留原画布 + 等比缩放。
2. Wait for **explicit confirmation** ("确认" / "OK" / "继续" 等) before proceeding to Phase 1.
3. If the user replies "随便" / "你决定" / 未明确选择 — apply the **default combination**: `单 HTML 文件 + 跟随设计稿原始画布 + 保留原画布 + 等比缩放`, restate it, and still wait for confirmation.
4. **A missing or ambiguous answer is a hard blocker** — do not proceed to Phase 1 with assumptions.
5. If the user later changes their mind, re-run Step 0.1 for the affected questions only.

### Single-HTML Output Rules (when Q1 = 单 HTML 文件)

When the default single-HTML path is chosen, the generated file MUST:
- Be a single `.html` containing `<!DOCTYPE html>`, `<head>` with inline `<style>`, and `<body>` with inline `<script>` if interactions exist.
- Reference exported images/SVGs via relative paths (e.g. `./assets/xxx.png`); do NOT inline base64 unless the asset is < 4 KB.
- Avoid build tools, bundlers, npm dependencies, and CDN frameworks unless the user explicitly requests them.
- Use vanilla HTML/CSS/JS only (no React/Vue/JSX).
- Be directly openable in a browser by double-clicking, with no server required.

## Phase 1: Design Analysis

### Step 1.1: Get Editor State

```markdown
fetch_editor_state() → understand canvas state, current page, top-level nodes, available components
```
Read `references/guidelines-code.md` to get code generation guidelines. (`references/guidelines-tailwind.md` optional for Tailwind)

**Key information to extract:**
- Identify each top-level frame as an independent page/slide
- Record each frame's `name`, `width`, `height`, `x`, `y`
- Understand page ordering (typically arranged left-to-right by x coordinate, top-to-bottom by y coordinate)

### Step 1.2: Scan Exportable Resources

```
scan_exportable_resources() → get all image nodes and SVG vector nodes
```

**Returns two lists:**
- `image`: nodes with image fills (export as PNG/JPEG/WEBP)
- `svg`: nodes with only vector/shape elements (export as SVG)

**Critical decision principles:**
- **Only export image nodes** (background images, scene images, character art, screenshots, etc.) — do NOT export entire slides/pages as images
- SVG decorative elements (corner decorations, dividers, icons, etc.) should be exported separately as SVG format
- Elements achievable with pure CSS (solid rectangles, gradients, simple borders) do not need exporting — reproduce them with code

### Step 1.3: Deep Read Each Page

Call separately for each page frame:

```markdown
batch_read(nodeIds: ["<frameId>"], readDepth: 10, resolveVariables: true)
```

**Read all pages in parallel** for efficiency. Extract the following key information:

| Category | Extraction Method | Purpose |
|----------|------------------|---------|
| Node hierarchy | Recursive `children` traversal | HTML DOM structure mapping |
| Layout mode | `layoutMode` (HORIZONTAL/VERTICAL/NONE) | CSS flex direction |
| Dimensions | `width`, `height` | CSS width/height |
| Positioning | `x`, `y`, `layoutPositioning` | CSS positioning method |
| Spacing | `itemSpacing`, `padding*` | CSS gap/padding |
| Alignment | `primaryAxisAlignItems`, `counterAxisAlignItems` | CSS justify/align |
| Clipping | `clipsContent` | CSS overflow |

### Step 1.4: Read Style Details

Call with `properties: [...]` for key nodes (text, decorative frames, backgrounds) to get complete styles:

```
batch_read(nodeIds: [...textIds, ...bgIds], readDepth: 0, properties: ["fills", "strokes", "effects", "cornerRadius", "opacity"])
```

**Required text node properties:**

| Property | CSS Mapping | Notes |
|----------|------------|-------|
| `fontSize` | `font-size` | Direct mapping |
| `fontName.family` | `font-family` | Note the font family name |
| `fontName.style` | `font-weight` | Regular→400, Medium→500, Bold→700 |
| `fills[0].color` | `color` | RGB 0~1 range, convert to hex |
| `letterSpacing.value` | `letter-spacing` | Note unit: PIXELS/PERCENT |
| `lineHeight` | `line-height` | AUTO or {value, unit} |
| `textAlignHorizontal` | `text-align` | LEFT/CENTER/RIGHT |
| `opacity` | `opacity` | Node-level opacity |
| `textAutoResize` | Affects wrapping behavior | HEIGHT → requires width to be set |

**Required frame/background node properties:**

| Property | CSS Mapping | Notes |
|----------|------------|-------|
| `fills` | `background` | SOLID→solid color, GRADIENT→gradient, IMAGE→background image |
| `fills[].opacity` | Fill opacity | Different from node opacity |
| `opacity` | `opacity` | Node-level opacity, affects entire element including children |
| `strokes` | `border` | Color, weight, position |
| `cornerRadius` | `border-radius` | |
| `effects` | `box-shadow`/`filter` | DROP_SHADOW/BLUR |
| `clipsContent` | `overflow: hidden` | |

### Step 1.5: Screenshot Each Page

```
capture_screenshot(nodeIds: ["<frameId>", ...]) → get visual references for all pages in one call
```

**Screenshot all pages in parallel.** Carefully analyze screenshots to understand:
- Overall color tone and atmosphere
- Element layering relationships
- Image positioning and cropping behavior
- Decorative element visual effects

---

## Phase 2: Asset Export

### Step 2.1: Create Output Directories

```bash
mkdir -p <project>/assets/images <project>/assets/svg
```

### Step 2.2: Export Image Assets

```
export_nodes(nodeIds: [...imageNodeIds], outputDir: "<project>/assets/images", format: "png", scale: 1)
```

**Key rules:**
- For SVG format there is no limit on the number of nodes per call. 
- For image formats (PNG/JPEG/WEBP), it is recommended to export no more than 5 nodes per batch, split into batches if exceeding
- Only export nodes from the `image` list returned by `scan_exportable_resources`
- **Do NOT export entire slide/page frames** — only export image nodes within them (backgrounds, scenes, etc.)
- Use `scale: 1` for background images (original size is sufficient; larger sizes slow down loading)
- Use `scale: 2` for small icons/graphics to maintain clarity
- If an image node export fails, reduce batch size and retry

### Step 2.3: Export SVG Vector Assets

call `export_nodes` with parameters (nodeIds: [...svgNodeIds], outputDir: "<project>/assets/svg", format: "svg")

**SVG export rules:**
- SVG always uses 1x scale (unaffected by the scale parameter)
- Suitable for SVG export: corner decorations, dividers, icons, emblems, arrows, etc.
- Not suitable for SVG export: nodes with image fills, complex lighting effects

### Step 2.4: Naming Conventions

Exported files automatically use `nodeId` as the filename (colons replaced with underscores), e.g., `2_5.svg`.
It is recommended to preserve semantic names in code via comments or variable names for maintainability:

```html
<!-- Corner Decor Top Left -->
<img src="assets/svg/2_5.svg" alt="">
```

---

## Phase 3: Code Generation

### Step 3.1: Project Structure

```markdown
<project>/
├── index.html          # Main page
├── style.css           # Stylesheet
├── script.js           # Interaction logic
└── assets/
    ├── images/         # PNG/JPEG images
    └── svg/            # SVG vectors
```

For complex projects, expand to:
```markdown
├── css/
│   ├── reset.css
│   ├── variables.css   # CSS variables
│   ├── slides.css      # Per-page styles
│   └── nav.css         # Navigation styles
├── js/
│   ├── main.js
│   └── slide-nav.js
```

### Step 3.2: CSS Variable Extraction

use `fetch_variables()` to extract variables from the design.
Extract frequently reused colors, fonts, etc. from the design and define as CSS variables:

```css
:root {
  /* Converted from fills[0].color RGB 0~1 values */
  --bg: #0D0A0F;           /* r:0.05, g:0.04, b:0.06 */
  --gold: #D4AF37;         /* r:0.83, g:0.69, b:0.22 */
  --white: #FFFFFF;
  --gray: #A0A0A0;         /* r:0.63, g:0.63, b:0.63 */
  --dark-gray: #666666;    /* r:0.40, g:0.40, b:0.40 */
}
```

**Color conversion formula:** `hex = Math.round(floatValue * 255).toString(16)`

### Step 3.3: Design-to-CSS Mapping Rules

#### Layout Mapping

| Ardot Property | CSS Property | Conversion Notes |
|---------------|-------------|-----------------|
| `layoutMode: "HORIZONTAL"` | `display: flex; flex-direction: row;` | |
| `layoutMode: "VERTICAL"` | `display: flex; flex-direction: column;` | |
| `layoutMode: "NONE"` | `position: relative;` (children use absolute) | |
| `itemSpacing` | `gap` | Direct pixel mapping |
| `primaryAxisAlignItems: "CENTER"` | `justify-content: center;` | |
| `primaryAxisAlignItems: "SPACE_BETWEEN"` | `justify-content: space-between;` | |
| `counterAxisAlignItems: "CENTER"` | `align-items: center;` | |
| `layoutGrow: 1` | `flex: 1;` | |
| `width: "fill_container"` | `flex: 1;` or `width: 100%;` | Context-dependent |
| `height: "hug_contents"` | `height: auto;` or `height: fit-content;` | |
| `clipsContent: true` | `overflow: hidden;` | |
| `padding: [top, right, bottom, left]` | `padding` | |

#### Text Mapping

| Ardot Property | CSS Property |
|---------------|-------------|
| `fontSize: 96` | `font-size: 96px;` |
| `fontWeight: "700"` | `font-weight: 700;` |
| `letterSpacing: {value: 24, unit: "PIXELS"}` | `letter-spacing: 24px;` |
| `lineHeight: {value: 36, unit: "PIXELS"}` | `line-height: 36px;` |
| `lineHeight: {unit: "AUTO"}` | (omit or `line-height: normal;`) |
| `textAlignHorizontal: "CENTER"` | `text-align: center;` |

#### Fill/Background Mapping

| Ardot Fill Type | CSS Implementation |
|----------------|-------------------|
| `type: "SOLID"` | `background-color: #hex;` |
| `type: "IMAGE"` + `scaleMode: "FILL"` | `background-image` or `<img>` + `object-fit: cover;` |
| `type: "GRADIENT_LINEAR"` | `background: linear-gradient(...)` |
| `type: "GRADIENT_RADIAL"` | `background: radial-gradient(...)` |
| Node `opacity: 0.35` with IMAGE fill | Set `opacity: 0.35;` on the wrapper container |

**Gradient conversion details:**

`gradientTransform: [[0, -1, 1], [1, 0, 0]]` represents a bottom-to-top direction.

Common gradientTransform to CSS direction mapping:
- `[[0, -1, 1], [1, 0, 0]]` → `linear-gradient(to top, ...)`
- `[[1, 0, 0], [0, 1, 0]]` → `linear-gradient(to right, ...)`
- `[[0, 1, 0], [-1, 0, 1]]` → `linear-gradient(to bottom, ...)`

In gradientStops, `color.a` controls color transparency, and `position` corresponds to the percentage position in CSS.

### Step 3.4: HTML Structure Generation

**Principle: Preserve the design's layer hierarchy**

```markdown
Design Frame Hierarchy     →    HTML Hierarchy
─────────────────────      ────────────────────
Slide 01 - Cover           <section class="slide slide-01">
  ├── Hero Background        <div class="hero-bg"><img></div>
  ├── Top Gradient           <div class="gradient-top"></div>
  ├── Center Content         <div class="center-content">
  │   ├── Subtitle Top          <p class="subtitle-top">...</p>
  │   ├── Main Title            <h1 class="main-title">...</h1>
  │   └── Tagline              <p class="tagline">...</p>
  └── Bottom Info            <div class="bottom-info">...</div>
```

**Naming rules:**
- Convert the design's `name` property to CSS class names (CamelCase to kebab-case)
- e.g., `"Center Content"` → `.center-content`
- e.g., `"Hero Background"` → `.hero-bg`

**Image references:**
- Image fill nodes → `<img src="assets/images/2_xxx.png">`
- SVG decorative nodes → `<img src="assets/svg/2_xxx.svg">`
- Pure CSS elements (gradients, solid rectangles) → no images needed, implement with CSS

### Step 3.5: Multi-Page/Slide Transition Effects

For slide-style designs, implement page transition effects:

**HTML structure:**
```html
<div class="slides-wrapper">
  <section class="slide slide-01">...</section>
  <section class="slide slide-02">...</section>
  ...
</div>
<div class="nav-controls">
  <button class="prev-btn">‹</button>
  <div class="slide-indicators">...</div>
  <button class="next-btn">›</button>
</div>
```

**CSS transition animation:**
```css
.slide {
  position: absolute;
  width: 1920px; height: 1080px;
  opacity: 0;
  transition: opacity 0.8s ease, transform 0.8s ease;
  pointer-events: none;
}
.slide.active { opacity: 1; pointer-events: auto; z-index: 2; }
.slide.prev { opacity: 0; transform: translateX(-100px) scale(0.95); }
.slide.next { opacity: 0; transform: translateX(100px) scale(0.95); }
```

**JS interaction key points:**
- Keyboard navigation (←/→, ↑/↓, Space, PageUp/PageDown)
- Mouse wheel navigation (with throttle/debounce)
- Touch swipe navigation (mobile support)
- Dot indicator click-to-jump
- Responsive window scaling (preserve original design aspect ratio)

**Responsive scaling core logic:**
```javascript
const scale = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
const offsetX = (window.innerWidth - 1920 * scale) / 2;
const offsetY = (window.innerHeight - 1080 * scale) / 2;
slide.style.transform = `scale(${scale})`;
slide.style.left = offsetX + 'px';
slide.style.top = offsetY + 'px';
slide.style.transformOrigin = 'top left';
```

## Lessons Learned & Best Practices

### Parallelization Strategy

The following steps throughout the workflow should be parallelized whenever possible for significant efficiency gains:
- All pages' `batch_read` calls in parallel
- All pages' `capture_screenshot` calls in parallel
- Text node and frame node `fullData` reads in parallel
- Image and SVG exports in parallel batches

### Image Export Strategy

- **Only export image nodes**: Use `scan_exportable_resources` to identify which nodes are images
- **Do NOT export entire slides**: Slides are composed of images + text + decorations; exporting the whole slide as an image loses editability
- **Batch exports**: `export_nodes` supports max 10 nodes per call; 5 per batch is more stable in practice
- **Create directories first**: Ensure target directories exist before exporting; otherwise exports may fail
- **Retry on failure**: Reducing batch size and retrying usually resolves export failures

### Color Value Conversion

Design files use 0~1 float values for colors; these must be converted to CSS hex values:

```javascript
function rgbToHex(r, g, b) {
  return '#' + [r, g, b].map(v => 
    Math.round(v * 255).toString(16).padStart(2, '0')
  ).join('').toUpperCase();
}
// Example: {r: 0.831, g: 0.686, b: 0.216} → #D4AF37
```

### Common Opacity Handling

Design files contain two types of opacity:
1. **Node opacity**: e.g., background image frame `opacity: 0.35`, affects the entire element → CSS `opacity: 0.35`
2. **Fill opacity**: e.g., `fills[0].opacity: 0.8`, affects only the fill → CSS `rgba()` or opacity on the fill
3. **Gradient color alpha**: e.g., `color.a: 0`, controls gradient endpoint transparency → CSS `rgba(r, g, b, 0)`

### Absolute Positioning Elements

In frames with `layoutMode: "NONE"` or children has `layoutPosition: "ABSOLUTE"`, use `x`, `y` for absolute positioning:

```css
.parent { position: relative; }
.child {
  position: absolute;
  left: <x>px;
  top: <y>px;
  width: <width>px;
  height: <height>px;
}
```

### Gradient Overlay Common Patterns

Designs frequently use gradient overlays to enhance text readability:

```css
/* Top-down gradient darkening */
.gradient-top {
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 400px;
  background: linear-gradient(to bottom, var(--bg), transparent);
}

/* Left-side gradient darkening (for text overlaid on images) */
.dark-overlay-left {
  position: absolute;
  top: 0; left: 0;
  width: 900px; height: 100%;
  background: linear-gradient(to right, rgba(13, 10, 15, 0.95), transparent);
}
```

### Letter Spacing Handling

The `letterSpacing` unit in design files can be `PIXELS` or `PERCENT`:
- `PIXELS` → use directly as `letter-spacing: <value>px`
- `PERCENT` → convert to `letter-spacing: <value/100>em`

### Decorative Border Lines Implementation

Double-line borders in design files are often implemented with two RECTANGLE nodes; CSS can use `::before`/`::after` pseudo-elements:

```css
.border-decor-top::before {
  content: '';
  position: absolute;
  top: 0; left: 0;
  width: 100%; height: 2px;
  background: var(--gold);
}
.border-decor-top::after {
  content: '';
  position: absolute;
  top: 4px; left: 60px;
  width: calc(100% - 120px); height: 1px;
  background: rgba(212, 175, 55, 0.3);
}
```

---

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| Image export fails | Batch too large or directory missing | Run `mkdir -p` first; reduce to 5 per batch |
| Text styles don't match | fullData not read | Re-read text nodes with `fullData: true` |
| Gradient direction wrong | gradientTransform misinterpreted | Cross-reference with the mapping table |
| Opacity stacking anomaly | Node opacity and fill opacity confused | Handle the two opacity types separately |
| Layout misaligned | Flex property mapping error | Verify primaryAxis/counterAxis mapping |
| SVG not displaying | Path error or viewBox issue | Check export path and SVG content |
| Responsive scaling offset | transformOrigin set incorrectly | Ensure it is set to `top left` |


### workflows/extract-style-guide-from-web.md

# Website-to-Design-Guide Extraction Workflow

This reference documents the complete workflow for extracting a website's visual design language and producing a structured, production-grade design style guide in an .ardot file.

## When to Use

Activate this workflow when the user provides a website URL and asks to:
- Generate a design style guide from a website
- Extract design tokens / design system from a URL
- Convert a website's visual style into a design specification
- Analyze and document a website's design language
- 将网站设计风格转为设计稿/设计指南
- 提取网站的配色/字体/组件规范

## Overview

The workflow has **4 phases**:

```
Phase 0: Ensure Design File Is Open (create or open an .ardot file)
    ↓
Phase 1: Website Analysis (extract raw design data)
    ↓
Phase 2: Design Token Creation (create reusable variables)
    ↓
Phase 3: Design Guide Assembly (build the guide frame-by-frame)
    ↓
Phase 4: Verification (screenshot each section)
```

---

## Phase 0: Ensure Design File Is Open

**Follow `ardot-design-core` SKILL.md → Step 0** for the full file-open rule — including the injected
`<ardot_file_directive action="create|open">` main path, the **at-most-one** `create_design` / `open_design`
idempotency hard rule, and the async-load wait gate (never re-issue to "confirm"). Do not re-derive create vs. open here.

**Design-to-code deviation — deferred `fetch_file_info`:** on the `create_design` branch, defer
`fetch_file_info` until just before the first MCP call in **Phase 2** (`apply_variables`). Phase 1 is entirely
local/web tooling (`curl`, `WebFetch`, `playwright`), which covers the async file-load window — pair
`fetch_file_info` with Phase 2's first MCP message. On the `open_design` branch, call it right after the file is ready.

---

## Phase 1: Website Analysis

### Goal
Extract all visual design information from the target website: colors, fonts, spacing, layout patterns, component styles, and overall aesthetic direction.

### Step 1.1: Fetch HTML Source

Use `curl` or `WebFetch` to retrieve the page HTML:

```bash
curl -sL "https://example.com" 2>&1 | head -500
```

From the HTML, identify:
- **Meta theme-color** — often reveals the brand accent color
- **CSS file paths** — `<link href="css/app.xxx.css">`
- **Font references** — `@font-face` declarations, Google Fonts links
- **Framework** — Vue SPA, React, static HTML, etc.

### Step 1.2: Fetch and Parse CSS

Download the main CSS file and extract design tokens:

```bash
# Get CSS variables (colors, spacing, fonts)
curl -sL "https://example.com/css/app.xxx.css" | tr ';' '\n' | grep -E '^\s*--' | sort -u

# Get font families
curl -sL "https://example.com/css/app.xxx.css" | tr ';' '\n' | grep -i 'font-family' | sort -u

# Get font sizes
curl -sL "https://example.com/css/app.xxx.css" | tr ';' '\n' | grep -i 'font-size' | sort -u

# Get @font-face declarations
curl -sL "https://example.com/css/app.xxx.css" | tr '{' '\n' | grep '@font-face' -A5
```

Key data to extract:
- **CSS custom properties** (`--theme-color`, `--bg-color`, etc.)
- **Color values** (hex, rgb, hsl)
- **Font families** (primary/display font, body font, CJK font fallbacks)
- **Font sizes** (establish the type scale)
- **Border radius values**
- **Spacing/padding patterns**

### Step 1.3: Take Screenshots

Use Playwright to capture the full page and viewport screenshots:

```bash
# Full page screenshot
npx playwright screenshot --wait-for-timeout 5000 --full-page "https://example.com" /tmp/site_full.png

# Viewport screenshot (hero section)
npx playwright screenshot --wait-for-timeout 5000 --viewport-size=1440,900 "https://example.com" /tmp/site_hero.png
```

If Playwright is not installed, use `npx playwright install chromium` first.

Analyze screenshots for:
- Overall color scheme (dark/light, accent colors)
- Typography style (serif/sans-serif, bold/light, uppercase patterns)
- Layout patterns (grid, full-width sections, sidebar)
- Component styles (buttons, cards, navigation)
- Visual effects (gradients, shadows, textures, patterns)
- Spacing density (tight/airy)
- Corner radius patterns (sharp/rounded/pill)

### Step 1.4: Synthesize Design Language

Compile findings into a structured brief:

| Category | Extracted Value | Design Decision |
|----------|----------------|-----------------|
| Primary BG | `#171717` | Deep black background |
| Accent | `#17F700` | Neon green for highlights |
| Text Primary | `#F7F7F7` | Near-white for readability |
| Eng Font | Custom sans-serif | Use Inter as closest match |
| ZH Font | 思源黑体 | Source Han Sans / fallbacks |
| Radius | 0px main, 100px buttons | Sharp containers, pill buttons |
| Style | Dark, high-contrast, techy | Brutalist + neon aesthetic |

---

## Phase 2: Design Token Creation

### Goal
Create a reusable variable set in the .ardot file using `apply_variables`.

### Step 2.1: Define Variable Structure

Organize tokens into logical groups:

```json
{
  "<ProjectName> Design Tokens": {
    "modes": ["Default"],
    "variables": {
      "theme-primary": { "type": "COLOR", "value": {...}, "scopes": ["ALL_FILLS"] },
      "theme-accent": { "type": "COLOR", "value": {...}, "scopes": ["ALL_FILLS"] },
      "theme-surface": { "type": "COLOR", "value": {...}, "scopes": ["ALL_FILLS"] },
      "text-primary": { "type": "COLOR", "value": {...}, "scopes": ["TEXT_FILL"] },
      "text-secondary": { "type": "COLOR", "value": {...}, "scopes": ["TEXT_FILL"] },
      "text-muted": { "type": "COLOR", "value": {...}, "scopes": ["TEXT_FILL"] },
      "border-default": { "type": "COLOR", "value": {...}, "scopes": ["STROKE"] },
      "spacing-xs": { "type": "FLOAT", "value": 4, "scopes": ["GAP"] },
      "spacing-sm": { "type": "FLOAT", "value": 8, "scopes": ["GAP"] },
      "spacing-md": { "type": "FLOAT", "value": 16, "scopes": ["GAP"] },
      "spacing-lg": { "type": "FLOAT", "value": 24, "scopes": ["GAP"] },
      "spacing-xl": { "type": "FLOAT", "value": 32, "scopes": ["GAP"] },
      "spacing-2xl": { "type": "FLOAT", "value": 48, "scopes": ["GAP"] },
      "spacing-3xl": { "type": "FLOAT", "value": 64, "scopes": ["GAP"] },
      "radius-none": { "type": "FLOAT", "value": 0, "scopes": ["CORNER_RADIUS"] },
      "radius-sm": { "type": "FLOAT", "value": 4, "scopes": ["CORNER_RADIUS"] },
      "radius-md": { "type": "FLOAT", "value": 8, "scopes": ["CORNER_RADIUS"] },
      "radius-lg": { "type": "FLOAT", "value": 12, "scopes": ["CORNER_RADIUS"] },
      "radius-pill": { "type": "FLOAT", "value": 100, "scopes": ["CORNER_RADIUS"] }
    }
  }
}
```

### Token naming conventions
- Colors: `theme-*` for brand, `text-*` for text fills, `border-*` for strokes
- Spacing: `spacing-xs/sm/md/lg/xl/2xl/3xl` (4/8/16/24/32/48/64)
- Radius: `radius-none/sm/md/lg/pill`
- COLOR values must be `{r, g, b, a}` with 0–1 range, NOT hex strings

---

## Phase 3: Design Guide Assembly

### Goal
Build the design guide as a single vertical frame containing all specification sections.

### Step 3.1: Get Editor State & Find Space

```
fetch_editor_state(includeSchema: false)   → understand canvas state
locate_available_space(width: 1440, height: 5000)  → find placement
```

### Step 3.2: Create Main Container

```javascript
page=I("pageId", {type: "frame", name: "<ProjectName> Design Style Guide", layout: "vertical", width: 1440, height: "hug_contents", fill: "<bg-color>", padding: [80, 100], gap: 80})
```

The main frame uses the website's background color as `fill`. All child sections use `fills: []` (transparent) to inherit the parent background.

### Step 3.3: Build Sections

Build sections in this order (each as a separate `batch_edit` call, ≤ 25 ops):

#### Section Pattern (reuse for each)

Each section follows this structure:

```javascript
section=I("<pageId>", {type: "frame", name: "<Section Name>", layout: "vertical", width: "fill_container", height: "hug_contents", gap: 40, fills: []})
sectionTag=I(section, {type: "text", name: "Section Tag", content: "0N — SECTION TITLE", fontSize: 14, fontWeight: "500", letterSpacing: 4, fill: "<accent-color>", fontName: {family: "Inter", style: "Medium"}})
sectionHeading=I(section, {type: "text", name: "Section Heading", content: "中文标题", fontSize: 48, fontWeight: "700", fill: "<text-primary>", fontName: {family: "Inter", style: "Bold"}})
sectionDesc=I(section, {type: "text", name: "Section Description", content: "描述文字...", fontSize: 16, fontWeight: "400", fill: "<text-secondary>", width: "fill_container", textAutoResize: "HEIGHT", lineHeight: 28, fontName: {family: "Inter", style: "Regular"}})
```

---

### Section 1: Color Palette (`01 — COLOR PALETTE`)

**Structure:**
- Section header (tag + heading + description)
- Primary color swatches row (horizontal, `fill_container` width)
  - For each color: swatch frame (160px height) + label + hex + RGB + usage text
- Auxiliary gray scale row

**Color Swatch Card Pattern:**

```javascript
card=I(colorRow, {type: "frame", name: "Color Card - <Name>", layout: "vertical", width: "fill_container", height: "hug_contents", gap: 16, fills: []})
swatch=I(card, {type: "frame", name: "<Name> Swatch", layout: "none", width: "fill_container", height: 160, fill: "<color-hex>"})
label=I(card, {type: "text", name: "<Name> Label", content: "<Color Name>", fontSize: 18, fontWeight: "600", fill: "<text-primary>", fontName: {family: "Inter", style: "SemiBold"}})
hex=I(card, {type: "text", name: "<Name> Hex", content: "<#HEXVAL>", fontSize: 14, fill: "<text-muted>", fontName: {family: "Inter", style: "Regular"}})
rgb=I(card, {type: "text", name: "<Name> RGB", content: "RGB <r>, <g>, <b>", fontSize: 12, fill: "<text-muted>", fontName: {family: "Inter", style: "Regular"}})
usage=I(card, {type: "text", name: "<Name> Usage", content: "用途说明", fontSize: 12, fill: "<text-secondary>", fontName: {family: "Inter", style: "Regular"}})
```

**Gray Scale Pattern:**
- Smaller swatches (60px height) in horizontal row
- Each with hex label below

---

### Section 2: Typography (`02 — TYPOGRAPHY`)

**Structure:**
- Section header
- English font display box (bordered frame with padding)
  - Font family label (accent color, 12px, uppercase)
  - Display/H1/H2/Body/Caption samples at actual sizes
- CJK font display box (same pattern)
- Type scale table

**Font Display Box Pattern:**

```javascript
fontBox=I(typoSection, {type: "frame", name: "English Font Display", layout: "vertical", width: "fill_container", height: "hug_contents", gap: 16, padding: [32, 32], fills: [], strokes: [{type: "SOLID", color: {r: 0.2, g: 0.2, b: 0.2, a: 1}}], strokeWeight: 1})
fontLabel=I(fontBox, {type: "text", content: "ENG — <Font Family>", fontSize: 12, fontWeight: "500", letterSpacing: 2, fill: "<accent-color>"})
displaySample=I(fontBox, {type: "text", content: "SAMPLE TEXT", fontSize: 72, fontWeight: "900", fill: "<text-primary>", letterSpacing: -2})
bodySample=I(fontBox, {type: "text", content: "Body text sample...", fontSize: 16, fill: "<text-secondary>", lineHeight: 28, width: "fill_container", textAutoResize: "HEIGHT"})
```

**Type Scale Table Pattern:**

```javascript
scaleTable=I(typoSection, {type: "frame", name: "Type Scale Table", layout: "vertical", width: "fill_container", height: "hug_contents", gap: 0, fills: []})
// For each row:
row=I(scaleTable, {type: "frame", name: "Scale Row", layout: "horizontal", width: "fill_container", height: "hug_contents", padding: [16, 0], gap: 24, fills: [], counterAxisAlignItems: "CENTER", strokes: [{type: "SOLID", color: {r: 0.2, g: 0.2, b: 0.2, a: 1}}], strokeWeight: 1, strokeAlign: "OUTSIDE"})
level=I(row, {type: "text", content: "Display", fontSize: 14, fontWeight: "500", fill: "<accent>", width: 120})
size=I(row, {type: "text", content: "72–120px", fontSize: 14, fill: "<text-primary>", width: 120})
weight=I(row, {type: "text", content: "Black (900)", fontSize: 14, fill: "<text-secondary>", width: 140})
usage=I(row, {type: "text", content: "首屏标题", fontSize: 14, fill: "<text-muted>"})
```

**Standard type scale levels:** Display, H1, H2, H3, Body, Caption

---

### Section 3: Components (`03 — COMPONENTS`)

**Structure:**
- Section header
- Button subsection: label + horizontal row of button variants
- Card subsection: label + horizontal row of card examples
- Description notes for each subsection

**Button Pattern:**

```javascript
// Primary button (filled)
btn=I(btnRow, {type: "frame", name: "Button - Primary", layout: "horizontal", width: "hug_contents", height: "hug_contents", padding: [14, 40], fill: "<accent>", cornerRadius: <radius>, primaryAxisAlignItems: "CENTER", counterAxisAlignItems: "CENTER"})
btnText=I(btn, {type: "text", content: "LABEL", fontSize: 14, fontWeight: "600", fill: "<contrast-text>", letterSpacing: 2})

// Outline button (stroked)
btnO=I(btnRow, {type: "frame", name: "Button - Outline", layout: "horizontal", width: "hug_contents", height: "hug_contents", padding: [14, 40], fills: [], cornerRadius: <radius>, strokes: [{type: "SOLID", color: {...}}], strokeWeight: 2})

// Ghost button (light stroke)
btnG=I(btnRow, {type: "frame", name: "Button - Ghost", layout: "horizontal", ..., strokes: [{type: "SOLID", color: {...}}], strokeWeight: 1})
```

**Card Pattern:**

```javascript
card=I(cardRow, {type: "frame", name: "Card Example", layout: "vertical", width: "fill_container", height: "hug_contents", gap: 0, fills: [], cornerRadius: <radius>, strokes: [{type: "SOLID", color: {...}}], strokeWeight: 1})
cardImg=I(card, {type: "frame", name: "Card Image", layout: "none", width: "fill_container", height: 200, fill: "<surface-dark>"})
cardBody=I(card, {type: "frame", name: "Card Body", layout: "vertical", width: "fill_container", height: "hug_contents", padding: 20, gap: 12, fills: []})
cardNum=I(cardBody, {type: "text", content: "01", fontSize: 14, fontWeight: "700", fill: "<accent>"})
cardTitle=I(cardBody, {type: "text", content: "Title", fontSize: 20, fontWeight: "600", fill: "<text-primary>"})
cardDesc=I(cardBody, {type: "text", content: "Description", fontSize: 14, fill: "<text-muted>"})
```

---

### Section 4: Spacing & Radius (`04 — SPACING & RADIUS`)

**Structure:**
- Section header
- Spacing visualization row (green bars of increasing width)
- Radius visualization row (bordered shapes with different corner radii)

**Spacing Bar Pattern:**

```javascript
spGroup=I(spRow, {type: "frame", name: "Spacing <N>", layout: "vertical", width: "hug_contents", height: "hug_contents", gap: 8, fills: [], counterAxisAlignItems: "CENTER"})
spBar=I(spGroup, {type: "frame", name: "<N>px Box", layout: "none", width: <N>, height: 40, fill: "<accent>"})
spLabel=I(spGroup, {type: "text", content: "<N>", fontSize: 12, fill: "<text-muted>"})
```

Standard spacing scale: 4, 8, 16, 24, 32, 48, 64

**Radius Example Pattern:**

```javascript
rdGroup=I(rdRow, {type: "frame", name: "Radius <N> Group", layout: "vertical", width: "hug_contents", height: "hug_contents", gap: 12, fills: [], counterAxisAlignItems: "CENTER"})
rdBox=I(rdGroup, {type: "frame", name: "Radius <N> Box", layout: "none", width: 80, height: 80, fills: [], cornerRadius: <N>, strokes: [{type: "SOLID", color: <accent-color>}], strokeWeight: 2})
rdLabel=I(rdGroup, {type: "text", content: "<N>px\n用途说明", fontSize: 12, fill: "<text-muted>", textAlignHorizontal: "CENTER", textAutoResize: "HEIGHT", width: 100, lineHeight: 18})
```

For pill shapes, use a wider box (160×50) with `cornerRadius: 100`.

---

### Section 5: Design Principles (`05 — DESIGN PRINCIPLES`)

**Structure:**
- Section header
- Wrapped grid of principle cards (3 columns)

**Principle Card Pattern:**

```javascript
pCard=I(pGrid, {type: "frame", name: "Principle <N>", layout: "vertical", width: 380, height: "hug_contents", gap: 16, padding: 32, fills: [], strokes: [{type: "SOLID", color: {...}}], strokeWeight: 1})
pNum=I(pCard, {type: "text", content: "0<N>", fontSize: 48, fontWeight: "900", fill: "<accent>"})
pTitle=I(pCard, {type: "text", content: "原则标题", fontSize: 20, fontWeight: "700", fill: "<text-primary>"})
pDesc=I(pCard, {type: "text", content: "原则描述...", fontSize: 14, fill: "<text-secondary>", lineHeight: 24, width: "fill_container", textAutoResize: "HEIGHT"})
```

Use `layoutWrap: "WRAP"` on the grid container for auto-wrapping.

Common design principles to document:
- Color contrast philosophy
- Animation/motion approach
- Pattern/texture usage
- Typography conventions (uppercase, tracking, etc.)
- Grid/layout system
- Iconography style

---

### Section 6: Layout & Motion (`06 — LAYOUT & MOTION`)

**Structure:**
- Section header
- Two-column info grid (layout spec + motion spec)

**Two-Column Pattern:**

```javascript
grid=I(section, {type: "frame", name: "Info Grid", layout: "horizontal", width: "fill_container", height: "hug_contents", gap: 24, fills: []})
col1=I(grid, {type: "frame", name: "Column 1", layout: "vertical", width: "fill_container", height: "hug_contents", gap: 20, padding: 32, fills: [], strokes: [{...}], strokeWeight: 1})
col1Title=I(col1, {type: "text", content: "页面布局", fontSize: 20, fontWeight: "700", fill: "<text-primary>"})
col1Content=I(col1, {type: "text", content: "• 布局要点1\n• 布局要点2\n...", fontSize: 14, fill: "<text-secondary>", lineHeight: 28, width: "fill_container", textAutoResize: "HEIGHT"})
// Repeat for col2
```

Layout specs to document:
- Page width / content width
- Section structure (full-width, contained, etc.)
- Responsive breakpoints
- Navigation pattern
- Scroll behavior (progress bar, parallax, etc.)

Motion specs to document:
- Animation library (GSAP, CSS, Framer Motion, etc.)
- Entrance animations (clip-path, fade, slide, etc.)
- Hover effects
- Loading animations
- Easing functions and duration ranges

---

### Section 7: Footer

```javascript
divider=I("<pageId>", {type: "frame", name: "Footer Divider", layout: "none", width: "fill_container", height: 3, fill: "<accent>"})
footer=I("<pageId>", {type: "frame", name: "Footer", layout: "horizontal", width: "fill_container", height: "hug_contents", fills: [], primaryAxisAlignItems: "SPACE_BETWEEN", counterAxisAlignItems: "CENTER"})
brand=I(footer, {type: "text", content: "<SITE>.COM", fontSize: 14, fontWeight: "700", fill: "<text-primary>", letterSpacing: 4})
copyright=I(footer, {type: "text", content: "© <Year> <Name>. All Rights Reserved. | Style Guide v1.0", fontSize: 12, fill: "<text-muted>"})
```

---

## Phase 4: Verification

Follow the **Post-Generation Validation Pattern** in `design-rules.md` for each section.

**Do NOT screenshot the entire page** if height exceeds 2000px — screenshot sections individually.

Additional checks specific to style guides:
- [ ] Color swatches display correct hex values
- [ ] Font samples render at intended sizes and weights
- [ ] Buttons show correct fill/stroke patterns
- [ ] Spacing bars match the defined scale (4, 8, 16, 24, 32, 48, 64)
- [ ] Layout wraps properly (principles grid with `layoutWrap: "WRAP"`)

---

## Design Conventions

### Section numbering
Use uppercase English labels with numbering: `01 — COLOR PALETTE`, `02 — TYPOGRAPHY`, etc.

### Text hierarchy within the guide
| Role | Size | Weight | Color |
|------|------|--------|-------|
| Section tag | 14px, Medium | 500 | Accent color |
| Section heading | 48px, Bold | 700 | Text primary |
| Description | 16px, Regular | 400 | Text secondary |
| Subsection label | 20–24px, SemiBold | 600 | Text primary |
| Body/notes | 14px, Regular | 400 | Text muted |
| Swatch label | 18px, SemiBold | 600 | Text primary |
| Hex/RGB value | 12–14px, Regular | 400 | Text muted |

### Frame naming convention
Every node must have a meaningful `name`. Use this pattern:
- Sections: `"Color Palette Section"`, `"Typography Section"`
- Cards: `"Color Card - Green"`, `"Principle 1"`
- Swatches: `"Green Swatch"`, `"Gray 666 Swatch"`
- Text: `"Green Label"`, `"Green Hex"`, `"P1 Title"`
- Rows: `"Color Swatches Row"`, `"Button Examples Row"`

### Accent color as section marker
The website's accent/brand color should be used for:
- Section tag text (e.g., "01 — COLOR PALETTE")
- Principle card numbers
- Spacing bar fills
- Radius example strokes
- Divider lines
- Card numbering in component examples
