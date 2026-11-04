"""Script-independent phonetic skeletons so Arabic transliterations match Latin names.

'ليبيتور' and 'LIPITOR' both become 'lbtr'. Vowels are dropped and similar-sounding
consonants collapsed (p~b, v~f, c/q/k, g/j, s/z/sh/x, ...).
"""
from __future__ import annotations

import re

_AR = {
    "ب": "b", "ت": "t", "ث": "t", "ج": "g", "ح": "h", "خ": "h", "د": "d", "ذ": "d",
    "ر": "r", "ز": "s", "س": "s", "ش": "s", "ص": "s", "ض": "d", "ط": "t", "ظ": "d",
    "غ": "g", "ف": "f", "ق": "k", "ك": "k", "ل": "l", "م": "m", "ن": "n", "ه": "h",
    "ة": "", "ا": "", "أ": "", "إ": "", "آ": "", "ى": "", "ي": "", "و": "", "ع": "", "ء": "",
    "ئ": "", "ؤ": "",
}
_LAT_MAP = {"p": "b", "v": "f", "c": "k", "q": "k", "j": "g", "z": "s", "x": "ks", "w": ""}
_DIAC = re.compile(r"[ً-ْـٰ]")
_TOKEN = re.compile(r"[A-Za-z]+|[؀-ۿ]+|\d+(?:\.\d+)?")


def _lat(word: str) -> str:
    w = word.lower().replace("ph", "f").replace("sh", "s").replace("ch", "k").replace("th", "t")
    w = re.sub(r"c(?=[ei])", "s", w)
    out = []
    for ch in w:
        if ch in "aeiouy":
            continue
        out.append(_LAT_MAP.get(ch, ch))
    return _dedupe("".join(out))


def _ar(word: str) -> str:
    return _dedupe("".join(_AR.get(ch, "") for ch in _DIAC.sub("", word)))


def _dedupe(s: str) -> str:
    return re.sub(r"(.)\1+", r"\1", s)


def tokens(text: str):
    """Split into (kind, raw) with kind in {'w' word, 'n' number}."""
    out = []
    for m in _TOKEN.finditer(text or ""):
        t = m.group(0)
        out.append(("n", t) if t[0].isdigit() else ("w", t))
    return out


# unit / dosage-form words that carry no identity (skeleton space)
STOP = {"mg", "mgm", "mg", "mls", "ml", "gm", "grm", "krs", "tb", "tbs", "tbls", "tbl", "tblt", "tblts",
        "kbsl", "kb", "kbs", "kbsls", "sk", "sbsl", "amb", "amps", "fl", "bks", "bk", "bg", "bgs",
        "ks", "sh", "ml", "mmg"}


def skeleton_words(text: str, drop_stop: bool = True):
    """List of phonetic skeletons for the alphabetic words of ``text`` (len>=2)."""
    res = []
    for kind, t in tokens(text):
        if kind != "w":
            continue
        sk = _ar(t) if "؀" <= t[0] <= "ۿ" else _lat(t)
        if len(sk) >= 2 and not (drop_stop and sk in STOP):
            res.append(sk)
    return res


def numbers(text: str):
    """Numeric tokens normalised ('2000', '14', '0.1'); Arabic-Indic digits converted."""
    text = (text or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    return {m.group(0).lstrip("0") or "0" for m in re.finditer(r"\d+(?:\.\d+)?", text)}
