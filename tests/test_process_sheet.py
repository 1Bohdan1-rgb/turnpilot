"""The process sheet fits the screen: the notes behind ⓘ (a popover), a warning stays in sight."""
import re

from gcode_jobs import make_job, ready_machine


def test_notes_behind_the_info_button_and_no_notes_column(client):
    ready_machine()
    job = make_job()
    page = client.get(f"/jobs/{job.id}/operations").get_data(as_text=True)
    assert "<th>Notes</th>" not in page
    with_note = [op for op in job.current_operations if op.note]
    assert with_note
    for op in with_note:
        assert f'popovertarget="note-{op.id}"' in page and f'<div popover id="note-{op.id}" class="note-pop">' in page
        pop = re.search(rf'<div popover id="note-{op.id}" class="note-pop">(.*?)\n\s*</div>', page, re.S).group(1)
        assert op.note.split(";")[0] in pop  # the catalogue text, as text
        assert "<a " not in pop
    # 11 columns at most: #, feature, operation, tool, Vc, n, f, ap, passes, status, actions
    head = page.split("<thead>", 1)[1].split("</thead>", 1)[0]
    assert len(re.findall(r"<th[ >]", head)) == 11


def test_a_warning_is_shown_without_opening_anything(client):
    ready_machine()
    job = make_job()
    page = client.get(f"/jobs/{job.id}/operations").get_data(as_text=True)
    warned = [op for op in job.current_operations if op.warning]
    assert warned
    for op in warned:
        before_popover = page.split(f'id="note-{op.id}"', 1)[-1]
        assert f'<div class="warning small">⚠ {op.warning.split(":")[0]}' in before_popover
