"""The simulation's messages on the program page: one row per reason with the range of its lines and values."""
from turnpilot.gcode.report import group


def test_one_reason_is_one_row_with_its_lines_and_values():
    rows = group([(112, "n·P 7760 mm/min: no threading feed limit"), (116, "n·P 7760 mm/min: no threading feed limit"),
                  (120, "n·P 7760 mm/min: no threading feed limit"),
                  (300, "cuts below the finished profile at Z-35.2 (D36.1 < D38.5)"),
                  (340, "cuts below the finished profile at Z-44.9 (D38.4 < D38.5)"),
                  (310, "cuts below the finished profile at Z-40 (D37 < D38.5)"), (0, "G92 through material")])
    assert rows == [
        {"line": 112, "last": 120, "count": 3, "message": "n·P 7760 mm/min: no threading feed limit"},
        {"line": 300, "last": 340, "count": 3,
         "message": "cuts below the finished profile at Z-44.9…-35.2 (D36.1…38.4 < D38.5)"},
        {"line": None, "last": None, "count": 1, "message": "G92 through material"}]


def test_different_reasons_stay_apart():
    rows = group([(5, "G96 while drilling on the axis"), (9, "G92 outside G97")])
    assert [r["message"] for r in rows] == ["G96 while drilling on the axis", "G92 outside G97"]


def test_a_number_with_a_trailing_point_is_one_number():
    rows = group([(189, "cuts below the finished profile at Z-44. (D38.5 < D38.605)"),
                  (189, "cuts below the finished profile at Z-42.6 (D38.5 < D38.621)")])
    assert rows == [{"line": 189, "last": 189, "count": 2,
                     "message": "cuts below the finished profile at Z-44.…-42.6 (D38.5 < D38.605…38.621)"}]
