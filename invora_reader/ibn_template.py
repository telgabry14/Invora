"""ibnsina pharma 'Statement Cash / Cash & Dated Statement' reader.

Columns (right -> left): batch/expiry | name | qty/bonus | total qty | unit price | supplier code |
supplier % | ibnsina % | cash % | customer disc | VAT value | VAT rate | total before VAT.
Row anchor = VAT-rate cell (``0.14`` / ``0.00``), a clean, short token on every row.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

import numpy as np

from .ocr import Tok, numeric_tokens, arabic_text, cell_number, cell_lines

RATE = re.compile(r"^0\.(14|00?)$")
# offsets from the VAT-rate cell centre, px at reference scale (W=1748, digit height 19.5)
OFF = {"pretax": -160, "vat": 109, "ibn_pct": 432, "sup_pct": 538, "code": 665,
       "price": 800, "tot_qty": 914, "qty": 1013, "name": 1100}
HALF_W = {"pretax": 72, "vat": 52, "ibn_pct": 40, "sup_pct": 40, "code": 50,
          "price": 48, "tot_qty": 38, "qty": 34}
BAND_UP, BAND_DOWN = 42.0, 52.0
DIGIT_H = 19.0


@dataclass
class IbnLine:
    y: int
    code_sup: Optional[str] = None
    qty: Optional[float] = None
    bonus: Optional[float] = None
    tot_qty: Optional[float] = None
    price: Optional[float] = None
    pretax: Optional[float] = None
    vat: Optional[float] = None
    vat_rate: Optional[float] = None
    sup_pct: Optional[float] = None
    ibn_pct: Optional[float] = None
    name_ar: str = ""
    flags: List[str] = field(default_factory=list)
    ok: bool = False

    def to_dict(self) -> Dict:
        return asdict(self)


def _anchors(toks: List[Tok], W: int) -> List[Tok]:
    cand = [t for t in toks if RATE.match(t.text) and t.conf >= 80]
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


def _tok_value(toks: List[Tok], cx: float, yc: float, s: float, key: str) -> Optional[float]:
    """Prefer a confident page-level token inside the cell window (more accurate than a tiny crop)."""
    xc, hw = cx + OFF[key] * s, HALF_W[key] * s
    hit = [t for t in toks if t.conf >= 85 and abs(t.cx - xc) <= hw and -BAND_UP * s <= t.cy - yc <= BAND_DOWN * s]
    if len(hit) != 1:
        return None
    try:
        return float(hit[0].text.strip(".-/"))
    except ValueError:
        return None


def read_page(clean: np.ndarray) -> Dict:
    H, W = clean.shape
    toks = numeric_tokens(clean, min_conf=40)
    anchors = _anchors(toks, W)
    if not anchors:
        return {"lines": [], "scale": None, "totals": {}, "warnings": ["no row anchors (VAT-rate column) found"]}
    s = 1.0  # fixed px layout at 300 dpi (digit height is not a scale)
    lines: List[IbnLine] = []
    warn: List[str] = []

    def box(cx, yc, key):
        xc = cx + OFF[key] * s
        hw = HALF_W[key] * s
        y0, y1 = int(max(yc - BAND_UP * s, 0)), int(min(yc + BAND_DOWN * s, H))
        return clean[y0:y1, int(max(xc - hw, 0)):int(min(xc + hw, W))]

    for a in anchors:
        cx, yc = a.cx, a.cy
        ln = IbnLine(y=int(yc), vat_rate=float(a.text))
        def val(key, psm=7):
            v = _tok_value(toks, cx, yc, s, key)
            return v if v is not None else cell_number(box(cx, yc, key), psm=psm)

        ln.price = val("price")
        ln.vat = val("vat")
        ln.pretax = val("pretax")
        ln.sup_pct = val("sup_pct")
        ln.ibn_pct = val("ibn_pct")
        code = "".join(ch for ch in "".join(cell_lines(box(cx, yc, "code"))) if ch.isdigit())
        ln.code_sup = code or None
        ql = cell_lines(box(cx, yc, "qty"))
        tq = cell_number(box(cx, yc, "tot_qty"), psm=7)
        ln.tot_qty = tq
        if ql:
            try:
                ln.qty = float(ql[0])
                ln.bonus = float(ql[1]) if len(ql) > 1 else 0.0
            except ValueError:
                pass
        _validate(ln)
        x0 = int(cx + OFF["name"] * s)
        y0, y1 = int(max(yc - BAND_UP * s, 0)), int(min(yc + BAND_DOWN * s, H))
        ln.name_ar = arabic_text(clean[y0:y1, x0:min(W, x0 + int(330 * s))])
        lines.append(ln)
    if any(l.ok for l in lines):
        bad = [l for l in lines if not l.ok]
        if bad:
            warn.append(f"dropped {len(bad)} inconsistent row(s)")
        lines = [l for l in lines if l.ok]
    return {"lines": [l.to_dict() for l in lines], "scale": round(s, 3),
            "totals": _totals(toks, lines, W), "warnings": warn}


def _validate(ln: IbnLine) -> None:
    """Pick the pre-VAT total that gives an integer qty.

    Candidates: OCR cell, and derived from VAT: net = vat/rate, then undo the two
    sequential discounts (supplier %, then ibnsina % of what is left).
    """
    sp = ln.sup_pct if ln.sup_pct is not None and ln.sup_pct < 0.5 else 0.0
    ip = ln.ibn_pct if ln.ibn_pct is not None and ln.ibn_pct < 0.5 else 0.0
    cands = []
    if ln.pretax:
        cands.append(("ocr", ln.pretax))
    if ln.vat and ln.vat_rate:
        net = ln.vat / ln.vat_rate
        cands.append(("vat", round(net / ((1 - sp) * (1 - ip)), 2)))
    if ln.price:
        for label, pt in cands:
            q = pt / ln.price
            qi = round(q)
            if qi >= 1 and abs(q - qi) <= max(0.02, 0.004 * qi):
                if label != "ocr" and ln.pretax and abs(ln.pretax - pt) > 0.01 * pt:
                    ln.flags.append(f"pretax {ln.pretax} -> {pt} ({label})")
                ln.pretax, ln.qty, ln.ok = round(qi * ln.price, 2), float(qi), True
                break
    if not ln.ok:
        ln.flags.append("no consistent qty*price/pretax")
    if ln.ok:
        # bonus is only trusted as (total qty - qty); handwriting around the qty cell corrupts the rest
        if ln.tot_qty is not None and ln.qty <= ln.tot_qty <= ln.qty * 3 + 50:
            ln.bonus = ln.tot_qty - ln.qty
        else:
            if ln.bonus:
                ln.flags.append(f"bonus {ln.bonus} unverified -> 0")
            ln.bonus = 0.0


def _totals(toks: List[Tok], lines: List[IbnLine], W: int) -> Dict:
    """Totals row under the table, left->right: grand, VAT, ibnsina disc, supplier disc, price sum.

    The identity price_sum - supplier - ibnsina + vat = grand repairs one lost digit.
    """
    if not lines:
        return {}
    ymax = max(l.y for l in lines)
    below = [t for t in toks if t.cy > ymax + 140 and t.conf >= 85 and re.match(r"^\d+\.\d{2}$", t.text)]
    if not below:
        return {}
    key = lambda t: round(t.cy / 30)
    row_k = max({key(t) for t in below}, key=lambda k: sum(1 for t in below if key(t) == k))
    vals = [float(t.text) for t in sorted([t for t in below if key(t) == row_k], key=lambda t: t.cx)]
    if len(vals) != 5:
        return {"raw": vals}
    grand, vat, ibn, sup, price_sum = vals
    out = {"grand": grand, "vat": vat, "ibn_disc": ibn, "sup_disc": sup, "price_sum": price_sum}
    resid = price_sum - sup - ibn + vat - grand
    if abs(resid) > 0.05:  # try repairing a single dropped leading digit in ibn / sup / vat
        for name in ("ibn_disc", "sup_disc", "vat"):
            base = out[name]
            for lead in range(1, 10):
                cand = float(f"{lead}{base:.2f}")
                trial = dict(out); trial[name] = cand
                r = trial["price_sum"] - trial["sup_disc"] - trial["ibn_disc"] + trial["vat"] - trial["grand"]
                if abs(r) <= 0.05:
                    out[name] = cand; out["repaired"] = name
                    break
            if "repaired" in out:
                break
    out["consistent"] = abs(out["price_sum"] - out["sup_disc"] - out["ibn_disc"] + out["vat"] - out["grand"]) <= 0.05
    return out
