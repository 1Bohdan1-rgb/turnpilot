"""One style for every page (the shop plan's palette), the theme chosen in the top bar."""
import re
from pathlib import Path

CSS = (Path(__file__).parent.parent / "turnpilot" / "static" / "style.css").read_text(encoding="utf-8")


def test_the_theme_button_is_in_the_top_bar_of_every_page(client):
    for url in ("/jobs", "/shop", "/machines", "/cutting-data"):
        page = client.get(url).get_data(as_text=True)
        assert page.count('id="theme-toggle"') == 1, url
        assert 'localStorage.getItem("tp-theme")' in page and "document.documentElement.dataset.theme" in page
    assert 'id="sf-theme"' not in client.get("/shop").get_data(as_text=True)  # the shop's own button is gone


def test_dark_theme_for_the_system_and_for_the_choice():
    assert '@media (prefers-color-scheme: dark) { :root:not([data-theme="light"])' in CSS
    assert ':root[data-theme="dark"]' in CSS
    # the shop page has no palette of its own any more: the root's
    assert not re.search(r"\.shop-floor[^{]*\{[^}]*--sf-paper", CSS)


def test_colours_come_from_the_palette():
    """Outside the palette blocks no colour is written by its value (the drawing's image keeps a white sheet)."""
    body = CSS.split("color-scheme:dark;\n}", 1)[1]
    found = [line for line in body.splitlines() if re.search(r"#[0-9a-fA-F]{3,6}\b", line)
             and "--p3d" not in line and ".drawing-preview img" not in line and "color:#fff" not in line]
    assert found == []


def test_the_machine_page_has_no_wide_tables(client):
    """The tool library in five columns (the sources behind ⓘ), the turret in five, inputs that take the cell's width."""
    page = client.get("/machines/1").get_data(as_text=True)
    library = page.split('<table class="tool-library">', 1)[1].split("</thead>", 1)[0]
    assert len(re.findall(r"<th[ >]", library)) == 5
    turret = page.split('<table class="machine-turret">', 1)[1].split("</thead>", 1)[0]
    assert len(re.findall(r"<th[ >]", turret)) == 5
    assert 'popovertarget="tool-src-' in page and 'size="40"' not in page
    assert 'class="fill"' in page and "input.fill, select.fill { width: 100%; min-width: 0; }" in CSS
