import sys, os, pymupdf
src = sys.argv[1]
doc = pymupdf.open(src); page = doc[0]; P = page.rect
rects = []
for d in page.get_drawings():
    r = d["rect"]
    if r.width > 0.95 * P.width and r.height > 0.95 * P.height: continue
    if d.get("fill") == (1.0, 1.0, 1.0) and d.get("color") is None: continue
    if r.width <= 0 or r.height <= 0: continue
    rects.append(r)
for b in page.get_text("blocks"):
    r = pymupdf.Rect(b[:4])
    if r.width < 0.95 * P.width: rects.append(r)
bb = rects[0]
for r in rects[1:]: bb = bb | r
pad = 7
bb = pymupdf.Rect(bb.x0-pad, bb.y0-pad, bb.x1+pad, bb.y1+pad) & P

# Re-place the clipped region on a page of exactly that size, so MediaBox
# itself is tight -- CropBox alone is ignored by some renderers/printers.
out = pymupdf.open()
np = out.new_page(width=bb.width, height=bb.height)
np.show_pdf_page(np.rect, doc, 0, clip=bb)
out.save(src + ".tmp", garbage=4, deflate=True)
out.close(); doc.close()
os.replace(src + ".tmp", src)
r = pymupdf.open(src)[0].rect
print(f"final PDF: {r.width:.1f} x {r.height:.1f} pt = {r.width/72:.2f} x {r.height/72:.2f} in")
