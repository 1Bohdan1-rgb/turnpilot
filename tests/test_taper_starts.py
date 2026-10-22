"""Where a taper or an arc is roughed from (planner.taper_starts): the nearest turned section on its larger end's side,
past other tapers, arcs and grooves; the bar when there is none on that side (or a smaller one). НД 012: the taper
Ø30-35 sits after the arc R51.6, so it was roughed from the bar D55 and took its neighbours' metal."""
from turnpilot.planner import FeatureSpec as F
from turnpilot.planner import taper_starts

STOCK = 55.0


def starts(features):
    out = taper_starts(features, lambda f: f.diameter, STOCK, axial_order=True)
    return {f.id: (s.diameter, s.from_bar) for f in features if id(f) in out for s in [out[id(f)]]}


def test_a_taper_between_an_arc_and_a_groove_starts_from_the_section_past_the_arc():
    # Ø40 | arc Ø40 -> Ø35 | taper Ø35 -> Ø30 | groove | Ø30: the larger end (35) faces the arc
    features = [F(1, "od_turn", diameter=40, length=10), F(2, "arc", start_diameter=40, diameter=35, length=8, radius=50),
                F(3, "taper", start_diameter=35, diameter=30, length=10),
                F(4, "groove", diameter=25, start_diameter=30, length=3), F(5, "od_turn", diameter=30, length=10)]
    assert starts(features)[3] == (40, False)
    assert starts(features)[2] == (40, False)  # the arc too, from its turned neighbour


def test_a_taper_whose_larger_end_faces_a_groove_starts_past_the_groove():
    features = [F(1, "od_turn", diameter=40, length=10), F(2, "groove", diameter=30, start_diameter=40, length=3),
                F(3, "taper", start_diameter=36, diameter=30, length=10), F(4, "od_turn", diameter=30, length=10)]
    assert starts(features)[3] == (40, False)


def test_a_taper_at_the_end_face_starts_from_the_bar():
    # the larger end is the part's end: nothing on that side
    features = [F(1, "taper", start_diameter=40, diameter=30, length=10), F(2, "od_turn", diameter=30, length=20)]
    assert starts(features)[1] == (STOCK, True)


def test_two_tapers_in_a_row_both_start_from_the_turned_section():
    features = [F(1, "od_turn", diameter=40, length=10), F(2, "taper", start_diameter=38, diameter=34, length=8),
                F(3, "taper", start_diameter=34, diameter=30, length=8), F(4, "od_turn", diameter=30, length=10)]
    assert starts(features)[2] == (40, False) and starts(features)[3] == (40, False)


def test_a_smaller_section_on_the_larger_side_means_the_bar():
    features = [F(1, "od_turn", diameter=32, length=10), F(2, "arc", start_diameter=36, diameter=34, length=5, radius=30),
                F(3, "taper", start_diameter=34, diameter=30, length=8)]
    assert starts(features)[3] == (STOCK, True)  # the turned Ø32 is below the taper's Ø34


def test_without_the_axial_order_every_taper_starts_from_the_bar():
    features = [F(1, "od_turn", diameter=40, length=10), F(2, "taper", start_diameter=38, diameter=30, length=8)]
    out = taper_starts(features, lambda f: f.diameter, STOCK, axial_order=False)
    assert (out[id(features[1])].diameter, out[id(features[1])].from_bar) == (STOCK, True)
