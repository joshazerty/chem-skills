#!/usr/bin/env python3
"""Give a ChemDraw PDF a tight MediaBox.

    uv run --with pymupdf python crop_pdf.py <file.pdf> [-o out.pdf]

ChemDraw exports PDF at the print page size regardless of the CDXML page
BoundingBox, so a figure headed for a journal has to be re-boxed. Setting only
/CropBox is not enough -- some renderers honour just /MediaBox -- so each page
is rebuilt at exactly the size of its own artwork.

Without -o the input is rewritten IN PLACE, which is what the skill's build
step wants; -o keeps the original.
"""
import sys, os, pymupdf


def content_box(page):
    """Bounding box of the real artwork, or None if the page is blank.

    The full-page white background rectangle has to be filtered out or the
    "tight" box comes back as the whole page again.
    """
    P = page.rect
    rects = []
    for d in page.get_drawings():
        r = d["rect"]
        if r.width > 0.95 * P.width and r.height > 0.95 * P.height:
            continue
        if d.get("fill") == (1.0, 1.0, 1.0) and d.get("color") is None:
            continue
        if r.width <= 0 or r.height <= 0:
            continue
        rects.append(r)
    for b in page.get_text("blocks"):
        r = pymupdf.Rect(b[:4])
        if r.width < 0.95 * P.width:
            rects.append(r)
    if not rects:
        return None
    bb = rects[0]
    for r in rects[1:]:
        bb = bb | r
    return bb


def crop(src, dst=None, pad=7):
    doc = pymupdf.open(src)
    out = pymupdf.open()
    kept = 0
    for i, page in enumerate(doc):
        bb = content_box(page)
        if bb is None:                       # blank page: copy it unchanged
            out.insert_pdf(doc, from_page=i, to_page=i)
            continue
        bb = pymupdf.Rect(bb.x0 - pad, bb.y0 - pad,
                          bb.x1 + pad, bb.y1 + pad) & page.rect
        np = out.new_page(width=bb.width, height=bb.height)
        np.show_pdf_page(np.rect, doc, i, clip=bb)
        kept += 1
    if not kept:
        doc.close()
        out.close()
        raise SystemExit(f"{src}: no artwork found on any page; left unchanged")
    tmp = (dst or src) + ".tmp"
    out.save(tmp, garbage=4, deflate=True)
    out.close()
    doc.close()
    os.replace(tmp, dst or src)
    r = pymupdf.open(dst or src)[0].rect
    print(f"final PDF: {r.width:.1f} x {r.height:.1f} pt "
          f"= {r.width / 72:.2f} x {r.height / 72:.2f} in")
    return dst or src


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "-o"]
    if not args or "-h" in sys.argv or "--help" in sys.argv:
        raise SystemExit(__doc__)
    dst = args[1] if "-o" in sys.argv and len(args) > 1 else None
    if not os.path.exists(args[0]):
        raise SystemExit(f"no such file: {args[0]}")
    crop(args[0], dst)
