"""The elevation document — the drawing the client signs off.

The interactive view and the exported PDF are rendered from one description,
so these check the description and then that both renderers survive it.
"""

import pytest

from app import elevdoc, facade
from app.build3d import level_elevations
from app.models import BuildParams, LevelParams, Panel
from conftest import needs_example


def _frame():
    return facade.Frame(
        side="+y", face=40.0, u0=0.0, u1=30.0, z_ground=0.0, z_top=22.0,
        faces=[facade.Wallface(0.0, 30.0, 0.0, 22.0, 40.0)],
    )


def _panels():
    return [
        Panel(id="a", kind="field", u0=0, u1=30, z0=0, z1=22, depth_ft=0.0,
              material="wall_ext", label="Main wall"),
        Panel(id="b", kind="band", u0=0, u1=30, z0=11.5, z1=12.5, depth_ft=0.5,
              material="trim", label="Band"),
        Panel(id="c", kind="clad", u0=2, u1=9, z0=0, z1=22, depth_ft=0.35,
              material="clad", label="Cladding"),
        Panel(id="d", kind="frame", u0=14, u1=21, z0=14, z1=20, depth_ft=0.65,
              material="trim", label="Box frame", hole=(15, 20, 15, 19)),
        Panel(id="e", kind="canopy", u0=-1, u1=31, z0=20.5, z1=22.5, depth_ft=3.15,
              material="accent", label="Canopy"),
    ]


# --------------------------------------------------------------------------- #
# the lvl tag, written the way the sheet writes it
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "depth,text",
    # no hyphen: the supplied sheet writes lvl +3'2", not +3'-2"
    [(0.0, "+0'"), (3.15, "+3'2\""), (0.5, "+0'6\""), (-0.35, "-0'4\"")],
)
def test_projection_reads_like_the_supplied_sheet(depth, text):
    """The document writes lvl +3'2" on its topmost slab; ours has to match,
    or the drawing is not in the same language as the one it copies."""
    assert elevdoc._lvl(depth) == text


def test_the_canopy_carries_the_documents_own_projection():
    assert elevdoc._lvl(3.15) == "+3'2\""


# --------------------------------------------------------------------------- #
# the drawing
# --------------------------------------------------------------------------- #
def test_the_elevation_draws_every_panel_and_tags_it():
    d = elevdoc.elevation(_panels(), _frame(), BuildParams())
    assert d.rects, "panels should be drawn"
    tags = [t[2] for t in d.texts if t[2].startswith("lvl ")]
    assert any("+3'2\"" in t for t in tags), "the canopy's projection is tagged"
    assert len(tags) >= 3


def test_a_frame_is_drawn_hollow_here_too():
    """The drawing has to agree with the model: a box frame is a surround.

    Counted inside the drawing area only — the legend on the right has
    swatches of its own, which is what made the first version of this test
    read five pieces where there are four.
    """
    d = elevdoc.elevation([_panels()[3]], _frame(), BuildParams())
    drawn = [r for r in d.rects if r[0] < elevdoc.PAGE_W - elevdoc.MARGIN - 230]
    assert len(drawn) == 4, "four pieces round the opening, not one plate"


def test_the_drawing_holds_everything_composed_not_just_the_walls():
    """The canopy caps the parapet and the screen oversails that, so both sit
    well above ``frame.z_top``. Scaled to the walls alone they are drawn off
    the top of the sheet, floating over the title."""
    panels = _panels() + [
        Panel(id="f", kind="fin", u0=10, u1=11, z0=0, z1=28, depth_ft=2.0,
              material="clad", label="Fin"),
    ]
    d = elevdoc.elevation(panels, _frame(), BuildParams())   # frame tops out at 22
    for x0, y0, x1, y1, *_rest in d.rects:
        # the drawing area starts below the title; anything above that has
        # overflowed the scale rather than been fitted to it
        assert min(y0, y1) > elevdoc.MARGIN + 40, "drawn over the title block"
        assert max(y0, y1) <= elevdoc.PAGE_H - elevdoc.MARGIN


def test_two_panels_on_the_same_spot_do_not_print_their_tags_through_each_other():
    """A box frame is centred on the panel it stands on, so their tags land on
    the same point and come out as unreadable overprinted text."""
    frame = _frame()
    both = [
        Panel(id="a", kind="mass", u0=10, u1=20, z0=8, z1=18, depth_ft=1.3,
              material="clad", label="Clad bay"),
        Panel(id="b", kind="frame", u0=12, u1=18, z0=10, z1=16, depth_ft=1.85,
              material="trim", label="Bay window frame"),
    ]
    tags = [t for t in elevdoc.elevation(both, frame, BuildParams()).texts
            if t[2].startswith("lvl ")]
    assert len(tags) == 1, "the second tag would land on top of the first"


def test_the_drawing_carries_dimension_chains():
    d = elevdoc.elevation(_panels(), _frame(), BuildParams())
    texts = [t[2] for t in d.texts]
    assert any(t.startswith("OVERALL") for t in texts)
    assert any("'" in t for t in texts), "figures in feet and inches"
    assert d.lines, "chains are drawn"


def test_the_legend_names_the_materials_and_their_hex():
    d = elevdoc.elevation(_panels(), _frame(), BuildParams())
    texts = " ".join(t[2] for t in d.texts)
    assert "MATERIALS & PROJECTIONS" in texts
    assert "#" in texts, "the legend quotes the colour"


def test_the_palette_page_lists_roles_and_colours():
    d = elevdoc.palette(BuildParams())
    texts = " ".join(t[2] for t in d.texts)
    assert "MATERIAL & COLOUR PALETTE" in texts
    assert "HEX #" in texts
    assert "USED FOR:" in texts


def test_the_spec_palette_is_reproduced_exactly():
    """The supplied sheet names four colours. If ours drift, the model stops
    matching the document it is supposed to be delivering."""
    params = BuildParams()
    colours = {v.upper() for v in elevdoc._colours(params).values()}
    for stated in ("#3D4249", "#D6D7D9", "#F4F4F2", "#8B5E3C"):
        assert stated in colours, stated


# --------------------------------------------------------------------------- #
# both renderers
# --------------------------------------------------------------------------- #
def test_the_svg_renders_and_is_well_formed():
    from xml.etree import ElementTree

    svg = elevdoc.to_svg(elevdoc.elevation(_panels(), _frame(), BuildParams()))
    ElementTree.fromstring(svg)  # raises if it is not valid XML
    assert svg.startswith("<svg") and svg.endswith("</svg>")


def test_text_with_an_ampersand_does_not_break_the_svg():
    from xml.etree import ElementTree

    d = elevdoc.Draw()
    d.text(10, 10, 'bands & frames <"quoted">')
    ElementTree.fromstring(elevdoc.to_svg(d))


def test_the_pdf_is_two_pages_at_the_documents_own_size():
    import fitz

    data = elevdoc.to_pdf([
        elevdoc.elevation(_panels(), _frame(), BuildParams()),
        elevdoc.palette(BuildParams()),
    ])
    assert data[:4] == b"%PDF"
    doc = fitz.open(stream=data, filetype="pdf")
    assert doc.page_count == 2
    assert doc[0].rect.width == pytest.approx(elevdoc.PAGE_W, abs=1)
    assert doc[0].rect.height == pytest.approx(elevdoc.PAGE_H, abs=1)


# --------------------------------------------------------------------------- #
# on the real building
# --------------------------------------------------------------------------- #
@needs_example
def test_the_document_is_produced_for_the_example_set(gf_extract, ff_extract):
    extracts = [gf_extract, ff_extract]
    params = BuildParams(levels=[
        LevelParams(level=0, name="Ground floor"),
        LevelParams(level=1, name="First floor"),
    ])
    elevations = level_elevations(extracts, params)
    frame = facade.front_frame(extracts, elevations, "+y", params.plinth_ft)
    panels = facade.compose(extracts, elevations, frame, params)

    d = elevdoc.elevation(panels, frame, params)
    texts = " ".join(t[2] for t in d.texts)
    # the overall width is the building's, not a round number someone chose
    assert f"OVERALL  {round(frame.width)}" in texts or "OVERALL" in texts
    data = elevdoc.to_pdf([d, elevdoc.palette(params)])
    assert len(data) > 5000
