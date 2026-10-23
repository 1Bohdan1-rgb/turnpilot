"""The buttons pressed at the machine are big enough for a finger: class "touch" (min 44 × 44 px in style.css)."""
import re
from pathlib import Path

TEMPLATES = Path(__file__).parent.parent / "turnpilot" / "templates"
CSS = (Path(__file__).parent.parent / "turnpilot" / "static" / "style.css").read_text(encoding="utf-8")

BUTTONS = {  # template: the texts of its buttons that must be "touch"
    "_order_panel.html": ['_("Done")', '_("Save parts done")', '_("Running now on {machine}"'],
    "shop_machine.html": ['_("Set the status")', '_("Running now")'],
    "operations.html": ['_("Approve")', '_("Skip")', '_("Skip: leave out of the program")'],
    "gcode_program.html": ['_("Ready to run")'],
    "_turret_installs.html": ['_("Installed, X / Z offsets measured")', '_("All installed, X / Z offsets measured")'],
}


def test_the_machine_buttons_are_touch_size():
    assert re.search(r"button\.touch, summary\.touch \{ min-height: 44px; min-width: 44px;", CSS)
    for name, texts in BUTTONS.items():
        source = (TEMPLATES / name).read_text(encoding="utf-8")
        for text in texts:
            tags = re.findall(r"<(?:button|summary)\b[^>]*>\{\{ " + re.escape(text), source)
            assert tags and all('class="touch' in t or 'touch"' in t or " touch" in t for t in tags), (name, text)
