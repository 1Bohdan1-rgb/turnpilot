"""Roadmap stage 5, step 6: the operator confirms, corrects or rejects each row; conflicts; rows entered by hand."""
from turnpilot import cutting_data as cd
from turnpilot import services
from turnpilot.models import CuttingDataRow, Material, Tool, db


def read_row(**kw):
    data = dict(kind="geometry", catalogue="TT 2020", insert_code="CNMG120408-PM", material_group="P1.2",
                ap_min=0.5, ap_rec=3, ap_max=5.5, f_min=0.15, f_rec=0.3, f_max=0.5, page="A283", origin="model",
                status="read", quote="CNMG 12 04 08-PM 0.5 3 5.5")
    data.update(kw)
    row = CuttingDataRow(**data)
    db.session.add(row)
    db.session.commit()
    return row


def form_of(row, decision="confirm", **changes):
    form = {f"decision-{row.id}": decision}
    for name in cd.GEOMETRY_FIELDS if row.kind == "geometry" else ("vc_min", "vc_max", "vc_points"):
        form[f"{row.id}-{name}"] = cd._fmt(getattr(row, name)) if name != "vc_points" else (row.vc_points or "")
    if row.kind == "grade_vc":
        form[f"{row.id}-coolant"] = {True: "yes", False: "no"}.get(row.coolant, "")
    form.update({f"{row.id}-{k}": v for k, v in changes.items()})
    return form


def test_review_page_lists_rows_to_check_with_their_source(app, client):
    row = read_row(checks='["not on the page: 3.5"]')
    page = client.get("/cutting-data/review").get_data(as_text=True)
    assert "CNMG120408-PM" in page and "not on the page: 3.5" in page and "p. A283" in page
    assert f'name="{row.id}-ap_rec" value="3"' in page


def test_confirm_as_read_and_reject(app, client):
    good, bad = read_row(), read_row(insert_code="DNMG150604-PF")
    response = client.post("/cutting-data/review", data={**form_of(good), f"decision-{bad.id}": "reject"},
                           follow_redirects=True)
    assert "1 row(s) confirmed, 1 rejected." in response.get_data(as_text=True)
    assert (good.status, good.origin, bad.status) == ("confirmed", "model", "rejected")
    assert good.confirmed_at is not None


def test_a_changed_row_is_the_operators(app, client):
    row = read_row()
    client.post("/cutting-data/review", data=form_of(row, ap_rec="2,5"))
    assert (row.status, row.origin, row.ap_rec) == ("confirmed", "operator", 2.5)
    assert "changed by the operator: ap_rec 3 → 2.5" in row.note


def test_left_rows_stay_to_check(app, client):
    row = read_row()
    client.post("/cutting-data/review", data=form_of(row, decision=""))
    assert row.status == "read"


def test_invalid_values_save_nothing(app, client):
    a, b = read_row(insert_code="DNMG150604-PF"), read_row()
    response = client.post("/cutting-data/review", data={**form_of(a), **form_of(b, ap_rec="9")}, follow_redirects=True)
    assert "ap: min / rec / max are not in order. Nothing was saved." in response.get_data(as_text=True)
    assert (db.session.get(CuttingDataRow, a.id).status, db.session.get(CuttingDataRow, b.id).status) == ("read", "read")


def test_a_conflict_needs_the_replace_tick(app, client):
    old = read_row(status="confirmed", origin="hand_typed")
    new = read_row(ap_rec=2.5)
    page = client.get("/cutting-data/review").get_data(as_text=True)
    assert "replace it" in page
    response = client.post("/cutting-data/review", data=form_of(new), follow_redirects=True)
    assert "a confirmed row gives other values" in response.get_data(as_text=True)
    assert (db.session.get(CuttingDataRow, old.id).status, db.session.get(CuttingDataRow, new.id).status) == (
        "confirmed", "read")
    client.post("/cutting-data/review", data={**form_of(new), f"replace-{new.id}": "1"})
    assert (db.session.get(CuttingDataRow, old.id).status, db.session.get(CuttingDataRow, new.id).status) == (
        "replaced", "confirmed")


def test_the_same_values_replace_without_a_tick(app, client):
    old = read_row(status="confirmed", origin="hand_typed")
    new = read_row()
    client.post("/cutting-data/review", data=form_of(new))
    assert (db.session.get(CuttingDataRow, old.id).status, new.status) == ("replaced", "confirmed")


def test_grade_row_review(app, client):
    row = read_row(kind="grade_vc", insert_code=None, grade="GC4325", application="turning",
                   ap_min=None, ap_rec=None, ap_max=None, f_min=None, f_rec=None, f_max=None,
                   vc_points="0.1:455, 0.4:305, 0.8:215", coolant=True)
    client.post("/cutting-data/review", data=form_of(row, vc_points="0.1:450, 0.4:305, 0.8:215"))
    assert (row.status, row.vc_points, row.coolant, row.origin) == ("confirmed", "0.1:450, 0.4:305, 0.8:215", True,
                                                                     "operator")


def test_hand_entry_of_a_graph_value(app, client):
    form = dict(kind="geometry", insert_code=" N123G2-0300-0003-GM ", material_group="P1.2", f_min="0.04",
                f_rec="0.07", f_max="0.14", catalogue="Sandvik Coromant Turning tools 2020", page="B139",
                from_graph="1")
    response = client.post("/cutting-data/add", data=form, follow_redirects=True)
    assert "entered and confirmed" in response.get_data(as_text=True)
    row = db.session.execute(db.select(CuttingDataRow)).scalar_one()
    assert (row.insert_code, row.status, row.origin, row.from_graph, row.f_rec) == (
        "N123G2-0300-0003-GM", "confirmed", "operator", True, 0.07)


def test_hand_entry_needs_its_source(app, client):
    response = client.post("/cutting-data/add", data=dict(kind="geometry", insert_code="X", material_group="P1.2",
                                                          f_rec="0.1", catalogue="TT"), follow_redirects=True)
    assert "page and material group are required" in response.get_data(as_text=True)
    assert db.session.execute(db.select(CuttingDataRow)).first() is None


def test_tool_library_shows_the_confirmed_rows(app, client):
    read_row(status="confirmed")
    page = client.get("/machine").get_data(as_text=True)
    assert "P1.2: ap/f ✓, Vc —" in page
    tools = db.session.execute(db.select(Tool)).scalars().all()
    groups = [m.catalogue_group for m in db.session.execute(db.select(Material)).scalars() if m.catalogue_group]
    status = services.tool_catalogue_status(tools, groups, services.confirmed_rows())
    rough = next(t for t in tools if t.insert_code == "CNMG120408-PM")
    assert status[rough.id] == [("P1.2", True, False)]


def test_page_titles_are_plain(app, client):
    import re

    for url in ("/cutting-data", "/cutting-data/review", "/cutting-data/catalogues"):
        title = re.search(r"<title>(.*?)</title>", client.get(url).get_data(as_text=True), re.S).group(1)
        assert "<" not in title and len(title) < 60, url
