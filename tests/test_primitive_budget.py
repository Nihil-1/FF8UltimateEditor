"""The 3D info bar scores a model against the per-frame PACKET-BUFFER budget (3584 prims). That
buffer only holds what the engine actually submits, so the % is based on the DRAWN count:

  * back-faces culled for the current view (the engine culls via GTE NCLIP before adding a polygon
    to the ordering table), and
  * 0xFE00 hidden faces excluded ALWAYS - independent of the 'show hidden faces' display toggle.

The 4096/object VERTEX cap stays a raw-storage figure. See Ifrit3DWidget._geometry_budget_html /
FF8OpenGLWidget.drawn_primitive_count.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QSettings
_APP = QApplication.instance() or QApplication([])

from Ifrit.ifritmanager import IfritManager
from Ifrit.ifritmonsterwidget import IfritMonsterWidget

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BATTLE = os.path.join(REPO, "extracted_files", "battle")


def _widget():
    base = IfritManager("FF8GameData")
    w = IfritMonsterWidget(settings=QSettings("test", "prim_budget"),
                           icon_path="Resources", game_data_folder="FF8GameData")
    w._game_data = base.game_data
    w.show()
    return w


def _viewer_for(w, name):
    p = os.path.join(BATTLE, name)
    if not os.path.isfile(p):
        pytest.skip(f"{name} not available")
    w._build_session([p])
    _APP.processEvents()
    v = w._files[0]['pane']._3d_widget
    v.gl_widget._recompute_cull_masks()      # offscreen may not paint; force one cull pass
    return v


def _raw_prims(v):
    total = 0
    for obj in v.ifrit_manager.enemy.geometry_data.object_data:
        total += (obj.nb_triangle + obj.nb_quad
                  + obj.nb_colored_triangle + obj.nb_colored_quad)
    return total


def test_backface_culling_lowers_the_count_below_raw():
    w = _widget()
    v = _viewer_for(w, "d0c000.dat")             # Squall: no hidden faces, so this isolates cull
    raw = _raw_prims(v)
    drawn = v.gl_widget.drawn_primitive_count()
    assert 0 < drawn < raw                        # some faces face away -> fewer than raw
    assert drawn < 0.75 * raw                      # a closed body culls a big chunk


def test_hidden_faces_excluded_regardless_of_display_toggle():
    w = _widget()
    v = _viewer_for(w, "d0w006.dat")             # Lion Heart: ~65% of faces flagged hidden
    v._show_hidden_faces = True                   # display them...
    v._refresh_static_geometry(); v.gl_widget._recompute_cull_masks()
    shown = v.gl_widget.drawn_primitive_count()
    v._show_hidden_faces = False                  # ...now hide them
    v._refresh_static_geometry(); v.gl_widget._recompute_cull_masks()
    hidden = v.gl_widget.drawn_primitive_count()
    assert shown == hidden                         # budget never counts engine-hidden faces


def test_info_label_reports_drawn_and_raw():
    w = _widget()
    v = _viewer_for(w, "d0c000.dat")
    html = v._geometry_budget_html()
    assert "drawn" in html
    assert "raw" in html                           # raw shown alongside when they differ
    assert "scene budget" in html


def test_count_survives_a_geometry_change_before_the_next_paint():
    """The cull mask is a per-paint cache. A weapon swap (or toggling hidden faces) replaces the
    face lists and immediately updates the info bar - drawn_primitive_count() - BEFORE the next
    paint recomputes the mask, so the count meets a mask one geometry behind. It must fall back to
    the raw list rather than broadcast a stale mask against the new one (the crash this guards)."""
    w = _widget()
    v = _viewer_for(w, "d0c000.dat")
    gl = v.gl_widget
    # A real cull mask for the geometry as it stands (what the last paint left behind).
    gl._recompute_cull_masks()
    assert gl._tri_cull_mask is not None

    # Now the face lists grow/shrink with no repaint - exactly _refresh_static_geometry's order:
    # it swaps the UV lists AND the aligned hidden masks, but not the cull mask (a paint's job).
    # The original crash was the stale cull mask OR'd against a hidden mask of the NEW length, so
    # the hidden mask has to be re-aligned here for this to reproduce it.
    def swap_tris(new_list):
        gl.set_triangles_with_uv(new_list)
        gl.set_budget_hidden_masks([False] * len(new_list),
                                   [False] * len(gl.quads_uv))

    swap_tris(list(gl.triangles_uv) + list(gl.triangles_uv)[:20])   # mask shorter than the list
    assert gl.drawn_primitive_count() >= 0         # no ValueError

    swap_tris(list(gl.triangles_uv)[:5])           # mask longer than the list
    drawn = gl.drawn_primitive_count()
    assert drawn >= 0

    # Once the paint's recompute runs, the mask fits again and culling is back on.
    gl._recompute_cull_masks()
    assert gl.drawn_primitive_count() <= len(gl.triangles_uv) + len(gl.quads_uv)
