#!/data/data/com.termux/files/usr/bin/python3
"""WPS / Office 文档读写工具 —— 不需要任何"连接器"，直接操作文件本身。

WPS 存的 .xlsx / .docx 本质就是 zip + XML，所以在 Termux 里可以直接读写。

用法：
  wps.py ls [目录]                     列出可读文档（默认扫共享存储）
  wps.py read <文件>                   读内容（.xlsx / .csv / .docx / .txt）
  wps.py write <out.xlsx> <data.json>  写 xlsx，data.json = [["表头","列2"],["值1","值2"]]
  wps.py csv2xlsx <in.csv> <out.xlsx>  CSV 转 xlsx（WPS 表格）
  wps.py doc <out.docx> <in.txt>       纯文本转 docx（WPS 文档）
"""
import csv
import json
import os
import sys
import zipfile
from xml.etree import ElementTree as ET

SHARED = os.path.expanduser("~/storage/shared")
NS_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


# ---------------------------------------------------------------- 读

def read_xlsx(path):
    """读 xlsx：优先用 openpyxl，没装则退回 zipfile+XML。"""
    try:
        import openpyxl
    except ImportError:
        return read_xlsx_raw(path)
    wb = openpyxl.load_workbook(path, data_only=True)
    out = []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(values_only=True):
            if any(c is not None and str(c).strip() for c in row):
                rows.append(["" if c is None else str(c) for c in row])
        out.append("### 工作表: %s（%d 行）" % (ws.title, len(rows)))
        for r in rows:
            out.append(" | ".join(r))
    return "\n".join(out)


def read_xlsx_raw(path):
    """不依赖 openpyxl 的兜底读法。"""
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root:
            shared.append("".join(t.text or "" for t in si.iter() if t.tag.endswith("}t")))
    out = []
    sheets = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))
    for sn in sheets:
        root = ET.fromstring(z.read(sn))
        out.append("### %s" % sn)
        for row in root.iter():
            if not row.tag.endswith("}row"):
                continue
            cells = []
            for c in row:
                v = c.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v")
                if v is None or v.text is None:
                    continue
                cells.append(shared[int(v.text)] if c.get("t") == "s" else v.text)
            if cells:
                out.append(" | ".join(cells))
    return "\n".join(out)


def read_docx(path):
    """读 docx：zip 里取出 word/document.xml，抽所有文本节点。"""
    z = zipfile.ZipFile(path)
    root = ET.fromstring(z.read("word/document.xml"))
    paras = []
    for p in root.iter(NS_W + "p"):
        txt = "".join(t.text or "" for t in p.iter(NS_W + "t"))
        paras.append(txt)
    return "\n".join(paras)


def read_csv(path):
    with open(path, newline="", encoding="utf-8", errors="ignore") as f:
        return "\n".join(" | ".join(r) for r in csv.reader(f))


# ---------------------------------------------------------------- 写

def write_xlsx(out, rows):
    try:
        import openpyxl
    except ImportError:
        sys.exit("需要 openpyxl：pip install openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for r in rows:
        ws.append(r)
    # 表头加粗 + 列宽自适应
    if rows:
        for c in range(1, len(rows[0]) + 1):
            cell = ws.cell(row=1, column=c)
            cell.font = openpyxl.styles.Font(bold=True)
        for c in range(1, len(rows[0]) + 1):
            width = max((len(str(r[c - 1])) for r in rows if len(r) >= c), default=8)
            ws.column_dimensions[openpyxl.utils.get_column_letter(c)].width = min(40, max(8, width + 3))
    wb.save(out)
    return out


def write_docx(out, text):
    """生成最小可用的 docx（WPS / Word 都能打开）。"""
    def esc(s):
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    body = "".join(
        '<w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % esc(line)
        for line in text.splitlines() or [""]
    )
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:body>%s</w:body></w:document>' % body)
    types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
             '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    z = zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED)
    z.writestr("[Content_Types].xml", types)
    z.writestr("_rels/.rels", rels)
    z.writestr("word/document.xml", doc)
    z.close()
    return out


# ---------------------------------------------------------------- 列举

def ls(root=SHARED, limit=40):
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath.count(os.sep) - root.count(os.sep) > 3:
            dirnames[:] = []
            continue
        for fn in filenames:
            if fn.lower().endswith((".xlsx", ".xls", ".docx", ".doc", ".csv", ".et", ".wps", ".pptx")):
                hits.append(os.path.join(dirpath, fn))
                if len(hits) >= limit:
                    return hits
    return hits


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return
    cmd = a[0]
    if cmd == "ls":
        root = a[1] if len(a) > 1 else SHARED
        for p in ls(root):
            print("  %8d  %s" % (os.path.getsize(p), p))
    elif cmd == "read":
        p = a[1]
        low = p.lower()
        if low.endswith(".xlsx") or low.endswith(".xls"):
            print(read_xlsx(p))
        elif low.endswith(".docx"):
            print(read_docx(p))
        elif low.endswith(".csv"):
            print(read_csv(p))
        else:
            print(open(p, encoding="utf-8", errors="ignore").read())
    elif cmd == "write":
        rows = json.loads(open(a[2], encoding="utf-8").read())
        print("已写入:", write_xlsx(a[1], rows))
    elif cmd == "csv2xlsx":
        with open(a[1], newline="", encoding="utf-8", errors="ignore") as f:
            rows = list(csv.reader(f))
        print("已写入:", write_xlsx(a[2], rows))
    elif cmd == "doc":
        text = open(a[2], encoding="utf-8", errors="ignore").read()
        print("已写入:", write_docx(a[1], text))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
