"""
Parser for Evoqua/Neptune Benson quote PDFs.

Extracts:
- Project metadata (name, customer, quote number, date)
- Line items grouped by section (Items, Lap Pool, Training pool, etc.)

Output is the structured payload the rest of the workflow consumes.
"""
import re
import json
import pdfplumber
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

# Filter model embedded in a "FILTER DEFENDER SP-33-48-732" description.
_SP_MODEL_RE = re.compile(r'(SP-\d+-\d+-\d+(?:-[A-Z])?)', re.I)


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


def parse_quote(pdf_path: str) -> Quote:
    quote = Quote()
    current_section: Optional[str] = None

    with pdfplumber.open(pdf_path) as pdf:
        # Pull header metadata from page 1-2
        header_text = "\n".join(
            p.extract_text() or "" for p in pdf.pages[:3]
        )
        m = re.search(r"Quote Number\s*:\s*([\w-]+)", header_text)
        if m:
            quote.quote_number = m.group(1)
        m = re.search(r"Account ID:\s*(\d+)", header_text)
        if m:
            quote.account_id = m.group(1)
        # Project name may wrap across two lines and end with either
        # "-AS SOLD" (Ontario) or "- ASSOLD" (Fort Saskatchewan).
        m = re.search(
            r"Project Name\s*:\s*(.+?)\s*-?\s*(?:AS\s*SOLD|ASSOLD)",
            header_text,
            re.S,
        )
        if m:
            quote.project_name = (
                re.sub(r"\s+", " ", m.group(1)).strip().rstrip("-").strip()
            )
        # Customer: line right after "Proposal For:" header
        for i, line in enumerate(header_text.split("\n")):
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

        # Walk every line of every page as one flat stream. Two quote
        # layouts are supported:
        #   Ontario: header is one line "Item Part No Qty Unit Price ...";
        #            rows carry the unit price inline; each row has a
        #            "Reference #:" line.
        #   Fort Saskatchewan: header is three lines
        #            ("Part No" / "Item Qty Unit Price ..." / "Description");
        #            large prices wrap across lines; there is NO
        #            "Reference #:" line (the model lives in the description).
        #
        # Section detection is layout-agnostic: the section name is always
        # the line immediately following a "Currency: ..." line or the
        # "Item Pricing Summary" heading. Flattening the pages means a
        # section whose table begins on the next page is still attributed
        # correctly, and current_section persists across page breaks.
        all_lines = []
        for page in pdf.pages:
            all_lines.extend((page.extract_text() or "").split("\n"))

        prev_nonempty = ""
        i = 0
        while i < len(all_lines):
            line = all_lines[i].strip()
            if not line:
                i += 1
                continue

            # Section header detection
            if ("Currency" in prev_nonempty) or ("Item Pricing Summary" in prev_nonempty):
                if (
                    not line.startswith("Currency")
                    and "Unit Price" not in line
                    and not _ITEM_RE.match(line)
                ):
                    current_section = line

            # Line-item detection
            m = _ITEM_RE.match(line)
            if m and current_section:
                part_no = m.group(1)
                qty = int(m.group(2))
                unit_price = float(m.group(3).replace(",", "")) if m.group(3) else 0.0

                # Description: next non-empty line that isn't a unit
                # indicator, a "Reference" line, or a stray price fragment
                # left behind by a wrapped price cell.
                description = ""
                for j in range(i + 1, min(i + 6, len(all_lines))):
                    candidate = all_lines[j].strip()
                    if not candidate:
                        continue
                    if candidate in {"EA", "FOT"}:
                        continue
                    if candidate.startswith("Reference"):
                        continue
                    if _is_price_fragment(candidate):
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

                # Fort Saskatchewan filters carry no Reference# line — recover
                # the model (e.g. "SP-33-48-732") from the description so the
                # downstream family/sort/datasheet logic behaves identically
                # to the Ontario layout.
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

            prev_nonempty = line
            i += 1

    return quote


if __name__ == "__main__":
    q = parse_quote("/mnt/user-data/uploads/Ontario_Aquatic_Center-AS_SOLD__1_.pdf")
    print(json.dumps(asdict(q), indent=2))
