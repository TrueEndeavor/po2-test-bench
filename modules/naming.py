import re
from modules.config import DOC_TYPE_MAP


def tc_sort_key(filename):
    m = re.search(r"TC(\d+)", filename)
    return int(m.group(1)) if m else 999


def guess_doc_type(filename):
    parts = filename.replace(".pdf", "").split("_")
    for part in parts:
        if part in DOC_TYPE_MAP:
            return DOC_TYPE_MAP[part]
    return "Marketing Material"


def short_name(filename):
    """Extract TC number and short description from filename."""
    name = filename.replace(".pdf", "")
    tc_match = re.search(r"(TC\d+)", name)
    tc = tc_match.group(1) if tc_match else "TC?"
    tc_num = re.search(r"\d+", tc)
    if tc_num:
        tc = f"TC{int(tc_num.group()):02d}"

    variant = ""
    v_match = re.search(r"[-_ ](1[A-C])\b", name)
    if v_match:
        variant = f" ({v_match.group(1)})"
    if "Copy of" in name:
        variant = " (alt)"

    after_tc = re.split(r"TC\d+[_]?", name, maxsplit=1)
    tail = after_tc[1].strip("_ ") if len(after_tc) > 1 else name

    tail = re.sub(r"^(FS|PP|CM|BR|RMCM)[_ ]+", "", tail)
    tail = re.sub(r"^2[_ ]*(?:Updated)?[_ ]*", "", tail)
    tail = re.sub(r"(?:TEST SAMPLE|Test Sample)", "", tail)
    tail = re.sub(r"(?:Updated)", "", tail)
    tail = re.sub(r"Copy of 2", "", tail)
    tail = re.sub(r"[_ ]*\d{0,2}[A-Z]{0,3}\d{4}[_ ]*", " ", tail)
    tail = re.sub(r"[-_ ]*(1[A-C])\b", "", tail)
    tail = tail.replace("_", " ")
    tail = re.sub(r"\s+", " ", tail).strip(" -_")

    if not tail:
        tail = guess_doc_type(filename)

    if len(tail) > 25:
        tail = tail[:25].rsplit(" ", 1)[0] + "..."

    return tc, tail + variant
