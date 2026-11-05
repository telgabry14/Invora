"""Review workbook: the human-in-the-loop cornerstone.

``build`` writes an .xlsx where every line shows the *crop of the scan* next to what was read and the
proposed Softec code, so a person can (a) check every column against the paper and (b) confirm or fix the
code. ``read`` + ``learned.learn_rows`` turn those confirmations into the alias memory, so each review makes
the next run more automatic.

Sheets
  للمراجعة          lines with status 'review' (crop + proposal + editable confirmed code)
  عينة تلقائي       a random sample of 'auto' lines: measures the REAL precision of the auto rule
  تعليمات           how to use it
Editable columns: «الكود المؤكد» (pre-filled with the proposal) and «تمت المراجعة» (✔ = confirmed, ✘ = proposal was wrong).
Only rows marked ✔/✘ are learned from; untouched rows teach nothing.
"""
from __future__ import annotations

import io
import os
import random
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from typing import Dict, List, Optional, Tuple

import openpyxl
import openpyxl.worksheet.properties
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

SHEET_REVIEW = "للمراجعة"
SHEET_SAMPLE = "عينة تلقائي"
SHEET_HELP = "تعليمات"
CROP_HALF_H = 52          # px above/below the row centre at 300 dpi

CROP_W = 1100             # every crop is resized to this width (px), so the image column width is exact
IMG_COL_W = CROP_W / 7.0 + 2
ROW_H_PT = 52

COLS = [  # (header, key, width): editable cells first, then the scan crop, then everything that was read
    ("#", "n", 5), ("الكود المؤكد", "confirmed", 12), ("تمت المراجعة", "reviewed", 9),
    ("الكود المقترح", "item_code", 11), ("اسم الصنف في الماستر", "item_name", 32), ("الدرجة", "match_score", 7),
    ("صورة السطر من الفاتورة", "_img", IMG_COL_W),
    ("الاسم على الفاتورة (OCR)", "name_on_scan", 28), ("بديل 2", "alt_2", 30), ("بديل 3", "alt_3", 30),
    ("الكمية", "qty", 7), ("سعر الصيدلي", "unit_price_net", 10), ("خصم %", "discount_pct", 7), ("سعر الجمهور", "public_price", 9),
    ("تشغيلة", "batch", 12), ("صلاحية", "expiry", 11), ("هامش صيدلي", "margin_pharm", 9), ("ضريبة سطر", "vat_line", 9),
    ("إجمالي السطر", "line_total", 10), ("المورد", "supplier", 11), ("رقم الفاتورة", "invoice_key", 14),
    ("تاريخ", "scan_date", 11), ("صفحة", "page", 6), ("ملاحظات آلية", "flags", 28),
    ("template", "template", 8), ("layout", "layout", 7), ("supplier_item_code", "supplier_item_code", 12),
    ("file", "file", 14),
]
KEYS = [k for _, k, _ in COLS]


# ---------------------------------------------------------------- crops
def _render_crops(args) -> Dict[Tuple[str, int, int], bytes]:
    """(pdf, page, [y...]) -> {(file, page, y): jpeg bytes}"""
    import cv2
    from .prep import clean_page, render_pages
    pdf, page, ys = args
    out = {}
    for n, g in render_pages(pdf, dpi=300):
        if n != page:
            continue
        clean, _ = clean_page(g)
        H, W = clean.shape
        for y in ys:
            y0, y1 = int(max(y - CROP_HALF_H, 0)), int(min(y + CROP_HALF_H, H))
            f = CROP_W / W
            im = cv2.resize(clean[y0:y1], None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, 72])
            if ok:
                out[(os.path.basename(pdf), page, int(y))] = buf.tobytes()
        break
    return out


def make_crops(rows: List[Dict], pdf_dir: str, workers: int = 4) -> Dict:
    need = defaultdict(list)
    for r in rows:
        if r.get("page") and r.get("y") and r.get("file"):
            need[(os.path.join(pdf_dir, r["file"]), int(r["page"]))].append(int(r["y"]))
    jobs = [(pdf, pg, ys) for (pdf, pg), ys in need.items() if os.path.exists(pdf)]
    crops: Dict = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for part in ex.map(_render_crops, jobs):
            crops.update(part)
    return crops


# ---------------------------------------------------------------- workbook
def _sheet(wb, title: str, rows: List[Dict], crops: Optional[Dict], sample: bool):
    ws = wb.create_sheet(title)
    ws.sheet_view.rightToLeft = True
    hdr_fill = PatternFill("solid", fgColor="1F4E78")
    for c, (h, _, w) in enumerate(COLS, start=1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.font, cell.fill = Font(bold=True, color="FFFFFF"), hdr_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[openpyxl.utils.get_column_letter(c)].width = w
    ws.row_dimensions[1].height = 30
    edit_fill = PatternFill("solid", fgColor="FFF2CC")
    from openpyxl.drawing.image import Image as XlImage
    for i, r in enumerate(rows, start=2):
        r = dict(r)
        r["n"] = i - 1
        r["confirmed"] = r.get("item_code", "")
        r["reviewed"] = ""
        for c, (_, k, _) in enumerate(COLS, start=1):
            if k == "_img":
                continue
            v = r.get(k, "")
            cell = ws.cell(row=i, column=c, value=(None if v in ("", None) else v))
            cell.alignment = Alignment(vertical="center", wrap_text=k in ("item_name", "alt_2", "alt_3", "name_on_scan", "flags"))
            if k in ("confirmed", "reviewed"):
                cell.fill = edit_fill
        ws.row_dimensions[i].height = ROW_H_PT
        buf = crops.get((r.get("file"), int(r["page"]), int(r["y"]))) if (crops and r.get("page") and r.get("y")) else None
        if buf:
            img = XlImage(io.BytesIO(buf))
            ws.add_image(img, f"{openpyxl.utils.get_column_letter(KEYS.index('_img') + 1)}{i}")
    dv = DataValidation(type="list", formula1='"✔,✘"', allow_blank=True)
    ws.add_data_validation(dv)
    rc = KEYS.index("reviewed") + 1
    dv.add(f"{openpyxl.utils.get_column_letter(rc)}2:{openpyxl.utils.get_column_letter(rc)}{len(rows) + 1}")
    ws.freeze_panes = "G2"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A3
    ws.sheet_properties.pageSetUpPr = openpyxl.worksheet.properties.PageSetupProperties(fitToPage=True)
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.auto_filter.ref = f"A1:{openpyxl.utils.get_column_letter(len(COLS))}{len(rows) + 1}"
    for k in ("template", "layout", "supplier_item_code", "file"):
        ws.column_dimensions[openpyxl.utils.get_column_letter(KEYS.index(k) + 1)].hidden = True
    return ws


HELP = [
    "طريقة المراجعة",
    "1) في ورقة «للمراجعة»: كل سطر فيه صورة السطر من الفاتورة. قارن الأعمدة المقروءة (كمية، سعر، خصم، تشغيلة، صلاحية، هامش) بالصورة.",
    "2) عمود «الكود المؤكد» متعبّي بالكود المقترح. لو غلط اكتب الكود الصحيح (من سوفتك).",
    "3) في «تمت المراجعة» اختار ✔ لو الكود المؤكد صح، أو ✘ لو المقترح كان غلط (وصحّحته). الأسطر اللي ماتمسّش مش بتتعلّم منها.",
    "4) ورقة «عينة تلقائي»: أسطر كانت تلقائية. راجع منها لتعرف دقة القاعدة الحقيقية (✔/✘).",
    "5) اتعلّم: python -m invora_reader learn review.xlsx --store learned.json ثم شغّل read بـ --learned learned.json",
    "الأعمدة المخفية (template/layout/supplier_item_code/file) لازم تفضل زي ما هي.",
]


def build(rows: List[Dict], out_path: str, pdf_dir: Optional[str] = None, sample_auto: int = 150,
          seed: int = 1, workers: int = 4, crops: Optional[Dict] = None) -> Dict:
    review = [r for r in rows if r["status"] != "auto"]
    auto = [r for r in rows if r["status"] == "auto"]
    random.Random(seed).shuffle(auto)
    sample = auto[:sample_auto]
    if crops is None and pdf_dir:
        crops = make_crops(review + sample, pdf_dir, workers)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    _sheet(wb, SHEET_REVIEW, review, crops, sample=False)
    _sheet(wb, SHEET_SAMPLE, sample, crops, sample=True)
    ws = wb.create_sheet(SHEET_HELP)
    ws.sheet_view.rightToLeft = True
    ws.column_dimensions["A"].width = 140
    for i, t in enumerate(HELP, start=1):
        ws.cell(row=i, column=1, value=t).font = Font(bold=(i == 1), size=13 if i == 1 else 11)
    wb.save(out_path)
    return {"review": len(review), "sample": len(sample), "crops": len(crops or {})}


# ---------------------------------------------------------------- reading it back
def read(path: str) -> List[Dict]:
    """Rows of both sheets as dicts, with the sheet name in ``_sheet`` and the human decision."""
    wb = openpyxl.load_workbook(path, data_only=True)
    out = []
    for name in (SHEET_REVIEW, SHEET_SAMPLE):
        if name not in wb.sheetnames:
            continue
        ws = wb[name]
        head = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        idx = {COLS[i][1]: head.index(COLS[i][0]) for i in range(len(COLS)) if COLS[i][0] in head}
        for row in ws.iter_rows(min_row=2, values_only=True):
            d = {k: (row[j] if j < len(row) else None) for k, j in idx.items()}
            d["_sheet"] = name
            out.append(d)
    return out


def decisions(rows: List[Dict]) -> Dict:
    """Summary of what the human did, incl. the measured precision of the auto sample."""
    s = {"confirmed": 0, "corrected": 0, "untouched": 0, "auto_checked": 0, "auto_correct": 0}
    for d in rows:
        mark = (d.get("reviewed") or "").strip()
        if not mark:
            s["untouched"] += 1
            continue
        proposal, final = str(d.get("item_code") or "").strip(), str(d.get("confirmed") or "").strip()
        wrong = mark == "✘" or (final and proposal and final != proposal)
        s["corrected" if wrong else "confirmed"] += 1
        if d["_sheet"] == SHEET_SAMPLE:
            s["auto_checked"] += 1
            s["auto_correct"] += 0 if wrong else 1
    return s
