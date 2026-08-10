"""
split_magmeter_templates.py — one-shot template builder for the Signet 2551
magmeter submittal pages.

Input (the two files Casey supplied, as exported from the released job):
    SIGNET_2551-PX-11_MAGMETER_FIELD_MOUNT_SUBMITTAL.pdf   (11 pages)
    SIGNET_2551-PX-12_MAGMETER_FOR_VFD_SUBMITTAL.pdf       ( 7 pages)

Both files arrived with their red boxes and yellow callouts stored as LIVE PDF
MARKUP ANNOTATIONS (Square / FreeText / Line, each with a Popup and an author
name: smcnulty, michael.lynch, Michael Lynch) rather than baked into the page
content the way every other template in templates/ is. Three problems with
shipping them as-is:

  1. the author names ride along into a customer-facing package,
  2. a customer can click and delete the boxes,
  3. the boxes encode ONE job's configuration (5-8 in. body on the dimensions
     page, PP clamp-on 10/12 in. K-factors, and — in the field-mount file —
     a ½-4 in. ordering row that contradicts its own dimensions box).

So this script strips EVERY annotation from every page and splits the files
into single-purpose templates. The orchestrator then draws the boxes fresh,
driven by the size on the quote line (step 4h). annotation_engine's
strip_baked pass only removes red Square + yellow FreeText, so the red /Line
strike-through, the Popups and the /Link annots would otherwise survive —
they are removed here instead.

Pages 6 and 7 came from a distributor's reproduction of the GF manual and
carry "CALL 1-800-577-8111 FOR SALES AND SUPPORT" / "CLICK HERE TO RETURN TO
WEBSITE" banners top and bottom, with live links. The links go with the other
annotations; the baked banner text is covered with a white rectangle.

Run once, from the repo root:

    python3 split_magmeter_templates.py <path-to-PX-11.pdf> <path-to-PX-12.pdf>

Outputs into templates/ (shared pages are written once, from PX-11):

    magmeter_intro.pdf              PX-11 p1-2   verbatim
    magmeter_dimensions.pdf         PX-11 p3     annotated (pipe-range row)
    magmeter_ordering_field_mount.pdf  PX-11 p4  annotated (model row)
    magmeter_ordering_vfd.pdf       PX-12 p4     annotated (model row)
    magmeter_saddles.pdf            PX-11 p5     annotated (PVC saddle row)
    magmeter_fittings.pdf           PX-11 p6     annotated (callout only)
    magmeter_kfactors.pdf           PX-11 p7     annotated (callout only)
    magmeter_9900_transmitter.pdf   PX-11 p8     annotated (field-mount box)
    magmeter_9900_specs.pdf         PX-11 p9-11  verbatim
"""
from __future__ import annotations

import sys
from pathlib import Path

import fitz

TEMPLATE_DIR = Path("templates")

# Distributor banners baked into the content of pages 6 and 7 (top and bottom).
# Removed by searching for the exact phrases and redacting those rectangles —
# a blanket white band would clip the section headings, which sit only 2 points
# below the second banner line.
BANNER_PHRASES = [
    "CALL 1-800-577-8111 FOR SALES AND SUPPORT",
    "CLICK HERE TO RETURN TO WEBSITE",
]


def strip_all_annots(page) -> int:
    """Remove every annotation on the page (markup, popups and links).

    Returns the number removed. Deleting while iterating invalidates the
    generator, so the list is materialised first.
    """
    n = 0
    annots = list(page.annots() or [])
    for a in annots:
        page.delete_annot(a)
        n += 1
    for link in list(page.get_links()):
        if link.get("xref"):
            page.delete_link(link)
    return n


def cover_banners(page) -> int:
    """Redact the third-party sales banners on the manual pages.

    Returns the number of banner instances removed. Images are left untouched
    (PDF_REDACT_IMAGE_NONE) so the fitting illustrations survive.
    """
    hits = 0
    for phrase in BANNER_PHRASES:
        for rect in page.search_for(phrase):
            page.add_redact_annot(rect, fill=(1, 1, 1))
            hits += 1
    if hits:
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
    return hits


def extract(src: fitz.Document, first: int, last: int, out_name: str,
            banners: bool = False) -> Path:
    """Write pages [first, last] (0-indexed, inclusive) to templates/out_name."""
    doc = fitz.open()
    doc.insert_pdf(src, from_page=first, to_page=last)
    removed = 0
    banners_gone = 0
    for page in doc:
        removed += strip_all_annots(page)
        if banners:
            banners_gone += cover_banners(page)
    doc.set_metadata({})          # drop producer/author metadata too
    out_path = TEMPLATE_DIR / out_name
    doc.save(out_path, garbage=4, deflate=True)
    doc.close()
    extra = f", {banners_gone} banner(s) redacted" if banners else ""
    print(f"  {out_name:34} pages {first + 1}-{last + 1}  "
          f"({removed} annotation(s) stripped{extra})")
    return out_path


def main(px11_path: str, px12_path: str):
    TEMPLATE_DIR.mkdir(exist_ok=True)
    px11 = fitz.open(px11_path)
    px12 = fitz.open(px12_path)

    if len(px11) != 11 or len(px12) != 7:
        print(f"WARNING: expected 11/7 pages, got {len(px11)}/{len(px12)}. "
              f"Page indices below assume the original layout — check the output.")

    print("From PX-11 (field mount):")
    extract(px11, 0, 1, "magmeter_intro.pdf")
    extract(px11, 2, 2, "magmeter_dimensions.pdf")
    extract(px11, 3, 3, "magmeter_ordering_field_mount.pdf")
    extract(px11, 4, 4, "magmeter_saddles.pdf")
    extract(px11, 5, 5, "magmeter_fittings.pdf", banners=True)
    extract(px11, 6, 6, "magmeter_kfactors.pdf", banners=True)
    extract(px11, 7, 7, "magmeter_9900_transmitter.pdf")
    extract(px11, 8, 10, "magmeter_9900_specs.pdf")

    print("From PX-12 (for VFD):")
    # p1-3 and p5-7 of PX-12 are byte-identical in layout to PX-11's, so only
    # the 4-20 mA ordering page is unique to this file.
    extract(px12, 3, 3, "magmeter_ordering_vfd.pdf")

    px11.close()
    px12.close()
    print("\nDone. Commit the new templates/magmeter_*.pdf files.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
