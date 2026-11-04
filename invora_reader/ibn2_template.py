"""ibnsina pharma, layout B (Oct 2026 onwards, ISO date, English item names).

Columns right -> left: bag | name (English) | qty/bonus | total qty | supplier code | batch/expiry |
public | pharmacist price | disc% | fixed margin | VAT | total before VAT.
Row anchor = supplier item code (6 digits), one per row in a clean column.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

import numpy as np

from .ocr import Tok, numeric_tokens, cell_number, cell_lines, arabic_text

# offsets from the code-cell centre (px at reference scale: digit height 19)
OFF = {"public": -305, "pharm": -451, "disc": -605, "margin": -745, "vat": -856,
       "pretax": -1190, "tot_qty": 163, "qty_b": 228}
HALF_W = {"public": 50, "pharm": 60, "disc": 46, "margin": 40, "vat": 50, "pretax": 70, "tot_qty": 36, "qty_b": 40}
BAND_UP, BAND_DOWN = 32.0, 84.0
NAME_X = (285, 500)      # item-name column span right of the code, px
NAME_UP, NAME_DOWN = 58.0, 92.0  # names wrap to 3-4 lines and start above the code row
DIGIT_H = 19.0
VAT_RATE = 0.14
CODE = re.compile(r"^\d{6}$")


@dataclass
class Ibn2Line:
    y: int
    code_sup: Optional[str] = None
    qty: Optional[float] = None
    public: Optional[float] = None
    pharm_price: Optional[float] = None
    disc_pct: Optional[float] = None
    margin: Optional[float] = None
    vat: Optional[float] = None
    pretax: Optional[float] = None
    tot_qty: Optional[float] = None
    total: Optional[float] = None       # unit-price * qty incl. VAT (what the system line should be)
    name_en: str = ""
    name_ar: str = ""                   # kept for pipeline compatibility (English names live here too)
    flags: List[str] = field(default_factory=list)
    ok: bool = False

    def to_dict(self) -> Dict:
        return asdict(self)


def _anchors(toks: List[Tok], W: int, H: int = 0) -> List[Tok]:
    cand = [t for t in toks if CODE.match(t.text) and t.conf >= 80 and t.h < 40
            and abs(t.cx / W - 0.585) < 0.07 and (not H or t.cy > 0.12 * H)]
    if not cand:
        return []
    cols, cur = [], []
    for t in sorted(cand, key=lambda t: t.cx):
        if cur and t.cx - cur[-1].cx > 0.015 * W + 14:
            cols.append(cur); cur = []
        cur.append(t)
    cols.append(cur)
    main = max(cols, key=len)
    return sorted(main, key=lambda t: t.cy)


def _tok_value(toks, cx, yc, s, key) -> Optional[float]:
    xc, hw = cx + OFF[key] * s, HALF_W[key] * s
    hit = [t for t in toks if t.conf >= 85 and abs(t.cx - xc) <= hw and -BAND_UP * s <= t.cy - yc <= BAND_DOWN * s]
    if len(hit) != 1:
        return None
    try:
        return float(hit[0].text.strip(".-/").split("-")[0])
    except ValueError:
        return None


def read_page(clean: np.ndarray) -> Dict:
    H, W = clean.shape
    toks = numeric_tokens(clean, min_conf=40)
    anchors = _anchors(toks, W, H)
    if not anchors:
        return {"lines": [], "scale": None, "totals": {}, "warnings": ["no row anchors (supplier code column)"]}
    s = float(np.median([t.h for t in anchors])) / DIGIT_H
    lines: List[Ibn2Line] = []

    def box(cx, yc, key):
        xc, hw = cx + OFF[key] * s, HALF_W[key] * s
        y0, y1 = int(max(yc - BAND_UP * s, 0)), int(min(yc + BAND_DOWN * s, H))
        return clean[y0:y1, int(max(xc - hw, 0)):int(min(xc + hw, W))]

    for idx, a in enumerate(anchors):
        cx, yc = a.cx, a.cy
        prev_y = anchors[idx - 1].cy if idx > 0 else None
        next_y = anchors[idx + 1].cy if idx + 1 < len(anchors) else None
        ln = Ibn2Line(y=int(yc), code_sup=a.text)

        def val(key, psm=7):
            v = _tok_value(toks, cx, yc, s, key)
            return v if v is not None else cell_number(box(cx, yc, key), psm=psm)

        ln.public, ln.pharm_price, ln.vat = val("public"), val("pharm"), val("vat")
        ln.pretax = val("pretax")
        ml = cell_lines(box(cx, yc, "margin"), psm=6)      # pharmacist margin on line 1, distributor on line 2
        try:
            ln.margin = float(ml[0]) if ml else 0.0
        except ValueError:
            ln.margin = 0.0
        dl = cell_lines(box(cx, yc, "disc"), psm=7)
        ln.disc_pct = _tok_value(toks, cx, yc, s, "disc")
        if ln.disc_pct is None and dl:
            try:
                ln.disc_pct = float(dl[0].split(".")[0] if dl[0].count(".") > 1 else dl[0])
            except ValueError:
                pass
        ql = cell_lines(box(cx, yc, "qty_b"), psm=6)   # qty on line 1, bonus on line 2
        ln.tot_qty = None
        for t in ql[:1]:
            try:
                ln.tot_qty = float(t)
            except ValueError:
                pass
        if ln.tot_qty is None:
            ln.tot_qty = val("tot_qty")
        _validate(ln)
        x0 = int(cx + NAME_X[0] * s)
        top = yc - NAME_UP * s
        bot = yc + NAME_DOWN * s
        if prev_y is not None:   # never read into the neighbouring row's name
            top = max(top, (prev_y + yc) / 2 - 8 * s)
        if next_y is not None:
            bot = min(bot, (yc + next_y) / 2 - 8 * s)
        y0, y1 = int(max(top, 0)), int(min(bot, H))
        import pytesseract
        t = pytesseract.image_to_string(clean[y0:y1, x0:min(W, int(cx + NAME_X[1] * s))], lang="eng", config="--psm 6")
        ln.name_en = ln.name_ar = " ".join(t.split())
        lines.append(ln)
    warn = [f"{sum(1 for l in lines if not l.ok)} row(s) need review"] if any(not l.ok for l in lines) else []
    totals = _totals(toks, anchors, s)
    return {"lines": [l.to_dict() for l in lines], "scale": round(s, 3), "totals": totals, "warnings": warn}


def _validate(ln: Ibn2Line) -> None:
    """qty must make pretax = qty * pharm price (and VAT = 14% of pretax when taxable)."""
    p = ln.pharm_price
    if not p:
        ln.flags.append("no pharmacist price")
        return
    cands = []
    if ln.vat and ln.vat > 0:
        cands.append(("vat", ln.vat / VAT_RATE / p))
    if ln.pretax:
        cands.append(("pretax", ln.pretax / p))
    if ln.tot_qty:
        cands.append(("ocr", ln.tot_qty))
    for label, q in cands:
        qi = round(q)
        if 1 <= qi <= 5000 and abs(q - qi) <= max(0.03, 0.01 * qi):
            if label == "ocr" and ln.vat and ln.vat > 0:
                continue  # taxable rows must be confirmed by VAT/pretax (handled below if unconfirmed)
            ln.qty, ln.ok = float(qi), True
            if ln.tot_qty and abs(ln.tot_qty - qi) > 0.5:
                ln.flags.append(f"qty OCR {ln.tot_qty} -> {qi} ({label})")
            break
    if not ln.ok and ln.tot_qty and 1 <= ln.tot_qty <= 5000 and ln.tot_qty == int(ln.tot_qty):
        ln.qty, ln.ok = float(ln.tot_qty), True
        ln.flags.append("qty from printed total-qty cell only (unverified)")
    if not ln.ok:
        ln.flags.append("no consistent qty")
        return
    rate = VAT_RATE if (ln.vat and ln.vat > 0) else 0.0
    ln.total = round(ln.qty * p * (1 + rate), 2)
    # public price: printed value is sometimes covered by handwriting; the pharmacist price is
    # public*(1-disc) within ~2%, so replace an OCR value that is >5% off that relation
    if ln.disc_pct is not None and ln.disc_pct < 60:
        derived = round(p / (1 - ln.disc_pct / 100), 2)
        if ln.public is None or abs(ln.public - derived) / derived > 0.05:
            ln.flags.append(f"public {ln.public} -> derived {derived}")
            ln.public = derived


def _totals(toks: List[Tok], anchors: List[Tok], s: float) -> Dict:
    """Totals row under the table: net invoice total is the left-most decimal, price sum the right-most."""
    if not anchors:
        return {}
    y_last = max(a.cy for a in anchors)
    below = [t for t in toks if t.cy > y_last + 150 * s and t.conf >= 80 and re.fullmatch(r"\d+\.\d{1,2}", t.text)]
    if not below:
        return {}
    # the totals row is the first text row below the table
    y0 = min(t.cy for t in below)
    row = [t for t in below if abs(t.cy - y0) <= 25 * s]
    if len(row) < 2:
        return {}
    row.sort(key=lambda t: t.cx)
    grand, price_sum = float(row[0].text), float(row[-1].text)
    return {"grand": grand, "price_sum": price_sum, "values_left_to_right": [t.text for t in row]}
