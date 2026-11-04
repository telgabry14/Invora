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
    """Expected system unit price of a scan line (VAT incl.)."""
    if l.get("total") and l.get("qty"):
        if l.get("margin") is not None and l.get("pharm_price"):   # ibnsina layout B
            m = l["margin"] if 0 <= (l["margin"] or 0) <= 3 else 0.0   # margin cell is noisy; real values are 0-3 EGP
            return l["total"] / l["qty"] + m * (1 + (0.14 if (l.get("vat") or 0) > 0 else 0))
        return l["total"] / l["qty"]
    if l.get("price") and l.get("qty"):  # ibnsina: supplier price; system ~2.5% below gross incl. VAT
        return l["price"] * (1 + (l.get("vat_rate") or 0.14)) * 0.975
    return None


def _sig_hits(inv: Dict, s: Dict) -> int:
    """How many scan lines have a system line with the same qty and a price within tolerance."""
    tol = 0.035 if inv["template"] == "po" else 0.06
    used, hits = set(), 0
    for l in inv["lines"]:
        ep = _eff_price(l)
        if not ep or not l.get("qty"):
            continue
        for j, x in enumerate(s["items"]):
            if j in used or abs(x["qty"] - l["qty"]) > 1e-6 or not x["price"]:
                continue
            if abs(ep - x["price"]) / x["price"] <= tol:
                used.add(j); hits += 1
                break
    return hits


def link_by_lines(inv: Dict, system: List[Dict], day_before: int = 3, day_after: int = 30,
                  min_frac: float = 0.5) -> List[Dict]:
    """Candidates ranked by line-signature hits (robust to the few-EGP margin differences)."""
    d0 = _d(inv.get("date"))
    fam = FAMILY.get(inv["template"], "")
    n = sum(1 for l in inv["lines"] if l.get("qty") and _eff_price(l))
    out = []
    if inv["template"] == "ibn" and inv["totals"].get("grand"):
        g = inv["totals"]["grand"]
        for s in system:
            if fam not in s["supplier"] or abs(s["amount"] - g) > 0.05:
                continue
            dd = _d(s["date"])
            gap = (dd - d0).days if (dd and d0) else None
            if gap is not None and not (-day_before <= gap <= day_after):
                continue
            out.append({"doc": s["doc"], "supplier": s["supplier"], "amount": s["amount"], "date": s["date"],
                        "hits": None, "of": n, "day_gap": gap, "by": "amount"})
        if out:
            out.sort(key=lambda c: abs(c["day_gap"] or 0))
            return out[:3]
    if not n:
        return out
    for s in system:
        if fam and fam not in s["supplier"]:
            continue
        dd = _d(s["date"])
        gap = (dd - d0).days if (dd and d0) else None
        if gap is not None and not (-day_before <= gap <= day_after):
            continue
        if abs(len(s["items"]) - n) > max(2, 0.3 * n):
            continue
        h = _sig_hits(inv, s)
        if h >= max(1, min_frac * n):
            out.append({"doc": s["doc"], "supplier": s["supplier"], "amount": s["amount"], "date": s["date"],
                        "hits": h, "of": n, "day_gap": gap})
    out.sort(key=lambda c: (-c["hits"] / c["of"], abs(c["day_gap"] or 0)))
    return out[:3]


def build_history(system: List[Dict]) -> Dict:
    """code -> list of (family, unit price, doc). Used as a prior (leave the linked doc out)."""
    h: Dict = {}
    for s in system:
        fam = next((f for f in FAMILY.values() if f in s["supplier"]), None)
        for x in s["items"]:
            if x["price"]:
                h.setdefault(x["code"], []).append((fam, x["price"], s["doc"]))
    return h


def prior_boost(history: Dict, fam: Optional[str], price: Optional[float], exclude_doc: Optional[str]):
    """Return f(code) -> bonus points: bought before from this supplier, at a similar price."""
    def f(code: str) -> float:
        pts = 0.0
        recs = [r for r in history.get(code, []) if r[2] != exclude_doc]
        if any(r[0] == fam for r in recs):
            pts += 12
        if price and any(r[0] == fam and abs(r[1] - price) / r[1] <= 0.04 for r in recs):
            pts += 18
        return pts
    return f


def line_candidates(inv: Dict, master: Master, k: int = 5, history: Optional[Dict] = None,
                    exclude_doc: Optional[str] = None) -> List[Dict]:
    rows = []
    fam = FAMILY.get(inv["template"])
    for i, l in enumerate(inv["lines"]):
        public = l.get("public")
        boost = prior_boost(history, fam, _eff_price(l), exclude_doc) if history else None
        cands = master.match(l.get("name_ar", ""), public, k=k, boost=boost)
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
