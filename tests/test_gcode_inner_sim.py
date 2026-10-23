"""The simulation inside: the hole per z, a drill as its own shape, a boring bar with its head (DMIN), its overhang and
its spindle direction, cuts beyond the finished bore and material left in it."""
from turnpilot.gcode import profile as gp
from turnpilot.gcode import simulate as gsim

# a bushing Ø30 × 40, Z0 on its left face; a blind bore Ø16 × 20 from the left face with a drill-point bottom
FEATURES = [gp.FeatureData(id=1, type="od_turn", diameter=30, length=40),
            gp.FeatureData(id=2, type="bore", diameter=16, length=20, bore_open_end="left", bore_from=0,
                           bore_bottom="drill_point")]
DRILL = gsim.ToolInfo("drilling", name="Drill 14", diameter=14, point_angle=140)
BAR = gsim.ToolInfo("boring", edge=2.0, name="Boring bar", dmin=10, overhang=40, spindle="M03")


def sim(body, tools=None, features=FEATURES, face_stock=0.0):
    profile = gp.build(features, "left")
    data = gsim.SimInput(profile=profile, stock_radius=15.0, face_stock=face_stock, stickout=50.0, chuck_safety=2.0,
                         max_rpm=3000, spindle="M04", tools=tools or {1: DRILL, 2: BAR})
    text = "%\nO0001 (T)\nG21 G18 G40 G99\n" + body.strip() + "\nM05\nG28 U0.\nG28 W0.\nM30\n%\n"
    return gsim.simulate(text, data)


def errors(result):
    return [m for _, m in result.errors]


DRILLING = """
N1 (DRILL)
G28 U0.
G28 W0.
T0101
G97 S1000 M04
G00 X40. Z5.
G00 X0.
G00 Z2.
G01 Z-22.548 F0.1
G00 Z5.
M01
"""
# Ø14 drill: the full Ø to Z-20, its 140° point 2.548 deeper


def boring(x_bore="X16.", z_end="Z-20."):
    return f"""
N2 (BORE)
G28 U0.
G28 W0.
T0202
G97 S1000 M03
G00 X40. Z5.
G00 X12. Z2.
G01 {x_bore} F0.1
G01 {z_end}
G01 X12.
G00 Z5.
G00 X40.
M01
"""


def test_drilled_and_bored_bushing_is_clean_and_complete():
    result = sim(DRILLING + boring())
    assert result.ok, result.errors
    assert result.complete, result.incomplete
    i = round((0.0 - (-10.0)) / gsim.DZ)  # Z-10: the bore's Ø16
    assert abs(result.final_bore[i] - 8.0) < 1e-9
    below = round(20.0 / gsim.DZ) + 100  # Z-21: inside the drill's cone (below the bore's bottom)
    assert 0 < result.final_bore[below] < 7.0
    assert result.blocks[0].bore[i] == 7.0 and result.blocks[1].bore[i] == 8.0


def test_bore_not_opened_is_left():
    result = sim(DRILLING)
    assert result.ok and not result.complete
    assert any(m.startswith("PART NOT COMPLETE: material left in the bore from Z0. to Z-20.") for m in result.incomplete)


def test_cut_beyond_the_bore_and_into_the_bottom():
    found = errors(sim(DRILLING + boring(x_bore="X17.")))
    assert any(m.startswith("cuts beyond the finished bore from Z") and "D16. none given" in m for m in found)
    found = errors(sim(DRILLING + boring(z_end="Z-21.")))
    assert any(m.startswith("cuts beyond the finished bore") for m in found)


def test_a_rapid_into_the_bottom():
    body = DRILLING + boring().replace("G00 X12. Z2.", "G00 X12. Z2.\nG00 Z-21.\nG00 Z2.")
    assert "G00 through material" in errors(sim(body))
    drill_deeper = DRILLING.replace("G00 Z5.\nM01", "G00 Z-30.\nG00 Z5.\nM01")
    assert "G00 through material: the drill meets material at Z-20.01" in errors(sim(drill_deeper))


def test_the_bars_head_across_the_axis():
    thick = gsim.ToolInfo("boring", edge=2.0, name="Boring bar", dmin=15, overhang=40, spindle="M03")
    found = errors(sim(DRILLING + boring(), tools={1: DRILL, 2: thick}))
    # edge at D12 in the Ø14 hole: across the axis 15 − 6 = 9 > 7
    assert any(m.startswith("the bar (DMIN 15) meets the wall across the axis") for m in found)
    unknown = gsim.ToolInfo("boring", edge=2.0, name="Boring bar", overhang=40, spindle="M03")
    assert "T02: DMIN of the bar is not set: whether its head fits the hole is not known" in errors(
        sim(DRILLING + boring(), tools={1: DRILL, 2: unknown}))


def test_the_bars_overhang_and_spindle_direction():
    short = gsim.ToolInfo("boring", edge=2.0, name="Boring bar", dmin=10, overhang=15, spindle="M03")
    assert "T02: goes 20.00 mm deep, the bar's overhang reaches 15" in errors(
        sim(DRILLING + boring(), tools={1: DRILL, 2: short}))
    unset = gsim.ToolInfo("boring", edge=2.0, name="Boring bar", dmin=10, spindle=None)
    found = errors(sim(DRILLING + boring(), tools={1: DRILL, 2: unset}))
    assert "M03: the spindle direction for T02 (a boring bar) is not set: the operator's, on the tool page" in found
    assert any(m.startswith("T02: the bar's overhang (its max depth) is not set") for m in found)
    other = gsim.ToolInfo("boring", edge=2.0, name="Boring bar", dmin=10, overhang=40, spindle="M04")
    assert "M03: the operator's direction for T02 (a boring bar) is M04" in errors(
        sim(DRILLING + boring(), tools={1: DRILL, 2: other}))


def test_too_deep_a_cut_for_the_bar():
    shallow = gsim.ToolInfo("boring", edge=0.5, name="Boring bar", dmin=10, overhang=40, spindle="M03")
    assert any(m.startswith("cuts 1.00 mm deep (per side), above the tool's ap max 0.5") for m in errors(
        sim(DRILLING + boring(), tools={1: DRILL, 2: shallow})))


def test_a_flat_bottom_left_as_the_drills_cone():
    flat = [FEATURES[0], gp.FeatureData(id=2, type="bore", diameter=16, length=20, bore_open_end="left", bore_from=0,
                                        bore_bottom="flat")]
    tip = DRILLING.replace("G01 Z-22.548 F0.1", "G01 Z-20. F0.1")  # the point to the bottom
    result = sim(tip + boring(z_end="Z-17."), features=flat)  # the bar stops above the drill's cone
    assert result.ok, result.errors
    assert any("material left in the bore" in m and "to Z-20." in m for m in result.incomplete)  # not faced
    result = sim(DRILLING + boring(), features=flat)  # the drill's point below a flat bottom
    assert any(m.startswith("cuts beyond the finished bore") and "T01" in m for m in errors(result))


def test_inside_not_checked_without_the_bores_place():
    unplaced = [FEATURES[0], gp.FeatureData(id=2, type="bore", diameter=16, length=20)]
    result = sim(DRILLING, features=unplaced)
    assert result.ok and result.complete  # the drill only meets its own hole
    assert not gp.build(unplaced, "left").inner_known


def test_profile_places_the_bores():
    profile = gp.build(FEATURES, "right")  # the bore opens at the left face: the chuck end when Z0 is on the right
    (bore,) = profile.bores
    assert (bore.z_top, bore.z_bottom) == (-20, -40) and "needs a second set-up" in bore.problem
    profile = gp.build(FEATURES + [gp.FeatureData(id=3, type="chamfer", diameter=16, length=1, location="internal",
                                                  face="left")], "left")
    assert profile.bores[0].chamfer == 1 and profile.inner(-0.5) == 8.5 and profile.inner(-5) == 8
    assert profile.drill_zone(-21) and not profile.drill_zone(-29)
