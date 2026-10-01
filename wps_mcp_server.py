#!/data/data/com.termux/files/usr/bin/python3
"""WPS MCP Server —— 手写实现（MCP over stdio，JSON-RPC 2.0）

为什么不用官方 mcp SDK：它依赖 rpds-py，需要 Rust 工具链，Termux 上编译不了。
MCP 的 stdio 传输就是「Content-Length 头 + JSON-RPC 消息」，协议本身很短，
手写一个反而能在 Termux 上跑，而且任何标准 MCP 客户端（Claude Code / Cursor 等）都能连。

单独测试：
  echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 wps_mcp_server.py   # 不行，需按帧格式
  用配套的 mcp_call.py 更方便。
"""
import json
import os
import sys
from pathlib import Path

# 统一工作目录：所有文件读写都限制在这里，避免乱跑
WORKSPACE = Path(os.path.expanduser("~/storage/shared/WPS_AI"))
DOCX_EXT = (".docx",)
XLSX_EXT = (".xlsx",)
PPTX_EXT = (".pptx",)

SERVER_INFO = {"name": "wps-mcp-server", "version": "1.0.0"}
PROTOCOL = "2024-11-05"


def _ws() -> Path:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    return WORKSPACE


def _p(filename: str) -> Path:
    """把文件名解析到工作目录内，禁止越界。"""
    name = Path(filename).name          # 只取文件名，防目录穿越
    return _ws() / name


# ---------------------------------------------------------------- 工具实现

def create_document(filename: str, title: str = "Untitled") -> str:
    import docx
    if not filename.endswith(DOCX_EXT):
        filename += ".docx"
    d = docx.Document()
    d.add_heading(title, level=0)
    d.save(str(_p(filename)))
    return "已创建文档：%s（标题：%s）" % (filename, title)


def add_heading(filename: str, text: str, level: int = 1) -> str:
    import docx
    d = docx.Document(str(_p(filename)))
    d.add_heading(text, level=max(0, min(9, int(level))))
    d.save(str(_p(filename)))
    return "已加入标题（%d 级）：%s" % (level, text)


def add_paragraph(filename: str, text: str, style: str = "Normal") -> str:
    import docx
    d = docx.Document(str(_p(filename)))
    try:
        d.add_paragraph(text, style=style)
    except Exception:
        d.add_paragraph(text)
    d.save(str(_p(filename)))
    return "已加入段落（%d 字）" % len(text)


def read_document(filename: str) -> str:
    import docx
    d = docx.Document(str(_p(filename)))
    lines = [p.text for p in d.paragraphs]
    return "\n".join(lines) if any(lines) else "（文档为空）"


def create_spreadsheet(filename: str, sheet_name: str = "Sheet1") -> str:
    import openpyxl
    if not filename.endswith(XLSX_EXT):
        filename += ".xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = sheet_name
    wb.save(str(_p(filename)))
    return "已创建表格：%s（工作表：%s）" % (filename, sheet_name)


def write_cell(filename: str, cell: str, value: str, sheet: str = "Sheet1") -> str:
    import openpyxl
    p = _p(filename)
    wb = openpyxl.load_workbook(str(p)) if p.exists() else openpyxl.Workbook()
    ws = wb[sheet] if sheet in wb.sheetnames else wb.create_sheet(sheet)
    ws[cell] = value
    wb.save(str(p))
    return "已写入：%s!%s = %s" % (sheet, cell, value)


def read_cell(filename: str, cell: str, sheet: str = "Sheet1") -> str:
    import openpyxl
    wb = openpyxl.load_workbook(str(_p(filename)), data_only=True)
    ws = wb[sheet] if sheet in wb.sheetnames else wb.active
    v = ws[cell].value
    return "空" if v is None else str(v)


def add_formula(filename: str, cell: str, formula: str, sheet: str = "Sheet1") -> str:
    import openpyxl
    p = _p(filename)
    wb = openpyxl.load_workbook(str(p))
    ws = wb[sheet] if sheet in wb.sheetnames else wb.create_sheet(sheet)
    ws[cell] = formula if formula.startswith("=") else "=" + formula
    wb.save(str(p))
    return "已写入公式：%s!%s" % (sheet, cell)


def create_presentation(filename: str, title: str = "Presentation") -> str:
    from pptx import Presentation
    if not filename.endswith(PPTX_EXT):
        filename += ".pptx"
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = title
    prs.save(str(_p(filename)))
    return "已创建演示文稿：%s（标题：%s）" % (filename, title)


def add_slide(filename: str, layout_index: int = 1) -> str:
    from pptx import Presentation
    prs = Presentation(str(_p(filename)))
    idx = max(0, min(len(prs.slide_layouts) - 1, int(layout_index)))
    prs.slides.add_slide(prs.slide_layouts[idx])
    prs.save(str(_p(filename)))
    return "已添加幻灯片，现共 %d 页" % len(prs.slides)


def add_text_to_slide(filename: str, slide_index: int, text: str) -> str:
    from pptx import Presentation
    prs = Presentation(str(_p(filename)))
    i = int(slide_index)
    if i < 0 or i >= len(prs.slides):
        return "页码越界：共 %d 页（0 起）" % len(prs.slides)
    slide = prs.slides[i]
    box = slide.shapes.add_textbox(0, 0, prs.slide_width, prs.slide_height // 2)
    box.text_frame.text = text
    prs.save(str(_p(filename)))
    return "已写入第 %d 页" % i


def set_slide_layout(filename: str, slide_index: int = 0, layout_index: int = 1) -> str:
    from pptx import Presentation
    prs = Presentation(str(_p(filename)))
    i = int(slide_index)
    if i < 0 or i >= len(prs.slides):
        return "页码越界"
    li = max(0, min(len(prs.slide_layouts) - 1, int(layout_index)))
    prs.slides[i].slide_layout = prs.slide_layouts[li]
    prs.save(str(_p(filename)))
    return "已把第 %d 页版式设为 %d" % (i, li)


def list_files() -> str:
    ws = _ws()
    fs = sorted(p.name for p in ws.iterdir() if p.is_file())
    return "工作目录 %s 下共 %d 个文件：\n%s" % (ws, len(fs), "\n".join(fs) if fs else "（空）")


# ---------------------------------------------------------------- 工具声明

TOOLS = [
    ("create_document", "创建 Word 文档（.docx）", {
        "filename": ("string", "文件名，如 周报.docx"), "title": ("string", "标题")},
     ["filename"]),
    ("add_heading", "向文档追加标题", {
        "filename": ("string", "文件名"), "text": ("string", "标题文字"),
        "level": ("integer", "标题级别 1-9")}, ["filename", "text"]),
    ("add_paragraph", "向文档追加段落", {
        "filename": ("string", "文件名"), "text": ("string", "段落内容"),
        "style": ("string", "样式名，默认 Normal")}, ["filename", "text"]),
    ("read_document", "读取 Word 文档全文", {"filename": ("string", "文件名")}, ["filename"]),
    ("create_spreadsheet", "创建 Excel 表格（.xlsx）", {
        "filename": ("string", "文件名"), "sheet_name": ("string", "工作表名")}, ["filename"]),
    ("write_cell", "写入单元格", {
        "filename": ("string", "文件名"), "cell": ("string", "单元格如 A1"),
        "value": ("string", "值"), "sheet": ("string", "工作表名")}, ["filename", "cell", "value"]),
    ("read_cell", "读取单元格", {
        "filename": ("string", "文件名"), "cell": ("string", "单元格如 A1"),
        "sheet": ("string", "工作表名")}, ["filename", "cell"]),
    ("add_formula", "写入公式", {
        "filename": ("string", "文件名"), "cell": ("string", "单元格"),
        "formula": ("string", "公式如 SUM(A1:A9)"), "sheet": ("string", "工作表名")},
     ["filename", "cell", "formula"]),
    ("create_presentation", "创建 PPT（.pptx）", {
        "filename": ("string", "文件名"), "title": ("string", "标题")}, ["filename"]),
    ("add_slide", "给 PPT 加一页", {
        "filename": ("string", "文件名"), "layout_index": ("integer", "版式序号")}, ["filename"]),
    ("add_text_to_slide", "给某页加文本框", {
        "filename": ("string", "文件名"), "slide_index": ("integer", "页码，0 起"),
        "text": ("string", "文字")}, ["filename", "slide_index", "text"]),
    ("set_slide_layout", "设置某页版式", {
        "filename": ("string", "文件名"), "slide_index": ("integer", "页码"),
        "layout_index": ("integer", "版式序号")}, ["filename"]),
    ("list_files", "列出 WPS_AI 工作目录下的文件", {}, []),
]

FUNCS = {
    "create_document": create_document, "add_heading": add_heading,
    "add_paragraph": add_paragraph, "read_document": read_document,
    "create_spreadsheet": create_spreadsheet, "write_cell": write_cell,
    "read_cell": read_cell, "add_formula": add_formula,
    "create_presentation": create_presentation, "add_slide": add_slide,
    "add_text_to_slide": add_text_to_slide, "set_slide_layout": set_slide_layout,
    "list_files": list_files,
}


def tool_schema():
    out = []
    for name, desc, props, required in TOOLS:
        out.append({
            "name": name, "description": desc,
            "inputSchema": {
                "type": "object",
                "properties": {k: {"type": v[0], "description": v[1]} for k, v in props.items()},
                "required": required,
            },
        })
    return out


# ---------------------------------------------------------------- JSON-RPC

def handle(req: dict):
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }}
    if method in ("notifications/initialized", "initialized"):
        return None                      # 通知，不回
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": tool_schema()}}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = FUNCS.get(name)
        if not fn:
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "未知工具：%s" % name}], "isError": True}}
        try:
            text = fn(**args)
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": str(text)}]}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "执行失败：%s: %s" % (type(e).__name__, e)}],
                "isError": True}}
    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": "未知方法：%s" % method}}


def main():
    inp, out = sys.stdin.buffer, sys.stdout.buffer
    while True:
        # 读 Content-Length 头
        length = None
        while True:
            line = inp.readline()
            if not line:
                return
            if line in (b"\r\n", b"\n"):
                break
            if line.lower().startswith(b"content-length:"):
                try:
                    length = int(line.split(b":", 1)[1].strip())
                except Exception:
                    length = None
        if length is None:
            continue
        body = inp.read(length)
        try:
            req = json.loads(body)
        except Exception:
            continue
        resp = handle(req)
        if resp is None:
            continue
        data = json.dumps(resp, ensure_ascii=False).encode("utf-8")
        out.write(b"Content-Length: %d\r\n\r\n" % len(data))
        out.write(data)
        out.flush()


if __name__ == "__main__":
    main()
