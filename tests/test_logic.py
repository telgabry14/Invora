"""Unit tests for the arithmetic validation, phonetic matching and system parser.

Run: python -m pytest tests -q   (no OCR / PDF needed)
Numbers come from real scanned invoices checked against the Softec export.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from invora_reader import ibn_template, po_template  # noqa: E402
from invora_reader.match import Master  # noqa: E402
from invora_reader.translit import numbers, skeleton_words  # noqa: E402


def test_skeleton_arabic_vs_latin():
    assert skeleton_words("ليبيتور 40 مجم 14 قرص") == skeleton_words("LIPITOR - 40MG - 14TAB") == ["lbtr"]
    assert skeleton_words("نستوجين 3 لبن 200 جرام")[0] == skeleton_words("NESTOGEN 3 200G MILK")[0]
    assert numbers("لينجالابس 25-5-1000 مجم 30") == numbers("LINGALABS 25/5/1000 30")


def test_master_ranks_known_items_first():
    m = Master([
        ("117718", "LIPITOR - 40MG - 14TAB", 226),
        ("83231", "LIPITOR - 80MG - 14TAB", 236),
        ("103158", "LIPITOR - 20MG - 30TAB (XX)", 240),
        ("111447", "NESTOGEN 3 200G MILK", 140),
    ])
    assert m.match("ليبيتور 40 مجم. 14 قرص", 226)[0].code == "117718"
    assert m.match("ليبيتور 80 مجم. 14 قرص", 236)[0].code == "83231"
    # public price on the invoice (135) is 3.6% below the master (140): still matched
    assert m.match("نستوجين 3 لبن 200 جرام", 135)[0].code == "111447"


def test_po_repairs_misread_unit_price_from_public_and_discount():
    # invoice 7876: OCR read the pharmacist price as 493.85, true 123.85; 10 x 123.85 = 1238.5
    ln = po_template.PoLine(y=0, public=135.0, tax_unit=0.0, disc_pct=8.26, pharm_price=493.85, total=1238.5)
    po_template._validate(ln)
    assert ln.ok and ln.qty == 10 and abs(ln.pharm_price - 123.85) < 0.01


def test_po_taxable_row_qty_from_total():
    # row 1 of the 2026-10-29 invoice: 4 x (175.25 + 24.54) = 799.16
    ln = po_template.PoLine(y=0, public=250.0, tax_unit=24.54, disc_pct=20.0, pharm_price=175.25, total=799.16)
    po_template._validate(ln)
    assert ln.ok and ln.qty == 4


def test_ibn_pretax_derived_from_vat_and_discounts():
    # invoice 9082 line 1: qty 12 x 24.93 = 299.16; OCR of the pre-VAT cell was garbage (216.0)
    ln = ibn_template.IbnLine(y=0, price=24.93, pretax=216.0, vat=39.81, vat_rate=0.14,
                              sup_pct=0.03, ibn_pct=0.02, tot_qty=12.0)
    ibn_template._validate(ln)
    assert ln.ok and ln.qty == 12 and abs(ln.pretax - 299.16) < 0.01 and ln.bonus == 0.0


def test_ibn_totals_identity_repairs_dropped_digit():
    from invora_reader.ocr import Tok
    lines = [ibn_template.IbnLine(y=100)]
    # grand, vat, ibn(117.72 read as 17.72), sup, price_sum  -- laid out left to right
    xs = [("6575.43", 100), ("807.54", 200), ("17.72", 300), ("28.07", 400), ("5913.68", 500)]
    toks = [Tok(x, 900, 80, 25, t, 96.0) for t, x in xs]
    out = ibn_template._totals(toks, lines, 1748)
    assert out["ibn_disc"] == 117.72 and out["consistent"]


def test_po_ignores_noise_tax_cell():
    # invoice 7876: '0.000' read as 0.0002 used to give pharm=0.001 and qty=1,032,083
    ln = po_template.PoLine(y=0, public=135.0, tax_unit=0.0002, disc_pct=8.26, pharm_price=493.85, total=1238.5)
    po_template._validate(ln)
    assert ln.ok and ln.qty == 10
