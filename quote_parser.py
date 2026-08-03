"""
Parser for Evoqua/Neptune Benson quote PDFs.

Extracts:
- Project metadata (name, customer, quote number, date)
- Line items grouped by section (Items, Lap Pool, Training pool, etc.)

Output is the structured payload the rest of the workflow consumes.
"""
import re
import io
import json
import pdfplumber
import fitz  # PyMuPDF — rasterize pages for the OCR fallback
import pytesseract
from PIL import Image
from dataclasses import dataclass, asdict, field
from typing import List, Optional


@dataclass
class LineItem:
    section: str           # "Lap Pool", "Training pool", "Items", etc.
    part_number: str       # e.g. "1000-8906"
    description: str       # e.g. "FILTER DEFENDER  SP-33-48-732"
    reference: str = ""    # e.g. "SP-33-48-732"
    quantity: int = 1
    unit_price: float = 0.0
    net_price: float = 0.0


@dataclass
class Quote:
    quote_number: str = ""
    account_id: str = ""
    project_name: str = ""
    customer: str = ""
    customer_address: str = ""
    quote_date: str = ""
    ocr_used: bool = False
    line_items: List[LineItem] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Accessory description parsing (reducers / precoat tees / strainers)
#
# The size shown in the yellow callout is parsed from the line-item
# description rather than stored per-part. Verified against real quote lines:
#   "REDUCER, CONC 6x4 FG"        -> CONC, FG, '6" X 4"'
#   "TEE PRECOAT 8X5X3 FG RED"    -> FG,       '8" x 5" x 3"'
#   "STRAINER H&L GUARDIAN 8 FG"  -> FG,       '8"'
# ---------------------------------------------------------------------------
_SIZE_3 = re.compile(r'(\d+(?:\s*1/2)?)\s*[xX]\s*(\d+(?:\s*1/2)?)\s*[xX]\s*(\d+(?:\s*1/2)?)')
_SIZE_2 = re.compile(r'(\d+(?:\s*1/2)?)\s*[xX]\s*(\d+(?:\s*1/2)?)')
_SIZE_1 = re.compile(r'GUARDIAN\s+(\d+)|(\d+)\s+(?:FG|SS|T316)', re.I)

# A line-item row: "<item#> <part-no> <qty> [EA|FOT] [$price] ..."
# The price is OPTIONAL: on some quote layouts a large unit price wraps onto
# the lines above/below the row (e.g. "$95,667.0" / row / "0"), so the row
# itself carries no "$price" token after the qty/unit. We only need the part
# number and qty here, so the price is captured when present and ignored
# otherwise.
_ITEM_RE = re.compile(
    r"^\d+\s+(\d{3,4}-\d{4})\s+(\d+)(?:\s+(?:EA|FOT))?(?:\s+\$\s*([\d,]+\.\d{2}))?"
)

# OCR-tolerant variant — used ONLY when a quote was recovered via OCR, where the
# leading item number, the qty, and the unit token are all unreliable. It keys
# off the 100x-xxxx part number and anchors at line start so a misread address
# ZIP (e.g. "46172-9538") can't be promoted to a line item. The strict text-path
# regex above is unchanged, so validated text layouts are byte-identical.
_ITEM_RE_OCR = re.compile(
    r"^\s*(?:\d+\s+)?(100\d-\d{4})(?:\s+(\d+))?(?:\s+(?:EA|FOT))?"
)

# Filter model embedded in a "FILTER DEFENDER SP-33-48-732" description.
_SP_MODEL_RE = re.compile(r'(SP-\d+-\d+-\d+(?:-[A-Z])?)', re.I)

# "Filter System - Defender" parent line. On single-section quotes (Ulster
# County layout) each occurrence of this part number marks the start of a new
# complete filter system inside the SAME quote section, and the system's name
# lives in the Alternative Description line that follows (e.g. "SWIMMING POOL
# (1) Existing SP49 Flowrate: 2000 GPM ..." / "SPLASH PAD Flowrate: 400 ...").
_SYSTEM_PARENT_PART = "1001-9810"
# Leading run of fully-UPPERCASE words in that alt-description = the system
# label. Word-by-word matching (each word >= 2 uppercase chars) stops cleanly
# before mixed-case tails ("Flowrate"), digits ("2000"), or '(' — so
# "SWIMMING POOL (1) Existing SP49 ..." -> "SWIMMING POOL" and
# "SPLASH PAD Flowrate: 400 GPM ..."    -> "SPLASH PAD".
_SYSTEM_LABEL_RE = re.compile(r'^((?:[A-Z][A-Z&/\-]+)(?:\s+[A-Z][A-Z&/\-]+)*)')


def _is_price_fragment(text: str) -> bool:
    """True for stray numeric/price fragments left by a wrapped price cell.

    e.g. '0', '00', '$95,667.0' — these must not be mistaken for a
    line-item description.
    """
    return bool(re.fullmatch(r'[\d.,$%]+', text or ""))


def accessory_material(description: str) -> str:
    """Return 'SS', 'FG', 'PVC', or '' from a line description."""
    u = (description or "").upper()
    if "T316" in u or re.search(r'\bSS\b', u):
        return "SS"
    if "FG" in u:
        return "FG"
    if "PVC" in u:
        return "PVC"
    return ""


def reducer_type(description: str) -> str:
    """Return 'CONC', 'ECC', or '' for reducer/tee lines."""
    u = (description or "").upper()
    if "CONC" in u:
        return "CONC"
    if "ECC" in u:
        return "ECC"
    return ""


def accessory_size(description: str):
    """Parse the AxBxC / AxB / single size token from a description.

    Returns the display string (e.g. '8" x 5" x 3"', '6" X 4"', '8"') or
    None if no size is present. The 3-dim form uses lowercase ' x ' to match
    the precoat-tee callout convention; the 2-dim form uses ' X ' to match
    the reducer convention.
    """
    d = description or ""
    m = _SIZE_3.search(d)
    if m:
        return " x ".join(f'{g.strip()}"' for g in m.groups())
    m = _SIZE_2.search(d)
    if m:
        return " X ".join(f'{g.strip()}"' for g in m.groups())
    m = _SIZE_1.search(d)
    if m:
        return f'{(m.group(1) or m.group(2))}"'
    return None


def _ocr_page(fitz_doc, page_index: int, dpi: int = 150) -> str:
    """Rasterize one page with PyMuPDF and OCR it with Tesseract.

    Pages are processed one at a time by the caller and the pixmap/image are
    released immediately so a scanned multi-page quote stays memory-bounded on
    the 2GB instance.
    """
    page = fitz_doc.load_page(page_index)
    pix = page.get_pixmap(dpi=dpi)
    try:
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        try:
            return pytesseract.image_to_string(img)
        finally:
            img.close()
    finally:
        pix = None  # drop the bitmap before the next page


def _page_texts(pdf_path: str):
    """Return (list-of-page-text, ocr_used).

    The text layer is tried first. If the document is effectively empty
    (a scanned / printed-to-image / flattened export), every page is OCR'd
    one at a time. A text quote never hits the OCR branch, so its output is
    identical to the pre-patch parser.
    """
    with pdfplumber.open(pdf_path) as pdf:
        raw = [(p.extract_text() or "") for p in pdf.pages]
    doc_chars = sum(len(re.sub(r"\s", "", t)) for t in raw)
    if doc_chars >= 40:  # has a real text layer
        return raw, False

    fitz_doc = fitz.open(pdf_path)
    try:
        return [_ocr_page(fitz_doc, i) for i in range(fitz_doc.page_count)], True
    finally:
        fitz_doc.close()


def _extract_project_name(header_text: str) -> str:
    """Three strategies, tried in order. Strategy 1 reproduces the original
    behavior exactly so Ontario / Fort Saskatchewan stay identical."""
    # 1) 'Project Name:' label anchored on an 'AS SOLD' / 'ASSOLD' marker
    #    (re.S lets the name span a wrapped continuation line).
    m = re.search(
        r"Project Name\s*:\s*(.+?)\s*-?\s*(?:AS\s*SOLD|ASSOLD)",
        header_text, re.S,
    )
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip().rstrip("-").strip()

    # 2) 'Project Name:' label with no AS SOLD marker (wrapped continuation).
    m = re.search(r"Project Name\s*:\s*(.+)", header_text)
    if m:
        name = re.sub(r"\s+", " ", m.group(1)).strip()
        name = re.split(r"\s{2,}|Account ID|Quote Number|Page \d", name)[0]
        return name.rstrip("-").strip()

    # 3) A Prepared-For block line ending in '- AS SOLD' / 'ASSOLD'
    #    (covers layouts with no 'Project Name:' field at all, e.g. Worthington).
    for line in header_text.split("\n"):
        if re.search(r"-\s*(?:AS\s*SOLD|ASSOLD)\s*$", line, re.I):
            return re.sub(
                r"\s*-?\s*(?:AS\s*SOLD|ASSOLD)\s*$", "", line, flags=re.I
            ).strip()
    return ""


# Repeated page furniture that OCR scatters into the line stream; never a
# legitimate line-item description.
_FURNITURE_RE = re.compile(
    r"^(Page \d+\b.*|Item Part No\b.*|Part No\b.*|Description\b.*|"
    r"Quote Number\b.*|Account ID\b.*|"
    r".*Net Price.*|.*Subtotal.*|Total\b.*|Currency\b.*)$", re.I,
)


def _is_furniture(text: str) -> bool:
    return bool(_FURNITURE_RE.match((text or "").strip()))


def _system_label_after(all_lines, start_idx, lookahead=8) -> str:
    """Extract the system label that follows a 1001-9810 parent row.

    Scans forward for the 'Alternative Description:' marker, then takes the
    leading uppercase-word run of the first non-empty, non-furniture line
    after it. Returns '' if no label is recoverable.
    """
    saw_alt = False
    for j in range(start_idx + 1, min(start_idx + 1 + lookahead, len(all_lines))):
        candidate = all_lines[j].strip()
        if not candidate:
            continue
        if not saw_alt:
            if "ALTERNATIVE DESCRIPTION" in candidate.upper():
                saw_alt = True
            continue
        if _is_furniture(candidate) or _is_price_fragment(candidate):
            continue
        m = _SYSTEM_LABEL_RE.match(candidate)
        return m.group(1).strip() if m else ""
    return ""


def _split_filter_systems(quote: Quote, markers) -> None:
    """Relabel sections when ONE quote section contains MULTIPLE
    'Filter System - Defender' (1001-9810) parent lines.

    Quotes like Ulster County place two complete filter systems inside a
    single 'Items' section, separated only by the 1001-9810 parent rows.
    Everything downstream (valve-kit pages, datasheet template resolution,
    filter-family gating, pool-labeled callouts) keys on LineItem.section, so
    without this split the second system's valve kit overwrites the first's,
    both datasheets collide on one output filename, and the package comes out
    as a single mangled system.

    markers: list of (line_items index, label) for each parent row, in quote
    order. Sections with fewer than two markers are left untouched, so named
    multi-pool layouts (Lap Pool / Training pool) and single-system quotes
    parse byte-identically to the pre-patch parser.
    """
    by_section = {}
    for idx, label in markers:
        by_section.setdefault(quote.line_items[idx].section, []).append((idx, label))

    for section, marks in by_section.items():
        if len(marks) < 2:
            continue
        bounds = [idx for idx, _ in marks] + [len(quote.line_items)]
        used = {}
        for k, (start, label) in enumerate(marks):
            name = (label or "").strip() or f"SYSTEM {k + 1}"
            used[name] = used.get(name, 0) + 1
            if used[name] > 1:
                name = f"{name} {used[name]}"  # two same-labeled systems
            for li in quote.line_items[start:bounds[k + 1]]:
                if li.section == section:  # don't touch interleaved real sections
                    li.section = name


def parse_quote(pdf_path: str) -> Quote:
    quote = Quote()
    current_section: Optional[str] = None
    system_markers = []  # (line_items index, label) per 1001-9810 parent row

    # Text layer first; OCR fallback only if the document is effectively empty.
    pages_text, ocr_used = _page_texts(pdf_path)
    quote.ocr_used = ocr_used
    item_re = _ITEM_RE_OCR if ocr_used else _ITEM_RE

    # Header metadata from the first up-to-3 pages.
    header_text = "\n".join(pages_text[:3])
    m = re.search(r"Quote Number\s*:\s*([\w-]+)", header_text)
    if m:
        quote.quote_number = m.group(1)
    m = re.search(r"Account ID:\s*(\d+)", header_text)
    if m:
        quote.account_id = m.group(1)

    # Project name — three strategies (label+marker, wrapped label, Prepared-For).
    quote.project_name = _extract_project_name(header_text)

    # Customer: line right after "Proposal For:" header
    for line in header_text.split("\n"):
        if "Proposal For:" in line:
            # Customer name may be on same line after the label or next non-empty line
            after = line.split("Proposal For:", 1)[1].strip()
            if after:
                # Strip trailing salesperson name (usually 2 capitalized words)
                after = re.sub(r"\s+[A-Z][a-z]+\s+[A-Z][a-z]+$", "", after)
                quote.customer = after.strip()
            break
    m = re.search(r"(\d{2}/\d{2}/\d{2})", header_text)
    if m:
        quote.quote_date = m.group(1)

    # Flat line stream across all pages; current_section persists across breaks.
    all_lines = []
    for t in pages_text:
        all_lines.extend(t.split("\n"))

    prev_nonempty = ""
    i = 0
    while i < len(all_lines):
        line = all_lines[i].strip()
        if not line:
            i += 1
            continue

        # Section header detection. The section name is the line after a
        # "Currency: ..." line or the "Item Pricing Summary" heading. Reject
        # subtotal/total/furniture rows (the multi-pool "Comp Pool" bug labeled
        # the section with its subtotal line) and strip any trailing price an
        # OCR merge may have appended to the title.
        if ("Currency" in prev_nonempty) or ("Item Pricing Summary" in prev_nonempty):
            if (
                not line.startswith("Currency")
                and "Unit Price" not in line
                and not item_re.match(line)
                and not _is_furniture(line)
                and not _is_price_fragment(line)
            ):
                current_section = (
                    re.sub(r"\s*\$?[\d,]+\.\d{2}\s*$", "", line).strip() or line
                )

        # Line-item detection
        m = item_re.match(line)
        if m and current_section:
            part_no = m.group(1)
            qty = (
                int(m.group(2))
                if (m.lastindex and m.lastindex >= 2 and m.group(2))
                else 1  # OCR drops the qty column often; default to 1
            )
            unit_price = (
                float(m.group(3).replace(",", ""))
                if (not ocr_used and m.lastindex and m.lastindex >= 3 and m.group(3))
                else 0.0  # price is not recovered on the OCR path
            )

            # Description: next non-empty line that isn't a unit indicator, a
            # "Reference" line, a stray wrapped-price fragment, or page furniture
            # (page numbers / repeated headers / totals) scattered by OCR.
            # Window is 10 lines (was 5): a row at a page break has the next
            # page's full header block (Quote Number / Account ID / Page N /
            # column headers) between the row and its wrapped description.
            description = ""
            for j in range(i + 1, min(i + 11, len(all_lines))):
                candidate = all_lines[j].strip()
                if not candidate:
                    continue
                if candidate in {"EA", "FOT"}:
                    continue
                if candidate.startswith("Reference"):
                    continue
                if _is_price_fragment(candidate):
                    continue
                if _is_furniture(candidate):
                    continue
                description = candidate
                break

            # Reference number (if a "Reference #:" line is present)
            reference = ""
            for j in range(i + 1, min(i + 6, len(all_lines))):
                ref_match = re.search(r"Reference\s*#:\s*(\S+)", all_lines[j])
                if ref_match:
                    reference = ref_match.group(1)
                    break

            # Filters with no Reference# line — recover the model (e.g.
            # "SP-33-48-732") from the description so downstream family/sort/
            # datasheet logic behaves identically to the Ontario layout.
            if not reference and "FILTER DEFENDER" in description.upper():
                sp = _SP_MODEL_RE.search(description)
                if sp:
                    reference = sp.group(1).upper()

            quote.line_items.append(LineItem(
                section=current_section,
                part_number=part_no,
                description=description,
                reference=reference,
                quantity=qty,
                unit_price=unit_price,
            ))

            # 1001-9810 parent row = start of a new filter system. Record the
            # marker (with its Alternative-Description label) so multi-system
            # single-section quotes can be split after the scan.
            if part_no == _SYSTEM_PARENT_PART:
                system_markers.append(
                    (len(quote.line_items) - 1, _system_label_after(all_lines, i))
                )

        prev_nonempty = line
        i += 1

    # Split any section holding multiple filter systems into per-system
    # sections (no-op for single-system and named multi-pool quotes).
    _split_filter_systems(quote, system_markers)

    # Fail loud rather than emit a blank submittal: if even OCR recovered no
    # line items, the caller gets a clear error instead of a cover-only PDF.
    if not quote.line_items:
        raise ValueError(
            f"No line items recovered from {pdf_path!r} (ocr_used={ocr_used}). "
            f"Refusing to emit a blank submittal — check the quote's text layer."
        )

    return quote


if __name__ == "__main__":
    q = parse_quote("/mnt/user-data/uploads/Ontario_Aquatic_Center-AS_SOLD__1_.pdf")
    print(json.dumps(asdict(q), indent=2))
