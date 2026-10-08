"""tools/dxf_blind_check.py on a synthetic sheet (tests/dxf_drawings.py): the comparison, the format check, the
freeze check. Never on real files."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

import dxf_drawings as dd

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("dxf_blind_check", ROOT / "tools" / "dxf_blind_check.py")
blind = importlib.util.module_from_spec(spec)
sys.modules["dxf_blind_check"] = blind  # dataclasses look the module up while it loads
spec.loader.exec_module(blind)

# draw_pin on the axis y 100 is the top part, draw_shaft on y 0 the bottom one
ETALON = """# Синтетичний аркуш

## Деталь 1 — палець

### Зовнішні ділянки (зліва направо, як на кресленні)
| № | тип | Ø або R | довжина | фаска зліва | фаска справа | R переходу справа | примітка |
|---|-----|---------|---------|-------------|--------------|-------------------|----------|
| 1 | od_turn | Ø24 | 10 | | | | |
| 2 | taper | Ø24→Ø16 | 10 | | | | |
| 3 | od_turn | Ø16 | 8 | | | | |
| 4 | od_turn | Ø28 | 12 | | | R2 | |
| 5 | od_turn | Ø18 | 10 | | | | |
| 6 | arc | R9 | 9 | | | | сфера |

### Розміри
| текст | до чого прив'язаний |
|-------|---------------------|
| 59 | межі 0–6 |
| 10 | межі 0–1 |
| 10 | межі 1–2 |
| 12 | межі 3–4 |
| Ø24 | ділянка 1 |
| Ø16 | ділянка 3 |
| Ø28 | ділянка 4 |
| Ø18 | ділянка 5 |
| R2 | R на межі 4 |
| R9 | ділянка 6 |

## Деталь 2 — вал

### Зовнішні ділянки (зліва направо, як на кресленні)
| № | тип | Ø або R | довжина | фаска зліва | фаска справа | R переходу справа | примітка |
|---|-----|---------|---------|-------------|--------------|-------------------|----------|
| 1 | od_turn | Ø40h12 | 20 | 1×45° | | | |
| 2 | groove | Ø30,2 | 3 | | | | |
| 3 | thread | M36×1,5-6g | 27 | | 1,5×45° | | |

### Внутрішні ділянки (поза критерієм)
| № | тип | Ø | довжина | примітка |
|---|-----|---|---------|----------|
| в1 | bore | Ø10H11 | 15 | |

### Розміри
| текст | до чого прив'язаний |
|-------|---------------------|
| 50 | межі 0–3 |
| 3 | межі 1–2 |
| 27 | межі 2–3 |
| 15 | внутрішній |
| Ø40h12 | ділянка 1 |
| Ø30,2 | ділянка 2, на кресленні не збігається |
| M36x1.5-6g | ділянка 3 |
| Ø10H11 | внутрішній |
| 1×45° | фаска на межі 0 |
| 1,5×45° | фаска на межі 3 |
"""


@pytest.fixture
def sheet(tmp_path):
    dd.write(tmp_path / "sheet.dxf", dd.draw_shaft, dd.draw_pin)
    (tmp_path / "sheet.etalon.md").write_text(ETALON, encoding="utf-8")
    return tmp_path


def test_all_outer_dimensions_right_inner_apart(sheet):
    result = blind.compare_sheet(sheet / "sheet.dxf", sheet / "sheet.etalon.md")
    assert result.broken is None and len(result.outer) == 18
    assert all(r.right for r in result.outer), [r for r in result.outer if not r.right]
    assert [r.text for r in result.rows if r.kind == "inner"] == ["15", "Ø10H11"]
    assert all(same for *_, same in result.sections)
    groove = next(r for r in result.rows if r.text == "Ø30,2")
    assert groove.flag_expected and groove.flags == ["geometry 30 ≠ text 30.2"]
    assert not result.extra


def test_a_wrong_expected_row_counts_as_a_miss(sheet):
    (sheet / "sheet.etalon.md").write_text(ETALON.replace("| 12 | межі 3–4 |", "| 12 | межі 2–4 |"), encoding="utf-8")
    result = blind.compare_sheet(sheet / "sheet.dxf", sheet / "sheet.etalon.md")
    wrong = [r for r in result.outer if not r.right]
    assert [(r.text, r.expected) for r in wrong] == [("12", "межі 2–4")]


def test_out_of_criterion_rows_are_apart(sheet):
    text = ETALON.replace("| R9 | ділянка 6 |", "| R9 | поза критерієм |")
    (sheet / "sheet.etalon.md").write_text(text, encoding="utf-8")
    result = blind.compare_sheet(sheet / "sheet.dxf", sheet / "sheet.etalon.md")
    assert len(result.outer) == 17 and [r.text for r in result.rows if r.kind == "out"] == ["R9"]


def test_a_broken_sheet_counts_all_its_outer_dimensions_wrong(sheet):
    def no_axis(s):
        s.vertical(0, 0, 10)
        s.line(0, 10, 30, 10)
        s.vertical(30, 0, 10)
    dd.write(sheet / "sheet.dxf", no_axis)
    result = blind.compare_sheet(sheet / "sheet.dxf", sheet / "sheet.etalon.md")
    assert result.broken.startswith("basic step broke") and len(result.outer) == 18
    assert not any(r.right for r in result.outer)


def test_report_and_verdict(sheet):
    text = blind.report([blind.compare_sheet(sheet / "sheet.dxf", sheet / "sheet.etalon.md")],
                        blind.freeze_manifest())
    assert "Outer-profile dimensions bound right: 18 of 18 (100.0%)" in text and "DXF CONFIRMED" in text
    assert "turnpilot/dxf_reader.py" in text


def test_check_etalon_finds_format_problems(sheet):
    assert blind.check_etalon(sheet / "sheet.etalon.md") == []
    bad = ETALON.replace("| Ø16 | ділянка 3 |", "| Ø16 | ділянка 9 |").replace("| 2 | taper |", "| 2 | cone |")
    (sheet / "bad.etalon.md").write_text(bad, encoding="utf-8")
    problems = blind.check_etalon(sheet / "bad.etalon.md")
    assert any("unknown type 'cone'" in p for p in problems)
    assert any("'Ø16' → 'ділянка 9'" in p for p in problems)


def test_check_etalon_mode_does_not_open_dxf(sheet, capsys):
    (sheet / "sheet.dxf").write_text("not a drawing", encoding="utf-8")  # would fail if it were read
    assert blind.main([str(sheet), "--check-etalon"]) == 0
    assert "format OK" in capsys.readouterr().out


def test_freeze_check(tmp_path):
    manifest = blind.freeze_manifest()
    path = tmp_path / "freeze.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert blind.check_freeze(path) == []
    manifest["files"]["turnpilot/dxf_reader.py"] = "0" * 32
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert any("turnpilot/dxf_reader.py" in p for p in blind.check_freeze(path))
    assert blind.check_freeze(tmp_path / "missing.json") == ["missing.json not found: the code is not frozen"]


def test_md5_ignores_line_ends(tmp_path):
    (tmp_path / "lf").write_bytes(b"a\nb\n")
    (tmp_path / "crlf").write_bytes(b"a\r\nb\r\n")
    assert blind.file_md5(tmp_path / "lf") == blind.file_md5(tmp_path / "crlf")


def test_a_dxf_without_an_expected_answer_stops_the_run(tmp_path):
    dd.write(tmp_path / "alone.dxf", dd.draw_shaft)
    with pytest.raises(SystemExit, match="No expected answer for: alone.dxf"):
        blind.sheets(tmp_path)
