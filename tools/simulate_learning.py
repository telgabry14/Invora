"""How much does the review->learn loop help? A fair, held-out experiment.

Invoices linked to Softec are split in two halves. A "perfect reviewer" confirms the true codes of half A
(unambiguous qty+price pairs only), those confirmations are learned, and the matcher is re-run on half B,
which it has never seen. Reports top-1 / top-5 before and after.

    python tools/simulate_learning.py out/invoices.json --master master.xlsx --system a.htm b.htm
"""
import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from invora_reader import learned, link, system_export  # noqa: E402
from invora_reader.match import Master  # noqa: E402

TOL = {"po": 0.035, "ibnB": 0.025, "ibnA": 0.06}


def truth_pairs(inv, by):
    """[(row, true_code)] for unambiguous qty+price pairs in a linked invoice."""
    sysinv = by[inv["system_candidates"][0]["doc"]]
    lay = "po" if inv["template"] == "po" else ("ibnB" if any("margin" in l for l in inv["lines"]) else "ibnA")
    out = []
    for r in inv["rows"]:
        l = r["line"]
        ep = link._eff_price(l)
        if not ep or not l.get("qty"):
            continue
        m = [x for x in sysinv["items"] if abs(x["qty"] - l["qty"]) < 1e-6 and x["price"]
             and abs(ep - x["price"]) / x["price"] <= TOL[lay]]
        codes = {x["code"] for x in m}
        if len(codes) == 1:
            out.append((r, next(iter(codes))))
    return out


def evaluate(invs, by, master, history, store, taught=None):
    n = t1 = t5 = 0
    seen = [0, 0]                      # lines whose true code was taught: [count, top1 correct]
    for inv in invs:
        pairs = truth_pairs(inv, by)
        if not pairs:
            continue
        sub = dict(inv, lines=[r["line"] for r, _ in pairs])
        rows = link.line_candidates(sub, master, history=history, learned=store,
                                    exclude_doc=inv["system_candidates"][0]["doc"])
        for row, (_, code) in zip(rows, pairs):
            codes = [c["code"] for c in row["cands"]]
            n += 1
            t1 += bool(codes) and codes[0] == code
            t5 += code in codes
            if taught and code in taught:
                seen[0] += 1
                seen[1] += bool(codes) and codes[0] == code
    return n, t1, t5, seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("invoices_json")
    ap.add_argument("--master", required=True)
    ap.add_argument("--system", nargs="+", required=True)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    invs = [i for i in json.load(open(a.invoices_json, encoding="utf-8"))
            if i.get("system_candidates") and i.get("rows")
            and (i["template"] == "po" or i["system_candidates"][0].get("by") == "amount")]
    by = {s["doc"]: s for f in a.system for s in system_export.parse(f)}
    master = Master.from_xlsx(a.master)
    history = link.build_history(list(by.values()))
    random.Random(a.seed).shuffle(invs)
    A, B = invs[: len(invs) // 2], invs[len(invs) // 2:]
    for tag, test, teach in (("A->B", B, A), ("B->A", A, B)):
        store, taught = {}, set()
        for inv in teach:                       # the perfect reviewer confirms every unambiguous line of the teaching half
            for r, code in truth_pairs(inv, by):
                k = learned.alias_key(inv["template"], r["line"])
                taught.add(code)
                if k:
                    store.setdefault(k, {})
                    store[k][code] = store[k].get(code, 0) + 1
        n, b1, b5, sb = evaluate(test, by, master, history, {}, taught)
        _, a1, a5, sa = evaluate(test, by, master, history, store, taught)
        print(f"{tag}: {len(teach)} invoices taught ({len(store)} aliases) | held-out lines {n} | "
              f"top1 {100*b1/n:.1f}% -> {100*a1/n:.1f}% | top5 {100*b5/n:.1f}% -> {100*a5/n:.1f}%")
        if sa[0]:
            print(f"      lines of items seen while teaching: {sa[0]} ({100*sa[0]/n:.0f}% of held-out) | "
                  f"top1 {100*sb[1]/sb[0]:.1f}% -> {100*sa[1]/sa[0]:.1f}%")


if __name__ == "__main__":
    main()
