"""Tests for the preview's in-render text overlay (Ifrit/IfritSeq/seqpreviewpanel.py).

The overlay is the readable side of a running sequence: BB's attack-name banner (found by
reverse-looking-up which monster ability queues this sequence), 0x91's monster battle
text, and a transient "effect playing" notice - including the flag-0x20 fire signal that
launches the parallel effect code with no effect op in sight (the missile case). What is
pinned is the pure fold from a BakeResult to one string per frame, so scrubbing anywhere
shows the right state.
"""
import pathlib

import pytest

from FF8GameData.gamedata import GameData
from FF8GameData.dat.sequencebake import bake_sequence
from Ifrit.IfritSeq.seqpreviewpanel import (build_overlay_texts, ability_name,
                                            sequence_attack_names)

PROJECT_ROOT = pathlib.Path(__file__).parent.parent


@pytest.fixture(scope="session")
def game_data():
    data = GameData(str(PROJECT_ROOT / "FF8GameData"))
    data.load_all()
    return data


def _bake(game_data, sequence_list, frame_count=None, **kwargs):
    sequence_by_id = {index + 1: bytes(data) for index, data in enumerate(sequence_list)}
    return bake_sequence(game_data, sequence_by_id, 1, frame_count or {}, **kwargs)


def _overlay(result, names=(), texts=()):
    return build_overlay_texts(
        result,
        attack_name_of_seq=lambda _sid: list(names),
        battle_text_of=lambda index: texts[index] if index < len(texts) else "")


class TestAbilityNames:

    def test_the_name_comes_from_the_json_the_type_points_at(self, game_data):
        assert ability_name(game_data, 2, 8) == "Thundara"        # kernel magic
        assert ability_name(game_data, 8, 201) == "Micro Missiles"  # monster ability
        assert ability_name(game_data, 0, 5) == ""                # type 0 = not defined

    def test_the_reverse_lookup_finds_the_ability_of_a_sequence(self, game_data):
        class _Enemy:
            info_stat_data = {"abilities_low": [{"type": 8, "animation": 12, "id": 200}],
                              "abilities_med": [], "abilities_high": []}
        assert sequence_attack_names(game_data, _Enemy(), 12) == ["Ray Bomb"]
        assert sequence_attack_names(game_data, _Enemy(), 13) == []


class TestOverlayFold:

    def test_bb_raises_the_attack_name_banner_and_it_stays_up(self, game_data):
        result = _bake(game_data, [[0xBB, 0xA1, 0xA1, 0xA9]])
        overlay = _overlay(result, names=["Micro Missiles"])
        assert overlay[0][0] == "Micro Missiles"
        assert overlay[-1][0] == "Micro Missiles"    # a banner, not a blink

    def test_a_sequence_no_ability_queues_still_says_a_name_is_printed(self, game_data):
        result = _bake(game_data, [[0xBB, 0xA9]])
        assert "(attack name)" in _overlay(result)[0][0]

    def test_91_shows_the_monsters_own_battle_text(self, game_data):
        result = _bake(game_data, [[0x91, 0x01, 0x00, 0xA9]])
        overlay = _overlay(result, texts=["first", "You will fall"])
        assert overlay[0][0] == "You will fall"

    def test_an_effect_notice_appears_and_expires(self, game_data):
        # B4 fires on frame 0 of a run long enough to outlive the notice.
        result = _bake(game_data, [[0xB4, 0x15, 0x00, 0x00, 0xA9]], {0: 20})
        overlay = _overlay(result)
        assert "effect playing" in overlay[0][1]
        assert overlay[-1][1] == ""                  # long gone by the end

    def test_the_flag_0x20_fire_signal_is_called_out(self, game_data):
        # No effect op at all - the missile idiom: raise bit 0x20 for the parallel
        # effect code (C3 08 / D9 20 / E5 08), like c0m001 seq 12 does per salvo.
        result = _bake(game_data, [[0xC3, 0x08, 0xD9, 0x20, 0xE5, 0x08,
                                    0x00, 0xA9]], {0: 4})
        assert "fire signal" in _overlay(result)[0][1]


class TestPresentationSpansOnTheTimeline:
    """Text and effect bars span how long the thing is SHOWN, not the one frame the
    command ran on."""

    def test_a_text_banner_bar_runs_to_the_end_of_the_bake(self, game_data):
        from Ifrit.IfritSeq.seqtimelinewidget import build_timeline_model
        from FF8GameData.dat.sequencebake import EVENT_TEXT
        result = _bake(game_data, [[0xBB, 0x00, 0xA9]], {0: 12})
        model = build_timeline_model(result)
        text_lane = [bar_list for kind, _l, bar_list in model.lane_list
                     if kind == EVENT_TEXT]
        (bar,), = text_lane
        assert bar.first_frame == 0
        assert bar.last_frame == result.frame_list[-1].index

    def test_an_effect_bar_holds_its_display_frames(self, game_data):
        from Ifrit.IfritSeq.seqtimelinewidget import (build_timeline_model,
                                                      EFFECT_HOLD_FRAMES)
        from FF8GameData.dat.sequencebake import EVENT_EFFECT
        result = _bake(game_data, [[0xB4, 0x15, 0x00, 0x00, 0xA9]], {0: 20})
        model = build_timeline_model(result)
        effect_lane = [bar_list for kind, _l, bar_list in model.lane_list
                       if kind == EVENT_EFFECT]
        (bar,), = effect_lane
        assert bar.nb_frame == EFFECT_HOLD_FRAMES

    def test_the_fire_signal_gets_an_effect_bar_of_its_own(self, game_data):
        from Ifrit.IfritSeq.seqtimelinewidget import build_timeline_model
        from FF8GameData.dat.sequencebake import EVENT_EFFECT
        result = _bake(game_data, [[0xC3, 0x08, 0xD9, 0x20, 0xE5, 0x08,
                                    0x00, 0xA9]], {0: 12})
        model = build_timeline_model(result)
        effect_lane = [bar_list for kind, _l, bar_list in model.lane_list
                       if kind == EVENT_EFFECT]
        assert effect_lane and "fire signal" in effect_lane[0][0].description


def _note_of(result, op_code):
    """The first value_note carried by a command with that op code."""
    for frame in result.frame_list:
        for command in frame.command_list:
            if command.op_code == op_code and command.value_note:
                return command.value_note
    return ""


class TestValueNotes:
    """Every meaningful write and branch carries its own story: the formula that built
    the value (battle reads as name(value), scratch slots spliced through) and where it
    went - shown on the command line, in the timeline tooltip, and on the value lane."""

    def test_an_engine_write_notes_its_formula(self, game_data):
        # The jump-height idiom: speed-to-target minus 100 goes through stack[FF] and
        # negation into pos_y. The note must show the WHOLE formula, not "stack[FF]".
        result = _bake(game_data, [[0xC3, 0x11, 0xC9, 0x64, 0xE5, 0xFF,
                                    0xC1, 0x00, 0xCB, 0xFF, 0xE5, 0x0F, 0xA1, 0xA9]])
        assert _note_of(result, 0xE5) == \
            "pos_forward ← 0 - (adj_speed_to_target(2500) - 100) = -2400"

    def test_a_computed_value_shows_its_formula(self, game_data):
        result = _bake(game_data,
                       [[0xC0, 0x60, 0xF6, 0xE5, 0x02,        # e5[2] = -2464
                         0x00, 0x00,                          # two anim frames pass
                         0xC3, 0x02, 0xCF, 0x09, 0xD3, 0x0A,  # value = e5[2]*frame/total
                         0xE5, 0x0F, 0xA9]], {0: 4})
        note_list = [c.value_note for f in result.frame_list for c in f.command_list
                     if c.value_note.startswith("pos_forward ←")]
        assert note_list and ") / anim_total(4)" in note_list[0]

    def test_a_conditional_jump_notes_its_outcome(self, game_data):
        result = _bake(game_data, [[0xC1, 0x01, 0xA1, 0xE7, 0x02, 0xA9]])
        assert _note_of(result, 0xE7) == "if value > 0: 1 → taken"

    def test_a_scratch_write_stays_quiet(self, game_data):
        # stack[FF] is plumbing: its formula resurfaces spliced into the real write.
        result = _bake(game_data, [[0xC1, 0x05, 0xE5, 0xFF, 0xA9]])
        assert _note_of(result, 0xE5) == ""

    def test_a_local_slot_write_says_where_and_what_for(self, game_data):
        # The seq-14 idiom: park a value in e5[2] on frame 0, read it back frames later
        # to build pos_y. The note must follow the slot to its first read and name what
        # that computation feeds - "what is this value FOR", answered.
        result = _bake(game_data, [[0xC1, 0x05, 0xE5, 0x02,      # e5[2] = 5
                                    0x00,                        # an animation passes
                                    0xC3, 0x02, 0xE5, 0x0F,      # pos_y = e5[2]
                                    0xA9]], {0: 4})
        note = _note_of(result, 0xE5)
        assert note == "e5[2] ← 5 — used from frame 4 (pos_forward)"

    def test_a_local_slot_never_read_says_so(self, game_data):
        result = _bake(game_data, [[0xC1, 0x05, 0xE5, 0x02, 0xA9]])
        assert _note_of(result, 0xE5) == "e5[2] ← 5 — never read afterwards"


class TestCurrentValueStory:
    """The end-of-frame register line only speaks when no command note already told the
    story: a future consumer is announced, a dead value says it is leftover."""

    def test_a_future_write_is_announced_with_its_destination(self, game_data):
        from Ifrit.IfritSeq.seqpreviewpanel import current_value_story
        result = _bake(game_data, [[0xC1, 0x05, 0xA1, 0xE5, 0x0F, 0xA9]])
        assert current_value_story(result, 0) == "value = 5 — will go to pos_forward"

    def test_a_value_deciding_a_jump_says_so(self, game_data):
        from Ifrit.IfritSeq.seqpreviewpanel import current_value_story
        result = _bake(game_data, [[0xC1, 0x01, 0xA1, 0xE7, 0x02, 0xA9]])
        assert current_value_story(result, 0) == \
            "value = 1 — decides the next conditional jump"

    def test_a_dead_value_is_just_leftover(self, game_data):
        from Ifrit.IfritSeq.seqpreviewpanel import current_value_story
        result = _bake(game_data, [[0xC1, 0x05, 0xA1, 0xC1, 0x00, 0xE5, 0x0F, 0xA9]])
        assert current_value_story(result, 0) == \
            "value = 5 — leftover, nothing reads it"

    def test_a_within_frame_write_leaves_the_story_to_its_note(self, game_data):
        from Ifrit.IfritSeq.seqpreviewpanel import current_value_story
        result = _bake(game_data, [[0xC1, 0x05, 0xC5, 0x03, 0xE5, 0x0F, 0xA1, 0xA9]])
        assert current_value_story(result, 0) == ""


class TestFrameChains:
    """The detail box folds a frame into outcome chains: plumbing under an expander,
    the headline being what the value became - and a stored slot links to its reader."""

    def test_plumbing_folds_under_the_noted_write(self, game_data):
        from Ifrit.IfritSeq.seqpreviewpanel import build_frame_chains
        result = _bake(game_data, [[0xC3, 0x11, 0xC9, 0x64, 0xE5, 0xFF,
                                    0xC1, 0x00, 0xCB, 0xFF, 0xE5, 0x0F, 0xA1, 0xA9]])
        chain_list = build_frame_chains(result.frame_list[0])
        value_chain = [chain for chain in chain_list if chain.kind == "value"]
        assert len(value_chain) == 1
        assert value_chain[0].headline.startswith("pos_forward ← ")
        # The six raw ops (reads, scratch write, subtraction...) fold underneath.
        assert len(value_chain[0].op_lines) == 6

    def test_actions_stay_their_own_lines_in_order(self, game_data):
        from Ifrit.IfritSeq.seqpreviewpanel import build_frame_chains
        result = _bake(game_data, [[0xBB, 0xB5, 0x00, 0x00, 0x00, 0xA9]], {0: 2})
        chain_list = build_frame_chains(result.frame_list[0])
        assert [chain.kind for chain in chain_list][:2] == ["text", "sound"]

    def test_a_stored_slot_chain_carries_its_reader_frame(self, game_data):
        from Ifrit.IfritSeq.seqpreviewpanel import build_frame_chains
        result = _bake(game_data, [[0xC1, 0x05, 0xE5, 0x02, 0x00,
                                    0xC3, 0x02, 0xE5, 0x0F, 0xA9]], {0: 4})
        chain = build_frame_chains(result.frame_list[0])[0]
        assert chain.target_frame == 4        # double-click jumps to the read

    def test_a_register_left_pending_closes_on_the_story(self, game_data):
        from Ifrit.IfritSeq.seqpreviewpanel import (build_frame_chains,
                                                    current_value_story)
        result = _bake(game_data, [[0xC1, 0x05, 0xA1, 0xE5, 0x0F, 0xA9]])
        chain = build_frame_chains(result.frame_list[0], None,
                                   current_value_story(result, 0))[-1]
        assert chain.headline == "value = 5 — will go to pos_forward"
        assert chain.op_lines             # the C1 05 folds under it


class TestValueCurves:
    """The curve band plots the variables that change, world-oriented."""

    def test_only_varying_variables_get_a_series(self, game_data):
        from Ifrit.IfritSeq.seqtimelinewidget import build_value_series
        result = _bake(game_data, [[0xA1, 0xA1, 0xA9]])
        assert build_value_series(result) == {}

    def test_an_approach_plots_toward_the_target(self, game_data):
        # E5 0F is the entity-local FORWARD offset; the engine convention is negative =
        # toward the target, and the plotted series flips it so an approach rises.
        from Ifrit.IfritSeq.seqtimelinewidget import build_value_series
        result = _bake(game_data, [[0xA1, 0xC0, 0x60, 0xF6, 0xE5, 0x0F, 0xA1, 0xA9]])
        series = build_value_series(result)
        assert "→ target (+)" in series
        _colour, value_list = series["→ target (+)"]
        assert max(value_list) == 2464        # raw -2464 = 2464 units toward the target

    def test_a_vertical_write_plots_on_the_up_series(self, game_data):
        # E5 0E is the entity-local VERTICAL offset (PSX down-positive, flipped to up+).
        from Ifrit.IfritSeq.seqtimelinewidget import build_value_series
        result = _bake(game_data, [[0xA1, 0xC0, 0x60, 0xF6, 0xE5, 0x0E, 0xA1, 0xA9]])
        series = build_value_series(result)
        assert "up (+)" in series
        assert max(series["up (+)"][1]) == 2464

    def test_the_9e_transport_gets_its_own_series(self, game_data):
        from Ifrit.IfritSeq.seqtimelinewidget import build_value_series
        result = _bake(game_data, [[0x9E, 0x00, 0x04, 0xA1, 0xA1, 0xA1, 0xA1, 0xA9]])
        series = build_value_series(result)
        assert "move → target (+)" in series
        assert max(series["move → target (+)"][1]) == 2048   # approaches, rising


class TestWatchRows:
    """The Variables watch panel: only what the bake touches, flags with bit names,
    changes flagged against the previous frame."""

    def test_only_touched_variables_get_a_row(self, game_data):
        from Ifrit.IfritSeq.seqpreviewpanel import relevant_watch_variables
        result = _bake(game_data, [[0xC1, 0x05, 0xE5, 0x02, 0xA1, 0xA9]])
        name_list = [name for name, _extract in relevant_watch_variables(result)]
        assert "e5[2]" in name_list
        assert "e5[3]" not in name_list
        assert "save[0]" not in name_list

    def test_the_flags_render_with_their_bit_names(self, game_data):
        from Ifrit.IfritSeq.seqpreviewpanel import (relevant_watch_variables,
                                                    build_watch_rows)
        result = _bake(game_data, [[0xC3, 0x08, 0xD9, 0x20, 0xE5, 0x08, 0xA1, 0xA9]])
        row_list = build_watch_rows(result, 0, relevant_watch_variables(result))
        flag_row = [row for row in row_list if row[0] == "state_flags"][0]
        assert "effect_fire" in flag_row[1]

    def test_a_change_is_flagged_on_its_frame_only(self, game_data):
        from Ifrit.IfritSeq.seqpreviewpanel import (relevant_watch_variables,
                                                    build_watch_rows)
        result = _bake(game_data, [[0xA1, 0xC1, 0x05, 0xE5, 0x02, 0xA1, 0xA9]])
        variables = relevant_watch_variables(result)

        def changed_on(frame_index):
            row = [row for row in build_watch_rows(result, frame_index, variables)
                   if row[0] == "e5[2]"][0]
            return row[2]

        assert changed_on(1) is True     # written on this frame
        assert changed_on(2) is False    # still 5, nothing new


class TestTimelineValueAndSoundLanes:

    def test_engine_writes_get_a_value_lane_bar_with_the_formula(self, game_data):
        from Ifrit.IfritSeq.seqtimelinewidget import build_timeline_model, VALUE_KIND
        result = _bake(game_data, [[0xC1, 0x05, 0xC5, 0x03, 0xE5, 0x0F, 0xA1, 0xA9]])
        model = build_timeline_model(result)
        value_lane = [bar_list for kind, _l, bar_list in model.lane_list
                      if kind == VALUE_KIND]
        assert value_lane and value_lane[0][0].description == "pos_forward ← 5 + 3 = 8"

    def test_a_per_frame_arc_write_folds_into_one_value_bar(self, game_data):
        # pos_y written on every pass of a yield loop = ONE continuous bar, the shape
        # of the arc itself.
        from Ifrit.IfritSeq.seqtimelinewidget import build_timeline_model, VALUE_KIND
        result = _bake(game_data, [[0xC3, 0x09, 0xE5, 0x0F, 0xA1, 0xE6, 0xFB, 0xA9]])
        model = build_timeline_model(result)
        value_lane = [bar_list for kind, _l, bar_list in model.lane_list
                      if kind == VALUE_KIND]
        assert value_lane and len(value_lane[0]) == 1
        assert value_lane[0][0].nb_frame > 1

    def test_a_sound_bar_stretches_to_its_audio_duration(self, game_data):
        from Ifrit.IfritSeq.seqtimelinewidget import build_timeline_model
        from FF8GameData.dat.sequencebake import EVENT_SOUND
        result = _bake(game_data, [[0xB5, 0x00, 0x00, 0x00, 0xA9]], {0: 30})
        model = build_timeline_model(result,
                                     sound_duration_of=lambda op, params: 12)
        sound_lane = [bar_list for kind, _l, bar_list in model.lane_list
                      if kind == EVENT_SOUND]
        assert sound_lane and sound_lane[0][0].nb_frame == 12
