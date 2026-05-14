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


def parse_quote(pdf_path: str) -> Quote:
    quote = Quote()
    current_section: Optional[str] = None

    with pdfplumber.open(pdf_path) as pdf:
        # Pull header metadata from page 1-2
        header_text = "\n".join(
            p.extract_text() or "" for p in pdf.pages[:3]
        )
        m = re.search(r"Quote Number:\s*(\d+)", header_text)
        if m:
            quote.quote_number = m.group(1)
        m = re.search(r"Account ID:\s*(\d+)", header_text)
        if m:
            quote.account_id = m.group(1)
        m = re.search(r"Project Name:\s*([^\n]+?)(?:\s*SOLD)?$", header_text, re.M)
        if m:
            # strip "-AS SOLD" suffix
            quote.project_name = re.sub(r"-AS\s*$", "", m.group(1)).strip()
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

        # Now walk every page collecting line items.
        # Section headers ("Items", "Lap Pool", "Training pool",
        # "Pool A Grating ...") appear as their own line in the text stream
        # immediately before the table.
        section_keywords = {
            "Items",
            "Lap Pool",
            "Training pool",
            "Pool A Grating",
        }

        for page in pdf.pages:
            text = page.extract_text() or ""
            lines = text.split("\n")

            i = 0
            while i < len(lines):
                line = lines[i].strip()

                # Section header detection
                for kw in section_keywords:
                    if line.startswith(kw):
                        current_section = (
                            "Pool A Grating" if kw == "Pool A Grating" else kw
                        )
                        break

                # Line-item detection
                # Format: "<item#> <part_no> <qty> <unit> $ price ..."
                # Quantity can be 1-4 digits; unit on same line OR wrapped to next.
                # Example: "2 1000-8906 1 EA $ 95,667.00 17% $79,403.61 $79,403.61"
                # Example: "6 1000-5852 11 $ 36.38 ..." (with EA on next line)
                m = re.match(
                    r"^\d+\s+(\d{3,4}-\d{4})\s+(\d+)(?:\s+(?:EA|FOT))?\s*\$\s*([\d,]+\.\d{2})",
                    line,
                )
                if m and current_section:
                    part_no = m.group(1)
                    qty = int(m.group(2))
                    unit_price = float(m.group(3).replace(",", ""))

                    # Description is the next non-empty line that isn't just
                    # a unit indicator ("EA" or "FOT")
                    description = ""
                    for j in range(i + 1, min(i + 4, len(lines))):
                        candidate = lines[j].strip()
                        if not candidate:
                            continue
                        if candidate in {"EA", "FOT"}:
                            continue
                        if candidate.startswith("Reference"):
                            continue
                        description = candidate
                        break

                    # Reference number (within next 3 lines)
                    reference = ""
                    for j in range(i + 1, min(i + 4, len(lines))):
                        ref_match = re.search(
                            r"Reference\s*#:\s*(\S+)",
                            lines[j],
                        )
                        if ref_match:
                            reference = ref_match.group(1)
                            break

                    quote.line_items.append(LineItem(
                        section=current_section,
                        part_number=part_no,
                        description=description,
                        reference=reference,
                        quantity=qty,
                        unit_price=unit_price,
                    ))
                i += 1

    return quote


if __name__ == "__main__":
    q = parse_quote("/mnt/user-data/uploads/Ontario_Aquatic_Center-AS_SOLD__1_.pdf")
    print(json.dumps(asdict(q), indent=2))
