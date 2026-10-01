# shared-styles

> Bind/unbind shared styles via textStyleId/fillStyleId/strokeStyleId/effectStyleId (bare nodeId or Style:key,ver; never `$`)

## 使用说明（重要）

本技能从 WorkBuddy 导入。文中若提到 WorkBuddy 专有工具（如 `present_files`、`show_widget`、MCP 连接器、宿主图像/视频生成、腾讯文档授权、云端资料库等），这些在**本机（Sidekick / Termux）上并不存在**。遇到这类步骤，请改用本机已有能力实现，或直接跳过并说明原因。

**本文档的价值在于方法论与规范**（怎么写、怎么排版、怎么避坑）——照此执行即可，不必强行还原 WorkBuddy 的实现方式。

# Working with Shared Styles

Bind reusable shared styles (text / fill / stroke / effect) to nodes by GUID. The `<StyleId>` is the style nodeId.

| Location | template |
|---|---|
| Local | `xxxStyleId:<StyleId>` |
| Team library | `xxxStyleId:<StyleId>, libraryKey:<LibraryKey>` |

```javascript
title=I(parent, {type: "text", content: "Heading", textStyleId: "<StyleId>"})

card=I(parent, {type: "frame", fillStyleId: "<StyleId>"})
card=U("nodeId", {strokeStyleId: "<StyleId>"})

card=U("nodeId", {effectStyleId: "<StyleId>"})

libraryCard=I(parent, {type: "frame", fillStyleId: "<StyleId>", libraryKey: "<LibraryKey>"})
```

## Style Rules

- ID format: pass the bare style nodeId. Do NOT prepend `$` (that prefix is for variable references).
- `textStyleId` is TEXT-only. `fillStyleId` / `strokeStyleId` apply to any node with fills/strokes. `effectStyleId` applies to any node with effects.
- Style binding wins at render time over inline `fontName`/`fills`/`strokes`/`effects` — when binding a style, omit the matching literal property unless overriding for a single instance.
- For team-library styles, pass `styleId` and `libraryKey` from `search_styles`.
- To remove a style binding, set the field to `null`:

```javascript
U("nodeId", {textStyleId: null, fillStyleId: null, strokeStyleId: null, effectStyleId: null})
```