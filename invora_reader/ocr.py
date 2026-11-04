"""Thin Tesseract helpers: numeric tokens with boxes, and Arabic text crops."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pytesseract

NUM_CFG = "--psm 11 -c tessedit_char_whitelist=0123456789.-/"


@dataclass(frozen=True)
class Tok:
    x: int
    y: int
    w: int
    h: int
    text: str
    conf: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


def numeric_tokens(img: np.ndarray, x0: int = 0, y0: int = 0, min_conf: float = 0.0) -> List[Tok]:
    """Digit-only OCR with word boxes, in full-image coordinates."""
    d = pytesseract.image_to_data(img, lang="eng", config=NUM_CFG, output_type=pytesseract.Output.DICT)
    out = []
    for i, t in enumerate(d["text"]):
        t = t.strip()
        if not t:
            continue
        try:
            c = float(d["conf"][i])
        except ValueError:
            continue
        if c < min_conf:
            continue
        out.append(Tok(d["left"][i] + x0, d["top"][i] + y0, d["width"][i], d["height"][i], t, c))
    return out


def arabic_text(img: np.ndarray, psm: int = 6) -> str:
    """Printed Arabic (+ embedded Latin/digits) from a cell crop, whitespace-normalised."""
    t = pytesseract.image_to_string(img, lang="ara+eng", config=f"--psm {psm}")
    return " ".join(t.split())


def _prep_cell(img: np.ndarray, up: float = 2.0, pad: int = 14) -> np.ndarray:
    import cv2
    img = cv2.resize(img, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
    return cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)


def cell_text(img: np.ndarray, psm: int = 6) -> str:
    """Digits and '.' only from one cell; wrapped lines are concatenated."""
    t = pytesseract.image_to_string(_prep_cell(img), lang="eng",
                                    config=f"--psm {psm} -c tessedit_char_whitelist=0123456789.")
    return "".join(t.split())


def cell_number(img: np.ndarray, psm: int = 6) -> Optional[float]:
    """Read one numeric cell (may be wrapped over two lines, e.g. '175.2' / '50')."""
    s = cell_text(img, psm).strip(".")
    if s.count(".") > 1:
        head, _, tail = s.partition(".")
        s = head + "." + tail.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def cell_lines(img: np.ndarray, psm: int = 6) -> list:
    """Digit/'.' text per visual line (e.g. qty on line 1, bonus on line 2)."""
    t = pytesseract.image_to_string(_prep_cell(img), lang="eng",
                                    config=f"--psm {psm} -c tessedit_char_whitelist=0123456789.")
    return [ln.strip() for ln in t.splitlines() if ln.strip()]


def cell_alnum(img: np.ndarray, psm: int = 6) -> str:
    """Batch numbers: capital letters and digits; wrapped lines are concatenated ('26083' + '04')."""
    t = pytesseract.image_to_string(_prep_cell(img), lang="eng",
                                    config=f"--psm {psm} -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    return "".join(t.split())


def cell_lines_alnum(img: np.ndarray, psm: int = 6) -> list:
    """Text per visual line, letters+digits+'-' (e.g. '20-0' / 'Reg', or '20290101' / '3262030')."""
    t = pytesseract.image_to_string(_prep_cell(img), lang="eng",
                                    config=f"--psm {psm} -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-")
    return [ln.strip() for ln in t.splitlines() if ln.strip()]
