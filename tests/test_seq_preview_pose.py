"""Tests for the 3D pose the sequence preview applies (Ifrit/IfritSeq/seqpreviewpanel.py).

The preview's one piece of 3D math is pose_vertices(): take the model's posed vertices
and apply what the SEQUENCE did to the entity - Y rotation (E5 17), scale (E5 24-27),
hidden parts (E5 20) - plus the raw->viewer position mapping. It is pure on purpose so it
can be pinned here without GL: a sign error in the rotation or a part hidden off-by-one
would show as "the monster faces the wrong way" in the viewer, where nothing asserts.
"""
import pytest

from FF8GameData.monsterdata import PositionType
from FF8GameData.dat.sequencebake import SequenceFrame
from Ifrit.IfritSeq.seqpreviewpanel import (pose_vertices, sequence_position_world,
                                            build_frame_chains, frame_anim_text,
                                            _PSX_ONE)


def make_frame(position=(0, 0, 0), rotation_y=0, scale=(4096, 4096, 4096, 4096),
               hidden_part_mask=0, anim_id=0):
    return SequenceFrame(index=0, seq_id=1, address=0, anim_id=anim_id, anim_frame=0,
                         anim_total=1, position=position, rotation_y=rotation_y,
                         scale=scale, current_value=0,
                         hidden_part_mask=hidden_part_mask, is_waiting_animation=False,
                         command_list=[])


SQUARE = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (2.0, 3.0, 4.0)]


class TestPose:

    def test_a_neutral_pose_changes_nothing(self):
        posed = pose_vertices(SQUARE, make_frame(), [(0, len(SQUARE))])
        assert posed == SQUARE

    def test_a_quarter_turn_swings_x_into_minus_z(self):
        # rotation_y raw 1024 = a quarter of the engine's 4096-per-turn circle.
        posed = pose_vertices([(1.0, 0.0, 0.0)], make_frame(rotation_y=1024), [(0, 1)])
        x, y, z = posed[0]
        assert y == 0.0
        assert x == pytest.approx(0.0, abs=1e-9)
        assert abs(z) == pytest.approx(1.0, abs=1e-9)

    def test_a_full_turn_is_the_identity(self):
        posed = pose_vertices(SQUARE, make_frame(rotation_y=4096), [(0, len(SQUARE))])
        for (px, py, pz), (x, y, z) in zip(posed, SQUARE):
            assert (px, py, pz) == pytest.approx((x, y, z), abs=1e-9)

    def test_the_model_scale_multiplies_every_axis(self):
        posed = pose_vertices([(1.0, 2.0, 3.0)],
                              make_frame(scale=(2 * _PSX_ONE,) + (_PSX_ONE,) * 3),
                              [(0, 1)])
        assert posed[0] == pytest.approx((2.0, 4.0, 6.0))

    def test_a_per_axis_scale_only_touches_its_axis(self):
        posed = pose_vertices([(1.0, 2.0, 3.0)],
                              make_frame(scale=(_PSX_ONE, _PSX_ONE, 2 * _PSX_ONE,
                                                _PSX_ONE)),
                              [(0, 1)])
        assert posed[0] == pytest.approx((1.0, 4.0, 3.0))

    def test_a_hidden_part_collapses_only_its_own_vertices(self):
        # Two parts of two vertices each; hiding part 0 (mask bit 0) must not move part 1.
        vertex_list = [(1.0, 1.0, 1.0), (2.0, 2.0, 2.0), (3.0, 3.0, 3.0), (4.0, 4.0, 4.0)]
        posed = pose_vertices(vertex_list, make_frame(hidden_part_mask=0b01),
                              [(0, 2), (2, 4)])
        assert posed[0] == (0.0, 0.0, 0.0)
        assert posed[1] == (0.0, 0.0, 0.0)
        assert posed[2:] == vertex_list[2:]

    def test_rotation_is_about_the_entity_origin_not_the_mesh_centre(self):
        # A vertex at the origin must stay put whatever the rotation.
        posed = pose_vertices([(0.0, 0.0, 0.0)], make_frame(rotation_y=2048), [(0, 1)])
        assert posed[0] == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)


class TestPositionMapping:

    def test_the_sequence_position_uses_the_shared_axis_convention(self):
        # The same per-axis mapping the animation root position uses: X and Y mirrored,
        # Z not (PositionType.AXIS_SCALE), at the same 1/204.8 magnitude.
        world = sequence_position_world((100, 200, 300))
        assert world[0] == pytest.approx(100 * PositionType.AXIS_SCALE[0])
        assert world[1] == pytest.approx(200 * PositionType.AXIS_SCALE[1])
        assert world[2] == pytest.approx(300 * PositionType.AXIS_SCALE[2])
        assert world[0] < 0 and world[1] < 0 and world[2] > 0

    def test_a_zero_position_maps_to_zero(self):
        assert sequence_position_world((0, 0, 0)) == (0.0, 0.0, 0.0)


class TestFrameDetail:

    def test_an_empty_frame_has_no_chains(self):
        assert build_frame_chains(make_frame(anim_id=None)) == []

    def test_the_animation_state_is_named(self):
        assert "anim 0" in frame_anim_text(make_frame())
        assert frame_anim_text(make_frame(anim_id=None)) == ""
