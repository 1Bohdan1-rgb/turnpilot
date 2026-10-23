"""The buttons pressed at the machine are big enough for a finger: class "touch" (min 44 × 44 px in style.css)."""
import re
from pathlib import Path

TEMPLATES = Path(__file__).parent.parent / "turnpilot" / "templates"
CSS = (Path(__file__).parent.parent / "turnpilot" / "static" / "style.css").read_text(encoding="utf-8")

BUTTONS = {  # template: the texts of its buttons that must be "touch"
    "_order_panel.html": ['_("Done")', '_("Save parts done")', '_("Running now on {machine}"', '_("Cancel the mark")',
                          '_("Take the latest program")', '_("Close the order")'],
    "shop_machine.html": ['_("Set the status")', '_("Running now")'],
    "operations.html": ['_("Approve")', '_("Skip")', '_("Skip: leave out of the program")', '_("Unskip")'],
    "gcode_program.html": ['_("Ready to run")'],
    "_turret_installs.html": ['_("Installed, X / Z offsets measured")', '_("All installed, X / Z offsets measured")',
                              '_("Cancel the mark")'],
}


def test_the_machine_buttons_are_touch_size():
    assert re.search(r"button\.touch, summary\.touch \{ min-height: 44px; min-width: 44px;", CSS)
    for name, texts in BUTTONS.items():
        source = (TEMPLATES / name).read_text(encoding="utf-8")
        for text in texts:
            tags = re.findall(r"<(?:button|summary)\b[^>]*>\{\{ " + re.escape(text), source)
            assert tags and all('class="touch' in t or 'touch"' in t or " touch" in t for t in tags), (name, text)


def test_take_the_latest_program_stands_out_when_there_is_a_newer_one(client):
    from gcode_jobs import make_job, ready_machine
    from turnpilot import services

    machine = ready_machine()
    job = make_job()
    services.generate_gcode(job, machine)
    order = services.create_order(job, machine, 5, "Bohdan")
    assert services.order_progress(order)["newer_program"] is False
    page = client.get(f"/orders/{order.id}").get_data(as_text=True)
    assert re.search(r'<button class="touch" formaction="[^"]*/program">Take the latest program', page)
    services.generate_gcode(job, machine)
    assert services.order_progress(order)["newer_program"] is True
    page = client.get(f"/orders/{order.id}").get_data(as_text=True)
    assert re.search(r'<button class="touch primary" formaction="[^"]*/program">Take the latest program', page)
