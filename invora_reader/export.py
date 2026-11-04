"""Build the upload table: one row per invoice line, split into 'auto' and 'review'.

Thresholds were measured on the sample against Softec entries (leave-one-out):
  PharmaOverseas  score>=90 and gap>=10  -> 79% of lines, 96.8% correct
  ibnsina         no threshold reached a safe precision yet -> every line is 'review'
"""
from __future__ import annotations

from typing import Dict, List

FAMILY_NAME = {"po": "PharmaOverseas", "ibn": "ابن سينا"}
AUTO_RULE = {"po": (90.0, 10.0)}      # template -> (min score, min gap to 2nd candidate)


def status_for(template: str, line: Dict, cands: List[Dict]) -> str:
    if not cands or not line.get("ok") or not line.get("qty"):
        return "review"
    rule = AUTO_RULE.get(template)
    if not rule:
        return "review"
    gap = cands[0]["score"] - (cands[1]["score"] if len(cands) > 1 else 0.0)
    return "auto" if cands[0]["score"] >= rule[0] and gap >= rule[1] else "review"


def _unit_net(l: Dict):
    return l.get("pharm_price") or l.get("price")


def rows_for_invoice(inv: Dict) -> List[Dict]:
    out = []
    t = inv["template"]
    for n, r in enumerate(inv.get("rows", []), start=1):
        l, c = r["line"], r["cands"]
        gap = (c[0]["score"] - (c[1]["score"] if len(c) > 1 else 0.0)) if c else None
        vat_rate = 0.14 if (l.get("vat") or l.get("tax_unit") or 0) > 0 else 0.0
        sysl = r["system"]["line"] if r.get("system") else None
        out.append({
            "status": status_for(t, l, c),
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
            "public_price": l.get("public"),
            "match_score": c[0]["score"] if c else "",
            "match_gap": round(gap, 1) if gap is not None else "",
            "alt_2": f'{c[1]["code"]} {c[1]["name"]}' if len(c) > 1 else "",
            "alt_3": f'{c[2]["code"]} {c[2]["name"]}' if len(c) > 2 else "",
            "name_on_scan": l.get("name_ar", ""),
            "flags": "; ".join(l.get("flags", [])),
            "softec_doc": (inv.get("system_candidates") or [{}])[0].get("doc", ""),
            "softec_code": sysl["code"] if sysl else "",
            "softec_unit_price": sysl["price"] if sysl else "",
            "code_agrees_with_softec": (c[0]["code"] == sysl["code"]) if (c and sysl) else "",
        })
    return out
