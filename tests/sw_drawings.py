r"""Synthetic SolidWorks-like DXF sheets for the tests (own geometry, no real drawing): what the adapter relies on.

SolidWorks traits: DXF 2000 (AC1015), the FORMAT layer and SW_* blocks, everything on layer 0 in Continuous lines told
apart by weight (frame 0.70 with the title block hanging on it at 0.50, contour 0.25, thin 0.18), CENTERX2 centre
lines, the sheet drawn at the drawing's scale, dimension texts with %%c / %%d and Cyrillic letters as \U+XXXX.
"""
import ezdxf

ESC_M, ESC_X = r"\U+041C", r"\U+0445"  # Cyrillic М and х as SolidWorks writes them


def sheet(scale=2.0):
    """A shaft Ø20 L30 (M20, a 1×45° chamfer on the left) + Ø30 L20 drawn at scale:1 on an A4 frame."""
    doc = ezdxf.new("R2000", setup=True)
    doc.layers.add("FORMAT")
    doc.blocks.new("SW_SFSYMBOL_0")
    doc.linetypes.add("CENTERX2", pattern=[2.0, 1.25, -0.25, 0.25, -0.25], description="center x2")
    msp = doc.modelspace()

    def line(a, b, lw, linetype="Continuous"):
        msp.add_line(a, b, dxfattribs={"lineweight": lw, "linetype": linetype})

    for a, b in (((5, 5), (205, 5)), ((205, 5), (205, 292)), ((205, 292), (5, 292)), ((5, 292), (5, 5))):
        line(a, b, 70)  # frame
    for a, b in (((105, 5), (105, 40)), ((105, 40), (205, 40)), ((150, 5), (150, 40))):
        line(a, b, 50)  # title block, hanging on the frame
    msp.add_mtext("SCALE: %d:1" % scale, dxfattribs={"insert": (152, 10)})
    msp.add_mtext("EN 10025-2 S355J2+N", dxfattribs={"insert": (107, 20)})
    x0, y0 = 40.0, 180.0  # the part's left end on the axis, on the sheet

    def P(x, r):
        return (x0 + x * scale, y0 + r * scale)

    upper = [(0, 9), (1, 10), (30, 10), (30, 15), (50, 15), (50, 0)]
    for sign in (1, -1):
        pts = [(0, 0)] + upper
        for (xa, ra), (xb, rb) in zip(pts, pts[1:]):
            line(P(xa, sign * ra), P(xb, sign * rb), 25)
    line(P(-2, 0), P(52, 0), 18, "CENTERX2")
    line(P(1, 8.5), P(30, 8.5), 18)  # the thread's root (thin)
    line(P(1, -8.5), P(30, -8.5), 18)
    dims = [  # (base, p1, p2, angle, text)
        (P(0, 25), P(0, 0), P(50, 0), 0, "<>"),
        (P(0, 20), P(0, 10), P(30, 10), 0, "<>"),
        (P(-8, 0), P(1, 10), P(1, -10), 90, ESC_M + "<>-6g"),
        (P(60, 0), P(50, 15), P(50, -15), 90, "%%c<>h6"),
        (P(-5, 14), P(0, 9), P(1, 9), 0, "<>" + ESC_X + "45%%d"),
    ]
    for base, p1, p2, angle, text in dims:
        msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=angle, text=text,
                           override={"dimlfac": 1 / scale}).render()
    return doc
