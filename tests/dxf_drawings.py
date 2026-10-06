"""Synthetic KOMPAS-like DXF drawings for the tests, built with ezdxf (own geometry, no real drawing).

KOMPAS traits that the reader relies on: the linetypes K5LT_BASIC (contour), K5LT_THIN, K5LT_AXLED
(centre line), DIMENSION entities with their defpoints, the dimension text in the dimension's block with
the KOMPAS private-use codes for Ø × ° and a Cyrillic М in threads.
"""
import ezdxf

DIA, TIMES, DEG = "", "", ""  # KOMPAS codes for Ø, ×, °


def new_doc():
    doc = ezdxf.new("R2007")
    for name in ("K5LT_BASIC", "K5LT_THIN"):
        doc.linetypes.add(name, pattern=[0.0], description=name)
    doc.linetypes.add("K5LT_AXLED", pattern=[20.0, 15.0, -2.5, 0.5, -2.5], description="axial")
    return doc


class Sheet:
    """Draws the upper half of a profile in part coordinates (x, r) and mirrors it about the axis at y0."""

    def __init__(self, doc, axis_y=0.0):
        self.doc, self.msp, self.y0 = doc, doc.modelspace(), axis_y

    def _line(self, a, b, linetype="K5LT_BASIC"):
        self.msp.add_line(a, b, dxfattribs={"linetype": linetype})

    def axis(self, x0, x1):
        self._line((x0, self.y0), (x1, self.y0), "K5LT_AXLED")

    def line(self, x0, r0, x1, r1):
        """A horizontal or slanted contour line, and its mirror image."""
        self._line((x0, self.y0 + r0), (x1, self.y0 + r1))
        self._line((x0, self.y0 - r0), (x1, self.y0 - r1))

    def vertical(self, x, r0, r1):
        """A face or a step: one line across the axis when it starts on it, else two mirrored pieces."""
        if r0 == 0:
            self._line((x, self.y0 - r1), (x, self.y0 + r1))
        else:
            self._line((x, self.y0 + r0), (x, self.y0 + r1))
            self._line((x, self.y0 - r1), (x, self.y0 - r0))

    def arc(self, cx, cr, radius, a0, a1):
        """An arc of the upper half (angles counter-clockwise, degrees), and its mirror image."""
        attribs = {"linetype": "K5LT_BASIC"}
        self.msp.add_arc((cx, self.y0 + cr), radius, a0, a1, dxfattribs=attribs)
        self.msp.add_arc((cx, self.y0 - cr), radius, 360 - a1, 360 - a0, dxfattribs=attribs)

    def length(self, x0, x1, text, r_from=0.0, offset=30.0):
        """A horizontal dimension between x0 and x1; defpoints at radius r_from, the line above the part."""
        self.msp.add_linear_dim(base=(x0, self.y0 + offset), p1=(x0, self.y0 + r_from), p2=(x1, self.y0 + r_from),
                                angle=0, text=text).render()

    def diameter(self, x, d, text, offset=-30.0):
        """A vertical dimension (KOMPAS writes a diameter as a linear dimension at 90°)."""
        self.msp.add_linear_dim(base=(x + offset, self.y0), p1=(x, self.y0 + d / 2), p2=(x, self.y0 - d / 2),
                                angle=90, text=text).render()

    def radius(self, cx, cr, radius, angle, text):
        self.msp.add_radius_dim(center=(cx, self.y0 + cr), radius=radius, angle=angle, text=text).render()


def draw_shaft(sheet):
    """Shaft, 50 long: Ø40 with a 1×45° chamfer on the left, groove Ø30 L3, thread M36×1.5 L27 with a 1.5×45°
    chamfer on the right; a blind bore Ø10 depth 15 from the left face."""
    s = sheet
    s.axis(-3, 53)
    s.vertical(0, 5, 19)  # left face, around the bore
    s.line(0, 19, 1, 20)  # chamfer 1×45°
    s.line(1, 20, 20, 20)
    s.vertical(20, 15, 20)
    s.line(20, 15, 23, 15)  # groove bottom
    s.vertical(23, 15, 18)
    s.line(23, 18, 48.5, 18)
    s.line(48.5, 18, 50, 16.5)  # chamfer 1.5×45°
    s.vertical(50, 0, 16.5)  # right face
    s.line(0, 5, 15, 5)  # bore
    s.vertical(15, 0, 5)  # bore bottom
    s.length(0, 50, "50", r_from=16.5)
    s.length(20, 23, "3", r_from=15, offset=25)
    s.length(23, 50, "27", r_from=16.5, offset=40)
    s.length(0, 15, "15", r_from=5, offset=-35)
    s.diameter(10, 40, f"{DIA}40h12")
    s.diameter(21.5, 30, f"{DIA}30,2")  # the text says 30.2, the geometry 30: flagged
    s.diameter(35, 36, f"М36{TIMES}1,5-6g")
    s.diameter(7, 10, f"{DIA}10H11", offset=-20)  # inner: not bound
    s.length(0, 1, f"1{TIMES}45{DEG}", r_from=19.5, offset=35)
    s.length(48.5, 50, f"1,5{TIMES}45{DEG}", r_from=17, offset=35)


def draw_pin(sheet):
    """Pin, 59 long: Ø24 L10, taper Ø24→Ø16 L10, Ø16 L8, Ø28 L12 with an R2 transition on the right, Ø18 L10,
    a spherical end R9."""
    s = sheet
    s.axis(-3, 62)
    s.vertical(0, 0, 12)
    s.line(0, 12, 10, 12)
    s.line(10, 12, 20, 8)  # taper
    s.line(20, 8, 28, 8)
    s.vertical(28, 8, 14)
    s.line(28, 14, 38, 14)
    s.arc(38, 12, 2, 0, 90)  # transition R2 from the Ø28 flat down to the step at x 40
    s.vertical(40, 9, 12)
    s.line(40, 9, 50, 9)
    s.arc(50, 0, 9, 0, 90)  # spherical end
    s.length(0, 59, "59", r_from=0)
    s.length(0, 10, "10", r_from=12, offset=20)
    s.length(10, 20, "10", r_from=8, offset=20)
    s.length(28, 40, "12", r_from=14, offset=20)
    s.diameter(5, 24, f"{DIA}24")
    s.diameter(20, 16, f"{DIA}16")  # on the boundary taper / cylinder: the cylinder
    s.diameter(33, 28, f"{DIA}28")
    s.diameter(45, 18, f"{DIA}18")
    s.radius(38, 12, 2, 45, "R2")
    s.radius(50, 0, 9, 45, "R9")


def write(path, *drawers, spacing=100.0):
    """One sheet with each part on its own axis, spaced in y; x ranges overlap."""
    doc = new_doc()
    for i, draw in enumerate(drawers):
        draw(Sheet(doc, axis_y=i * spacing))
    doc.saveas(path)
    return path
