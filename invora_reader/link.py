"""Link a read invoice to its Softec entry and its lines to master codes."""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from .match import Master

FAMILY = {"po": "فارما اوفر سيز", "ibn": "ابن سينا"}


def scan_amount(inv: Dict) -> Optional[float]:
    """Invoice total as it should appear in the system (incl. VAT, after discounts)."""
    if inv["template"] == "ibn":
        g = inv["totals"].get("grand")
        if g:
            return g
        return None
    tot = [l["total"] for l in inv["lines"] if l.get("total")]
    return round(sum(tot), 2) if tot else None


def _d(s: Optional[str]) -> Optional[date]:
    try:
        y, m, d = map(int, s.split("-"))
        return date(y, m, d)
    except Exception:
        return None


def find_system_invoice(inv: Dict, system: List[Dict], day_before: int = 3, day_after: int = 30,
                        top: int = 3) -> List[Dict]:
    """Candidates by supplier family, date window and amount; best first."""
    amt, d0 = scan_amount(inv), _d(inv.get("date"))
    fam = FAMILY.get(inv["template"], "")
    out = []
    for s in system:
        if fam and fam not in s["supplier"]:
            continue
        if amt is None:
            continue
        diff = abs(s["amount"] - amt)
        if diff > max(0.6, 0.003 * amt):
            continue
        dd = _d(s["date"])
        gap = (dd - d0).days if (dd and d0) else None
        if gap is not None and not (-day_before <= gap <= day_after):
            continue
        out.append({"doc": s["doc"], "supplier": s["supplier"], "amount": s["amount"], "date": s["date"],
                    "amount_diff": round(diff, 2), "day_gap": gap})
    out.sort(key=lambda c: (c["amount_diff"], abs(c["day_gap"] or 0)))
    return out[:top]


def _eff_price(l: Dict) -> Optional[float]:
    return (l["total"] / l["qty"]) if l.get("total") and l.get("qty") else None


def line_candidates(inv: Dict, master: Master, k: int = 5) -> List[Dict]:
    rows = []
    for i, l in enumerate(inv["lines"]):
        public = l.get("public") if inv["template"] == "po" else None
        cands = master.match(l.get("name_ar", ""), public, k=k)
        rows.append({"idx": i, "line": l, "cands": [c.__dict__ for c in cands]})
    return rows


def assign_to_system(rows: List[Dict], sysinv: Dict) -> None:
    """Greedy one-to-one assignment of scan lines to system lines (qty, code, effective price)."""
    sl = sysinv["items"]
    scores = []
    for r in rows:
        l = r["line"]
        for j, s in enumerate(sl):
            sc = 0.0
            if l.get("qty") and abs(s["qty"] - l["qty"]) < 1e-6:
                sc += 50
            codes = [c["code"] for c in r["cands"]]
            if s["code"] in codes:
                sc += 50 - 8 * codes.index(s["code"])
            ep = _eff_price(l)
            if ep and s["price"] and abs(ep - s["price"]) / s["price"] < 0.01:
                sc += 30
            if sc > 0:
                scores.append((sc, r["idx"], j))
    used_r, used_s = set(), set()
    for sc, ri, sj in sorted(scores, reverse=True):
        if ri in used_r or sj in used_s:
            continue
        used_r.add(ri); used_s.add(sj)
        rows[ri]["system"] = {"line": sl[sj], "score": sc}
    for r in rows:
        r.setdefault("system", None)
