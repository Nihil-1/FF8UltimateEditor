"""Tests for the graphical timeline (Ifrit/IfritSeq/seqtimelinewidget.py).

What is pinned is the FOLD from a BakeResult into the drawn model - blocks, bars, lanes -
because that is where the widget could lie: a lane that drops an event, a poll drawn as a
wall of markers, an animation block whose edges drift off the bake's frames. The painting
itself is not asserted (it is a QPainter dump); the geometry helpers the mouse relies on
(frame <-> x) are, since a seek that lands on the wrong frame makes the scrub bar wrong.
"""
import pathlib
import sys

import pytest
from PyQt6.QtWidgets import QApplication

from FF8GameData.gamedata import GameData
from FF8GameData.dat.sequencebake import (bake_sequence, EVENT_SOUND, EVENT_EFFECT,
                                          EVENT_FLOW, STOP_END, STOP_LOOP)
from Ifrit.IfritSeq.seqtimelinewidget import (build_timeline_model,
                                              SequenceTimelineWidget)

PROJECT_ROOT = pathlib.Path(__file__).parent.parent


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(scope="session")
def game_data():
    data = GameData(str(PROJECT_ROOT / "FF8GameData"))
    data.load_all()
    return data


def _bake(game_data, sequence_list, frame_count=None, **kwargs):
    sequence_by_id = {index + 1: bytes(data) for index, data in enumerate(sequence_list)}
    return bake_sequence(game_data, sequence_by_id, 1, frame_count or {}, **kwargs)


class TestTheDrawnModel:

    def test_animation_blocks_cover_the_animated_frames_edge_to_edge(self, game_data):
        # 00 (4 frames) then 01 (3 frames) then A9: two blocks, adjacent, whose edges are
        # the bake's own frame indices.
        result = _bake(game_data, [[0x00, 0x01, 0xA9]], {0: 4, 1: 3})
        model = build_timeline_model(result)
        assert [block.anim_id for block in model.animation_blocks] == [0, 1]
        first, second = model.animation_blocks
        assert first.first_frame == 0
        assert second.first_frame == first.last_frame + 1
        assert second.last_frame == result.frame_list[-1].index

    def test_waiting_frames_are_marked(self, game_data):
        # 00 queues a 5 frame animation and waits for it. The bake flags the queueing
        # frame itself as waiting too (the pause starts on it), so 0-4 all carry the mark.
        result = _bake(game_data, [[0x00, 0xA9]], {0: 5})
        model = build_timeline_model(result)
        assert model.waiting_frame_set == {0, 1, 2, 3, 4}

    def test_only_the_kinds_that_happen_get_a_lane(self, game_data):
        # One sound (B5) and the A9 terminator: a sound lane and a flow lane, nothing
        # else - no empty effect/text/target lanes padding the widget.
        result = _bake(game_data, [[0x00, 0xB5, 0x01, 0x00, 0xA9]], {0: 3})
        model = build_timeline_model(result)
        assert [kind for kind, _label, _bar_list in model.lane_list] == \
               [EVENT_SOUND, EVENT_FLOW]

    def test_a_per_frame_poll_folds_into_one_bar(self, game_data):
        # B5 / A1 / jump back to 0: the SAME B5 (same address) runs on every frame. One
        # bar spanning them all, not one marker per frame burying the timeline.
        result = _bake(game_data, [[0xB5, 0x01, 0x00, 0xA1, 0xE6, 0xFC]])
        model = build_timeline_model(result)
        assert result.stop_reason == STOP_LOOP   # the premise: it really does repeat
        sound_lane = [bar_list for kind, _label, bar_list in model.lane_list
                      if kind == EVENT_SOUND]
        assert len(sound_lane) == 1
        bar_list = sound_lane[0]
        assert len(bar_list) == 1
        assert bar_list[0].first_frame == 0
        assert bar_list[0].last_frame == result.frame_list[-1].index

    def test_the_same_command_on_separate_frames_stays_separate_bars(self, game_data):
        # B4 on frame 0 and again after the animation ends: two occurrences, two bars.
        result = _bake(game_data,
                       [[0xB4, 0x15, 0x00, 0x00, 0xB4, 0x15, 0x00, 0xA9]], {0: 4})
        model = build_timeline_model(result)
        effect_lane = [bar_list for kind, _label, bar_list in model.lane_list
                       if kind == EVENT_EFFECT]
        assert len(effect_lane) == 1
        bar_list = effect_lane[0]
        assert len(bar_list) == 2
        assert [bar.first_frame for bar in bar_list] == [0, 4]

    def test_the_stop_reason_and_loop_frame_ride_along(self, game_data):
        looping = build_timeline_model(_bake(game_data, [[0x00, 0xA7, 0x01]], {0: 2}))
        assert looping.stop_reason == STOP_LOOP
        assert looping.loop_from is not None
        ending = build_timeline_model(_bake(game_data, [[0xA1, 0xA9]]))
        assert ending.stop_reason == STOP_END
        assert ending.loop_from is None


class TestTheWidgetGeometry:
    """frame <-> pixel mapping: what a click seeks and where the playhead lands."""

    def test_x_and_frame_at_are_inverse_over_the_whole_run(self, qapp, game_data):
        widget = SequenceTimelineWidget()
        widget.set_result(_bake(game_data, [[0x00, 0xA9]], {0: 8}))
        for frame in range(widget._model.nb_frame):
            x = widget._x(frame) + widget._px_per_frame / 2   # middle of the frame cell
            assert widget._frame_at(x) == frame

    def test_a_click_left_of_frame_zero_and_right_of_the_end_clamp(self, qapp, game_data):
        widget = SequenceTimelineWidget()
        widget.set_result(_bake(game_data, [[0x00, 0xA9]], {0: 4}))
        assert widget._frame_at(0) == 0
        assert widget._frame_at(10_000) == widget._model.nb_frame - 1

    def test_the_playhead_clamps_to_the_new_bake_on_rebake(self, qapp, game_data):
        widget = SequenceTimelineWidget()
        widget.set_result(_bake(game_data, [[0x00, 0xA9]], {0: 8}))
        widget.set_current_frame(8)
        assert widget.current_frame() == 8
        widget.set_result(_bake(game_data, [[0xA1, 0xA9]]))   # now only 2 frames
        assert widget.current_frame() <= 1

    def test_the_width_follows_the_frame_count(self, qapp, game_data):
        widget = SequenceTimelineWidget()
        widget.set_result(_bake(game_data, [[0xA1, 0xA9]]))
        short = widget.minimumSizeHint().width()
        widget.set_result(_bake(game_data, [[0x00, 0xA9]], {0: 60}))
        assert widget.minimumSizeHint().width() > short
