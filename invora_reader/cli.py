"""python -m invora_reader read scans/*.pdf --master master.xlsx [--system export.htm] --out out/"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

from . import export, learned, link, pipeline, system_export
from .match import Master


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="invora_reader")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read", help="OCR scanned invoices and match items to the master")
    r.add_argument("pdfs", nargs="+")
    r.add_argument("--master", required=True, help="item master .xlsx (Sheet1: code, name, price)")
    r.add_argument("--system", nargs="+", help="Softec purchase export .htm, to link invoices and verify matches")
    r.add_argument("--out", default="out")
    r.add_argument("--learned", help="alias memory (json) built with the 'learn' command")
    r.add_argument("--dpi", type=int, default=300)
    r.add_argument("--workers", type=int, default=4)
    L = sub.add_parser("learn", help="remember the item codes confirmed in a reviewed upload.csv")
    L.add_argument("reviewed_csv")
    L.add_argument("--store", default="learned.json")
    a = ap.parse_args(argv)
    if a.cmd == "learn":
        store = learned.load(a.store)
        n = learned.learn_csv(store, a.reviewed_csv)
        learned.save(store, a.store)
        print(f"learned {n} rows -> {a.store} ({len(store)} aliases)")
        return 0

    os.makedirs(a.out, exist_ok=True)
    master = Master.from_xlsx(a.master)
    system = [i for f in (a.system or []) for i in system_export.parse(f)]
    invoices = []
    for pdf in a.pdfs:
        cache = os.path.join(a.out, os.path.basename(pdf)[:-4] + ".pages.json")
        pages = pipeline.read_pdf(pdf, dpi=a.dpi, workers=a.workers, cache=cache)
        invoices.extend(pipeline.group_invoices(pages))

    out_rows = []
    memory = learned.load(a.learned)
    history = link.build_history(system) if system else None
    by_doc = {s["doc"]: s for s in system}
    for inv in invoices:
        cands = link.link_by_lines(inv, system) if system else []
        inv["system_candidates"] = cands
        # leave-one-out: the linked entry itself must not help to match its own lines
        rows = link.line_candidates(inv, master, history=history, learned=memory,
                                    exclude_doc=cands[0]["doc"] if cands else None)
        if cands:
            link.assign_to_system(rows, by_doc[cands[0]["doc"]])
        inv["rows"] = rows
        for r in rows:
            l, c = r["line"], r["cands"]
            sysl = r["system"]["line"] if r.get("system") else None
            out_rows.append({
                "file": inv["file"], "invoice_key": inv["key"], "template": inv["template"], "date": inv["date"],
                "name_ar": l.get("name_ar"), "qty": l.get("qty"), "bonus": l.get("bonus"),
                "public": l.get("public"), "disc_pct": l.get("disc_pct", l.get("sup_pct")),
                "ibn_pct": l.get("ibn_pct"), "total": l.get("total", l.get("pretax")),
                "top1_code": c[0]["code"] if c else None, "top1_name": c[0]["name"] if c else None,
                "top1_score": c[0]["score"] if c else None,
                "sys_doc": cands[0]["doc"] if cands else None,
                "sys_code": sysl["code"] if sysl else None, "sys_name": sysl["name"] if sysl else None,
                "top1_matches_system": (c[0]["code"] == sysl["code"]) if (c and sysl) else None,
                "flags": "; ".join(l.get("flags", [])),
            })
    every = [row for inv in invoices for row in export.rows_for_invoice(inv)]
    upload = [r for r in every if not export.is_noise(r)]
    rejected = [r for r in every if export.is_noise(r)]
    if rejected:
        with open(os.path.join(a.out, "rejected_rows.csv"), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rejected[0]))
            w.writeheader()
            w.writerows(rejected)
    summary = []
    for inv in invoices:
        rows = [r for r in export.rows_for_invoice(inv) if not export.is_noise(r)]
        if not rows:
            continue
        sysc = (inv.get("system_candidates") or [{}])[0]
        scan_total = link.scan_amount(inv)
        summary.append({
            "supplier": export.FAMILY_NAME.get(inv["template"], inv["template"]), "invoice_key": inv["key"],
            "scan_date": inv["date"], "file": inv["file"], "pages": ",".join(map(str, inv["pages"])),
            "invoice_check": export.printed_total_check(inv),
            "lines": len(rows), "auto": sum(r["status"] == "auto" for r in rows),
            "review": sum(r["status"] != "auto" for r in rows),
            "ready": all(r["status"] == "auto" for r in rows),
            "scan_total": scan_total, "softec_doc": sysc.get("doc", ""), "softec_amount": sysc.get("amount", ""),
            "total_diff": round(scan_total - sysc["amount"], 2) if (scan_total and sysc.get("amount")) else "",
        })
    if summary:
        with open(os.path.join(a.out, "invoices_summary.csv"), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(summary[0]))
            w.writeheader()
            w.writerows(summary)
        print(f"{sum(1 for x in summary if x['ready'])} of {len(summary)} invoices fully auto (ready)")
    empty = [i for i in invoices if not i["lines"]]
    if empty:
        with open(os.path.join(a.out, "invoices_without_lines.csv"), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["file", "pages", "template", "scan_date", "warnings"])
            for i in empty:
                w.writerow([i["file"], ",".join(map(str, i["pages"])), i["template"], i["date"], "; ".join(i["warnings"])])
        print(f"{len(empty)} invoices without readable lines -> invoices_without_lines.csv")
    if rejected:
        print(f"{len(rejected)} noise rows moved to rejected_rows.csv")
    if upload:
        with open(os.path.join(a.out, "upload.csv"), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(upload[0]))
            w.writeheader()
            w.writerows(upload)
        n_auto = sum(1 for r in upload if r["status"] == "auto")
        print(f"upload.csv: {len(upload)} lines, {n_auto} auto, {len(upload) - n_auto} review")
    with open(os.path.join(a.out, "invoices.json"), "w", encoding="utf-8") as f:
        json.dump(invoices, f, ensure_ascii=False, indent=1)
    if out_rows:
        with open(os.path.join(a.out, "lines.csv"), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(out_rows[0]))
            w.writeheader()
            w.writerows(out_rows)
    print(f"{len(invoices)} invoices, {len(out_rows)} lines -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
