"""Reproducible matching evaluation against Softec entries.

    python tools/evaluate.py out/invoices.json --system purchases1.htm purchases2.htm

A scan line counts as "known truth" only when exactly one Softec line of the linked entry has the same qty and a
close effective price (so ambiguous pairs are skipped and the codes proposed by the matcher never influence the
pairing). Then: top-1 / top-5 accuracy per supplier, and precision of the auto rules (score >= S and gap >= G).
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from invora_reader import link, system_export  # noqa: E402

TOL = {"po": 0.035, "ibnB": 0.025, "ibnA": 0.06}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("invoices_json")
    ap.add_argument("--system", nargs="+", required=True)
    a = ap.parse_args()
    inv = json.load(open(a.invoices_json, encoding="utf-8"))
    by = {s["doc"]: s for f in a.system for s in system_export.parse(f)}
    rows = []
    for i in inv:
        if not i.get("system_candidates"):
            continue
        lay = "po" if i["template"] == "po" else ("ibnB" if any("margin" in l for l in i["lines"]) else "ibnA")
        if lay != "po" and i["system_candidates"][0].get("by") != "amount":
            continue                                   # ibnsina links are only trusted when made by printed total
        sysinv = by[i["system_candidates"][0]["doc"]]
        for r in i["rows"]:
            l = r["line"]
            ep = link._eff_price(l)
            if not ep or not l.get("qty") or not r["cands"]:
                continue
            m = [x for x in sysinv["items"] if abs(x["qty"] - l["qty"]) < 1e-6 and x["price"]
                 and abs(ep - x["price"]) / x["price"] <= TOL[lay]]
            codes = {x["code"] for x in m}
            if len(codes) != 1:
                continue
            c = r["cands"]
            gap = c[0]["score"] - (c[1]["score"] if len(c) > 1 else 0)
            rows.append((lay, c[0]["score"], gap, c[0]["code"] in codes, bool(codes & {x["code"] for x in c})))
    print("lines with known truth:", collections.Counter(r[0] for r in rows))
    for lay in TOL:
        R = [r for r in rows if r[0] == lay]
        if R:
            print(f"{lay}: n={len(R)} top1={100*sum(r[3] for r in R)/len(R):.0f}% top5={100*sum(r[4] for r in R)/len(R):.0f}%")
    for lay in TOL:
        R = [r for r in rows if r[0] == lay]
        for sc, g in ((90, 10), (90, 15), (95, 15)):
            S = [r for r in R if r[1] >= sc and r[2] >= g]
            if S:
                print(f"  {lay} score>={sc} gap>={g}: auto {len(S)}/{len(R)} ({100*len(S)/len(R):.0f}%) precision {100*sum(r[3] for r in S)/len(S):.1f}%")


if __name__ == "__main__":
    main()
