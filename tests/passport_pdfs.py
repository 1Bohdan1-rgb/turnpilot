"""Synthetic machine passports for the tests, built with pymupdf (no real passport is used)."""
import pymupdf

SPEC_PAGE = """TECHNICAL DATA
Max turning diameter over the carriage, mm ............ 300
Max turning length, mm ................................ 500
Spindle bore (bar capacity), mm ....................... 52
Spindle speed range, rpm ............................... 30 - 4000
Main motor power S1 / S6, kW ........................... 11 / 15
Turret, number of positions ............................ 12
Max feed Z, mm/min ..................................... 5000
Coolant pump pressure, bar ............................. 2
CNC control ............................................ Fanuc 0i-TF"""

PAGES = [
    "LATHE TL-1\nPASSPORT\nManufacturer's documentation",
    "1. General\nThe machine is intended for turning shafts and bushings in small batches.",
    SPEC_PAGE,
    "4. Safety\nDo not open the guard while the spindle turns.",
    None,  # a scanned page: an image, no text
]


def make_passport(path, pages=PAGES):
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        if text is None:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
            pix.clear_with(200)
            page.insert_image(pymupdf.Rect(72, 72, 300, 300), pixmap=pix)
        else:
            page.insert_text((50, 72), text, fontsize=9)
    doc.save(path)
    return path
