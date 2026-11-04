"""PharmaOverseas 'اذن استلام مسعر' reader.

Numeric columns, right->left (after upright orientation):
  qty | batch | expiry | public | tax_unit | disc_pct | pharm_price | total_incl_tax | tax_total | margins...
Row anchor = the tax_unit cell (one short ``dd.ddd`` token per row, never wrapped).
Numbers that wrap over two lines are re-joined by column and row.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

import numpy as np

from .ocr import Tok, numeric_tokens, arabic_text, cell_number, cell_text, cell_alnum

TAX = 0.14
ANCHOR = re.compile(r"^\d{1,3}\.\d{3}$")


@dataclass
class PoLine:
    y: int
    qty_ocr: Optional[float] = None
    public: Optional[float] = None
    tax_unit: Optional[float] = None
    disc_pct: Optional[float] = None
    pharm_price: Optional[float] = None
    total: Optional[float] = None
    tax_total: Optional[float] = None
    name_ar: str = ""
    maker_ar: str = ""
    margin_pharm: Optional[float] = None   # هامش ثابت للصيدلي
    margin_dist: Optional[float] = None    # هامش ثابت للموزع
    batch: str = ""                        # التشغيلة
    expiry: str = ""                       # ت. الصلاحية (MM.YYYY)
    qty: Optional[float] = None
    flags: List[str] = field(default_factory=list)
    ok: bool = False

    def to_dict(self) -> Dict:
        return asdict(self)


def _cluster_x(tokens: List[Tok], gap: float) -> List[List[Tok]]:
    cols: List[List[Tok]] = []
    for t in sorted(tokens, key=lambda t: -t.cx):  # right -> left
        if cols and cols[-1][-1].cx - t.cx <= gap:
            cols[-1].append(t)
        else:
            cols.append([t])
    return cols


def _num(tokens: List[Tok]) -> Optional[float]:
    """Join a cell's tokens top->bottom and parse."""
    s = "".join(t.text for t in sorted(tokens, key=lambda t: (t.cy)))
    s = s.strip(".-/")
    if s.count(".") > 1:
        head, _, tail = s.partition(".")
        s = head + "." + tail.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


# Calibration (px at reference scale s=1: digit height ~19.5px, i.e. 300dpi / 515pt page),
# relative to the discount-% column centre. Positive = further right (towards the item name).
OFF = {"qty": 565, "batch": 374, "expiry": 280, "public": 175, "tax_unit": 90, "pharm": -74,
       "total": -184, "tax_total": -298, "margin_pharm": -407, "margin_dist": -476}
HALF_W = {"public": 52, "tax_unit": 44, "pharm": 50, "total": 58, "tax_total": 54, "qty": 36, "batch": 50,
          "expiry": 48, "margin_pharm": 34, "margin_dist": 34}
BAND_UP, BAND_DOWN = 48.0, 26.0   # row band around the discount token centre (px at s=1)
DIGIT_H = 19.0
DISC = re.compile(r"^\d{1,2}(\.\d{1,2})?$")


TAXTOK = re.compile(r"^\d{1,3}\.\d{3}$")


def _has_tax_cell(t: Tok, toks: List[Tok]) -> bool:
    """A real discount anchor has a 'dd.ddd' VAT-per-unit cell ~90px (at s=1) to its right, same row."""
    s = max(t.h / DIGIT_H, 0.5)
    return any(TAXTOK.match(u.text) and u.conf >= 50 and abs(u.cx - (t.cx + OFF["tax_unit"] * s)) <= 40 * s
               and abs(u.cy - t.cy) <= 30 * s for u in toks)


def _anchors(toks: List[Tok], W: int) -> List[Tok]:
    """Discount-% cells: short clean numbers lying on one narrow, slightly slanted line.

    A VAT cell next to a candidate is used only to pick the right column (wrapped price fragments
    can outnumber the discount cells); once the line is known every candidate on it is a row.
    """
    cand = [t for t in toks if DISC.match(t.text) and t.conf >= 80 and t.w < 0.02 * W + 40 and t.h < 40]
    if not cand:
        return []
    seeds = [t for t in cand if _has_tax_cell(t, toks)] or cand
    main = max(_cluster_x(seeds, gap=0.012 * W + 14), key=len)
    pts = sorted(main, key=lambda t: t.cy)
    if len(pts) >= 4:
        keep = pts
        for _ in range(3):
            a, b = np.polyfit([t.cy for t in keep], [t.cx for t in keep], 1)
            res = [abs(t.cx - (a * t.cy + b)) for t in pts]
            keep = [t for t, r in zip(pts, res) if r <= 9] or keep
        a, b = np.polyfit([t.cy for t in keep], [t.cx for t in keep], 1)
    else:
        a, b = 0.0, float(np.median([t.cx for t in pts]))
    on_line = sorted([t for t in cand if abs(t.cx - (a * t.cy + b)) <= 11], key=lambda t: t.cy)
    out: List[Tok] = []
    for t in on_line:                       # one anchor per row
        if out and abs(t.cy - out[-1].cy) < 25:
            continue
        out.append(t)
    return out


def _fill_gaps(rows: List[Tok], s: float) -> List[tuple]:
    """(cx, cy) per row; insert rows where a ~100px-multiple gap shows a missed anchor."""
    out = [(rows[0].cx, rows[0].cy)]
    for a, b in zip(rows, rows[1:]):
        gap = b.cy - a.cy
        k = round(gap / (100.0 * s))
        if k >= 2 and abs(gap - k * 100.0 * s) < 20 * s:
            for j in range(1, k):
                f = j / k
                out.append((a.cx + (b.cx - a.cx) * f, a.cy + gap * f))
        out.append((b.cx, b.cy))
    return out


def read_page(clean: np.ndarray) -> Dict:
    """Return {'lines': [...], 'scale': s, 'warnings': [...]} for one upright, cleaned page."""
    H, W = clean.shape
    toks = numeric_tokens(clean, min_conf=40)
    anchors = _anchors(toks, W)
    if not anchors:
        return {"lines": [], "scale": None, "warnings": ["no row anchors (discount column) found"]}
    s = float(np.median([t.h for t in anchors])) / DIGIT_H
    rows = _fill_gaps(anchors, s)
    warn: List[str] = []
    lines: List[PoLine] = []

    def crop(name: str, cx: float, yc: float):
        hw = HALF_W[name] * s
        xc = cx + OFF[name] * s
        x0, x1 = int(max(xc - hw, 0)), int(min(xc + hw, W))
        y0, y1 = int(max(yc - BAND_UP * s, 0)), int(min(yc + BAND_DOWN * s, H))
        return clean[y0:y1, x0:x1]

    for cx, yc in rows:
        ln = PoLine(y=int(yc))
        ln.public = cell_number(crop("public", cx, yc))
        ln.tax_unit = cell_number(crop("tax_unit", cx, yc), psm=7)
        ln.pharm_price = cell_number(crop("pharm", cx, yc))
        ln.total = cell_number(crop("total", cx, yc))
        ln.tax_total = cell_number(crop("tax_total", cx, yc))
        ln.margin_pharm = _read(crop("margin_pharm", cx, yc), _margin)
        ln.margin_dist = _read(crop("margin_dist", cx, yc), _margin)
        ln.qty_ocr = _read(crop("qty", cx, yc), _qty_cell)
        ln.expiry = _read(crop("expiry", cx, yc), _expiry) or ""
        ln.batch = cell_alnum(crop("batch", cx, yc))
        d = [t for t in anchors if abs(t.cy - yc) < 12 * s]
        if d:
            ln.disc_pct = float(d[0].text)
        else:  # anchor was inferred by gap-filling: read the discount cell directly
            hw = 30 * s
            y0, y1 = int(max(yc - BAND_UP * s, 0)), int(min(yc + BAND_DOWN * s, H))
            ln.disc_pct = cell_number(clean[y0:y1, int(max(cx - hw, 0)):int(cx + hw)], psm=7)
        _validate(ln)
        x_item = cx + (564 + 255) * s
        x_make = cx + (564 + 85) * s
        y0, y1 = int(max(yc - BAND_UP * s, 0)), int(min(yc + BAND_DOWN * s, H))
        ln.name_ar = arabic_text(clean[y0:y1, int(x_item):int(W - 0.03 * W)])
        ln.maker_ar = arabic_text(clean[y0:y1, int(x_make):int(x_item) - 5], psm=7)
        lines.append(ln)
    if any(l.ok for l in lines):
        dropped = [l for l in lines if not l.ok]
        if dropped:
            warn.append(f"dropped {len(dropped)} inconsistent row(s) (likely totals-box tokens)")
        lines = [l for l in lines if l.ok]
    return {"lines": [l.to_dict() for l in lines], "scale": round(s, 3), "warnings": warn,
            "totals": _printed_values(clean, rows, s)}


def _printed_values(clean: np.ndarray, rows: List[tuple], s: float) -> Dict:
    """Numbers printed in the totals box under the table (taxable / non-taxable value, VAT, grand).

    The box uses thousands separators ('3,674.600'), so it is read with its own digit+comma OCR.
    """
    if not rows:
        return {}
    import pytesseract
    H, W = clean.shape
    y_last = max(y for _, y in rows)
    y0, y1 = int(y_last + 60 * s), int(min(H, y_last + 560 * s))
    if y1 <= y0:
        return {}
    crop = clean[y0:y1, int(0.10 * W):int(0.98 * W)]
    txt = pytesseract.image_to_string(crop, lang="eng",
                                      config="--psm 11 -c tessedit_char_whitelist=0123456789.,")
    vals = sorted({round(float(m.replace(",", "")), 3)
                   for m in re.findall(r"\d{1,3}(?:,\d{3})+\.\d{2,3}|\d+\.\d{2,3}", txt)})
    ints = sorted({int(m) for m in re.findall(r"(?<![\d.,])\d{1,5}(?![\d.,])", txt)})
    return {"printed_values": vals, "printed_ints": ints}


def _read(img: np.ndarray, parse, psms=(6, 7, 11)):
    """Try several page-segmentation modes; return the first reading that passes ``parse``."""
    for psm in psms:
        v = parse(cell_text(img, psm=psm))
        if v is not None:
            return v
    return None


def _expiry(txt: str) -> Optional[str]:
    """MM.YYYY with a sane month/year ('062027' and '06 2027' are accepted, '0.20273' is not)."""
    digits = re.sub(r"\D", "", txt or "")
    for m in re.finditer(r"(0[1-9]|1[0-2])(20[2-3]\d)", digits):
        return f"{m.group(1)}.{m.group(2)}"
    return None


def _margin(txt: str) -> Optional[float]:
    """Fixed margins are multiples of 0.5 (0, 1, 1.5, 2 ...); '1.53' is '1.5' plus a stray dot-line digit."""
    t = (txt or "").strip(".")
    m = re.match(r"^(\d{1,2})(?:\.(\d))?", t)
    if not m or not t:
        return None
    v = float(m.group(1) + ("." + m.group(2) if m.group(2) else ""))
    return v if v <= 5 and abs(v * 2 - round(v * 2)) < 1e-9 else None


def _qty_cell(txt: str) -> Optional[float]:
    t = re.sub(r"[^\d]", "", txt or "")
    return float(t) if t and len(t) <= 4 and int(t) >= 1 else None


def _near_int(q: float) -> Optional[int]:
    qi = round(q)
    return qi if 1 <= qi <= 5000 and abs(q - qi) <= max(0.012, 0.004 * qi) else None


def _validate(ln: PoLine) -> None:
    """Choose the pharmacist unit price that makes the row arithmetic consistent.

    Sources for the unit price (excl. VAT): the OCR cell, tax_unit/0.14, and
    public*(1-disc). total = qty * (unit + tax_unit) must give an integer qty.
    """
    tax = ln.tax_unit or 0.0
    if tax < 0.05:  # '0.000' is often read as '0.0002'; real VAT per unit is never this small
        tax = 0.0
        ln.tax_unit = 0.0
    cands = []  # (label, value)
    if ln.pharm_price:
        cands.append(("ocr", ln.pharm_price))
    if tax > 0:
        cands.append(("tax", round(tax / TAX, 3)))
    if ln.public and ln.disc_pct is not None and ln.disc_pct < 100:
        cands.append(("public", round(ln.public * (1 - ln.disc_pct / 100) / ((1 + TAX) if tax > 0 else 1.0), 3)))
    for label, v in list(cands):
        if v >= 400:  # '225.0' + '0' sometimes becomes 2250.0
            cands.append((label + "/10", round(v / 10, 3)))
            cands.append((label + "/100", round(v / 100, 3)))
    chosen = None
    for label, v in cands:
        if ln.total and v > 0:
            q = _near_int(ln.total / (v + tax))
            if q:
                chosen = (label, v, q)
                break
    if chosen:
        label, v, q = chosen
        if label != "ocr" and ln.pharm_price and abs(ln.pharm_price - v) / v > 0.005:
            ln.flags.append(f"pharm_price {ln.pharm_price} -> {v} ({label})")
        ln.pharm_price = v
        if ln.qty_ocr is not None and abs(ln.qty_ocr - q) > 0.5:
            ln.flags.append(f"qty OCR {ln.qty_ocr} != computed {q}")
        ln.qty = float(q)
        ln.ok = True
    else:
        ln.qty = ln.qty_ocr
        ln.ok = False
        ln.flags.append("no consistent unit price/qty")
    # public price: OCR primary; replace only when >4% off the invoice's own discount maths
    if ln.pharm_price and ln.disc_pct is not None and ln.disc_pct < 100:
        gross = ln.pharm_price * ((1 + TAX) if tax > 0 else 1.0)
        derived = round(gross / (1 - ln.disc_pct / 100), 2)
        if derived > 0 and (ln.public is None or abs(ln.public - derived) / derived > 0.04):
            ln.flags.append(f"public {ln.public} -> derived {derived}")
            ln.public = derived
