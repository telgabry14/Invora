"""Build the upload table: one row per invoice line, split into 'auto' and 'review'.

Thresholds were measured on the sample against Softec entries (leave-one-out):
  PharmaOverseas  score>=90 and gap>=15  -> 71% of lines, 97.7% correct (668 measured lines)
  ibnsina layout B score>=95 and gap>=15 -> ~41% of lines, 11/11 correct (only 27 measured lines: weak evidence)
  ibnsina layout A -> always review
  a learned alias confirmed >= 2 times -> auto
"""
from __future__ import annotations

import re
from typing import Dict, List

FAMILY_NAME = {"po": "PharmaOverseas", "ibn": "ابن سينا"}
AUTO_RULE = {"po": (90.0, 15.0), "ibn_b": (95.0, 15.0)}   # (min score, min gap to 2nd candidate)


def status_for(template: str, line: Dict, cands: List[Dict], learned: Dict = None) -> str:
    if not cands or not line.get("ok") or not line.get("qty"):
        return "review"
    if learned and cands[0]["code"] == learned["code"] and learned["count"] >= 2:
        return "auto"          # confirmed twice by a human before
    rule = AUTO_RULE.get("ibn_b" if (template == "ibn" and "margin" in line) else template)
    if not rule:
        return "review"
    gap = cands[0]["score"] - (cands[1]["score"] if len(cands) > 1 else 0.0)
    return "auto" if cands[0]["score"] >= rule[0] and gap >= rule[1] else "review"


def _unit_net(l: Dict):
    return l.get("pharm_price") or l.get("price")


def _expiry_iso(l: Dict) -> str:
    e = l.get("expiry") or ""
    if re.fullmatch(r"\d{2}\.\d{4}", e):          # PharmaOverseas MM.YYYY
        return f"{e[3:]}-{e[:2]}"
    if re.fullmatch(r"20\d{6}", e):                 # ibnsina YYYYMMDD
        return f"{e[:4]}-{e[4:6]}-{e[6:]}"
    return ""


def rows_for_invoice(inv: Dict) -> List[Dict]:
    out = []
    t = inv["template"]
    check = printed_total_check(inv)
    for n, r in enumerate(inv.get("rows", []), start=1):
        l, c = r["line"], r["cands"]
        gap = (c[0]["score"] - (c[1]["score"] if len(c) > 1 else 0.0)) if c else None
        vat_rate = 0.14 if (l.get("vat") or l.get("tax_unit") or 0) > 0 else 0.0
        sysl = r["system"]["line"] if r.get("system") else None
        out.append({
            "status": status_for(t, l, c, r.get("learned")),   # printed-total check is informational (column invoice_check)
            "invoice_check": check,
            "template": t,
            "layout": "B" if (t == "ibn" and "margin" in l) else "",
            "supplier_item_code": l.get("code_sup") or "",
            "supplier": FAMILY_NAME.get(t, t),
            "invoice_key": inv.get("key"),
            "scan_date": inv.get("date"),
            "file": inv.get("file"),
            "pages": ",".join(map(str, inv.get("pages", []))),
            "line": n,
            "item_code": c[0]["code"] if c else "",
            "item_name": c[0]["name"] if c else "",
            "qty": l.get("qty"),
            "bonus": l.get("bonus") or 0,
            "unit_price_net": _unit_net(l),
            "discount_pct": l.get("disc_pct") if l.get("disc_pct") is not None else l.get("sup_pct"),
            "vat_rate": vat_rate,
            "line_total": l.get("total") if l.get("total") is not None else l.get("pretax"),
            "qty_printed": l.get("qty_ocr") if t == "po" else l.get("tot_qty"),
            "batch": l.get("batch") or "",
            "expiry": _expiry_iso(l),
            "margin_pharm": l.get("margin_pharm") if t == "po" else l.get("margin"),
            "margin_dist": "" if t == "po" else l.get("margin_dist"),   # PO: overprinted by side text, unreadable
            "vat_per_unit": l.get("tax_unit") if t == "po" else "",
            "vat_line": l.get("tax_total") if t == "po" else l.get("vat"),
            "discount_tag": l.get("disc_tag") or "",
            "supplier_on_invoice": l.get("maker_ar") or "",
            "public_price": l.get("public"),
            "match_score": c[0]["score"] if c else "",
            "match_gap": round(gap, 1) if gap is not None else "",
            "alt_2": f'{c[1]["code"]} {c[1]["name"]}' if len(c) > 1 else "",
            "alt_3": f'{c[2]["code"]} {c[2]["name"]}' if len(c) > 2 else "",
            "name_on_scan": l.get("name_ar", ""),
            "learned_code": (r.get("learned") or {}).get("code", ""),
            "flags": "; ".join(l.get("flags", []) + []),
            "softec_doc": (inv.get("system_candidates") or [{}])[0].get("doc", ""),
            "softec_code": sysl["code"] if sysl else "",
            "softec_unit_price": sysl["price"] if sysl else "",
            "code_agrees_with_softec": (c[0]["code"] == sysl["code"]) if (c and sysl) else "",
        })
    return out


def is_noise(row: Dict) -> bool:
    """A row with no quantity and no usable price pair is a false row anchor (handwriting, stamps, totals box)."""
    if row.get("qty"):
        return False
    try:
        price = float(row.get("unit_price_net") or 0)
    except (TypeError, ValueError):
        price = 0.0
    return price <= 0 or not row.get("public_price")


def printed_total_check(inv: Dict) -> str:
    """PharmaOverseas: do the lines add up to what is printed on the invoice?

    'ok'         both section values (taxable pre-VAT / non-taxable) are printed and match, or the printed
                 grand total equals the lines plus a printed fixed margin
    'mismatch'   the grand total was read and the lines do not explain it (a line is missing or wrong)
    'unknown'    not enough was read to decide
    """
    if inv["template"] != "po":
        return "unknown"
    t = inv.get("totals", {})
    vals = t.get("printed_values") or []
    ints = t.get("printed_ints") or []
    lines = [l for l in inv["lines"] if l.get("ok") and l.get("total")]
    if not vals or not lines:
        return "unknown"

    def seen(x):
        return x == 0 or any(abs(v - x) <= max(0.06, 0.0004 * x) for v in vals)

    taxed = [l for l in lines if (l.get("tax_unit") or 0) > 0]
    nontax = round(sum(l["total"] for l in lines if not (l.get("tax_unit") or 0) > 0), 2)
    taxable_pre = round(sum(l["total"] - (l.get("tax_total") or l["total"] - l["total"] / 1.14) for l in taxed), 2)
    if seen(nontax) and seen(taxable_pre) and (nontax or taxable_pre):
        return "ok"
    total = round(sum(l["total"] for l in lines), 2)
    big = max(vals)
    if big >= 0.5 * total:                       # the grand total was read
        gap = round(big - total, 2)
        if abs(gap) <= 0.6 or any(abs(gap - m) <= 0.6 for m in ints) or any(abs(gap - 2 * m) <= 0.6 for m in ints):
            return "ok"
        return "mismatch"
    return "unknown"
