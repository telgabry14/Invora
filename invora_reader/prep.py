"""PDF page rendering and scan clean-up: orientation, deskew, table-line removal."""
from __future__ import annotations

import re
from typing import Iterator, Tuple

import cv2
import numpy as np
import pytesseract


def render_pages(pdf_path: str, dpi: int = 300) -> Iterator[Tuple[int, np.ndarray]]:
    """Yield (page_number starting at 1, grayscale uint8 image) for every page."""
    import pymupdf  # imported lazily so the rest of the package works without it

    with pymupdf.open(pdf_path) as doc:
        for i, page in enumerate(doc, start=1):
            pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
            yield i, img.copy()


def binarize(gray: np.ndarray) -> np.ndarray:
    """Ink = 255. Adaptive so tinted/blue scans still work."""
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY_INV, 35, 15)


def _row_score(gray: np.ndarray) -> float:
    """High when text lines run horizontally (strong periodic row projection)."""
    small = cv2.resize(gray, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    ink = binarize(small).astype(np.float32)
    rows = ink.sum(axis=1)
    return float(np.var(rows) / (np.mean(rows) + 1e-6))


_DEC = re.compile(r"^\d{1,6}[.,]\d{2,4}$")


def _decimal_score(gray: np.ndarray) -> float:
    """Count of confident decimal numbers: used to tell 0deg from 180deg."""
    h, w = gray.shape
    scale = 1400.0 / max(h, w)
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    try:  # some rotations make Tesseract crawl; a timeout just scores that rotation 0
        d = pytesseract.image_to_data(small, lang="eng", config="--psm 6", timeout=25,
                                      output_type=pytesseract.Output.DICT)
    except RuntimeError:
        return 0.0
    s = 0.0
    for t, c in zip(d["text"], d["conf"]):
        try:
            c = float(c)
        except ValueError:
            continue
        if c >= 60 and _DEC.match(t.strip()):
            s += 1.0
    return s


def auto_orient(gray: np.ndarray) -> Tuple[np.ndarray, int]:
    """Rotate so text is upright. Returns (image, clockwise degrees applied)."""
    cands = [(0, gray), (90, cv2.rotate(gray, cv2.ROTATE_90_CLOCKWISE))]
    best_deg, best_img = max(cands, key=lambda c: _row_score(c[1]))
    flipped = cv2.rotate(best_img, cv2.ROTATE_180)
    if _decimal_score(flipped) > _decimal_score(best_img):
        return flipped, (best_deg + 180) % 360
    return best_img, best_deg


def deskew(gray: np.ndarray, max_angle: float = 8.0, step: float = 0.25) -> Tuple[np.ndarray, float]:
    """Projection-profile deskew: pick the angle whose row-sum profile is sharpest.

    Works on pages with no ruling (PharmaOverseas) as well as ruled ones.
    """
    small = cv2.resize(gray, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    ink = binarize(small)
    h, w = ink.shape
    best_a, best_s = 0.0, -1.0
    for a in np.arange(-max_angle, max_angle + 1e-6, step):
        m = cv2.getRotationMatrix2D((w / 2, h / 2), float(a), 1.0)
        r = cv2.warpAffine(ink, m, (w, h), flags=cv2.INTER_NEAREST)
        rows = r.sum(axis=1).astype(np.float64)
        s = float(np.sum(np.diff(rows) ** 2))
        if s > best_s:
            best_a, best_s = float(a), s
    if abs(best_a) < 0.1:
        return gray, 0.0
    H, W = gray.shape
    m = cv2.getRotationMatrix2D((W / 2, H / 2), best_a, 1.0)
    return cv2.warpAffine(gray, m, (W, H), flags=cv2.INTER_CUBIC, borderValue=255), best_a


def strip_lines(gray: np.ndarray) -> np.ndarray:
    """Erase table rules so Tesseract does not read them as ':' '|' noise."""
    b = binarize(gray)
    h, w = gray.shape
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 25, 20), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(h // 40, 20)))
    lines = cv2.add(cv2.morphologyEx(b, cv2.MORPH_OPEN, hk), cv2.morphologyEx(b, cv2.MORPH_OPEN, vk))
    lines = cv2.dilate(lines, np.ones((3, 3), np.uint8))
    out = gray.copy()
    out[lines > 0] = 255
    return out


def clean_page(gray: np.ndarray) -> Tuple[np.ndarray, dict]:
    """orient -> deskew -> strip rules. Returns (clean image, diagnostics)."""
    img, rot = auto_orient(gray)
    img, skew = deskew(img)
    return strip_lines(img), {"rotated_cw": rot, "skew_deg": round(skew, 2)}
