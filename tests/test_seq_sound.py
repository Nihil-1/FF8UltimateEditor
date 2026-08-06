"""Tests for the sequence sound resolution (FF8GameData/dat/sequencesound.py).

The preview plays a sound command through the same lookup the engine does (verified in
FF8_EN.exe linkedToSoundAnimSeq @0x505AB0 / BdPlayActorSE @0x5015A0): B5/B6/97 index the
actor's 7-slot row of BattleActorSoundTable, B8/98 carry the global audio id directly.
What is pinned here is that mapping - the actor row must be indexed by SLOT (an empty
slot between used ones must not shift the ids after it), the file name must land on the
right table row, and the bake must hand the parameters through for it.
"""
import pathlib

import pytest

from FF8GameData.gamedata import GameData
from FF8GameData.dat.sequencebake import bake_sequence
from FF8GameData.dat.sequencesound import (SequenceSoundResolver, actor_key_for_file,
                                           SOUND_OP_CODES)

PROJECT_ROOT = pathlib.Path(__file__).parent.parent


@pytest.fixture(scope="session")
def game_data():
    data = GameData(str(PROJECT_ROOT / "FF8GameData"))
    data.load_all()
    return data


class TestActorKey:

    def test_a_monster_file_maps_to_16_plus_its_number(self):
        assert actor_key_for_file("c0m001.dat") == 17
        assert actor_key_for_file(r"C:\somewhere\battle\c0m123.dat") == 139

    def test_a_character_file_maps_to_its_character_id(self):
        assert actor_key_for_file("d0w003.dat") == 0    # Squall weapon
        assert actor_key_for_file("d4c.dat") == 4       # Rinoa body
        assert actor_key_for_file("daw001.dat") == 10   # Ward

    def test_anything_else_has_no_row(self):
        assert actor_key_for_file("mag184.dat") is None
        assert actor_key_for_file("") is None


class TestResolve:

    def test_an_actor_sound_reads_the_actors_slot(self, game_data):
        # GIM52A (c0m001 -> table row 17): slots [217, 218, 1691, ...] from stru_B8A418.
        resolver = SequenceSoundResolver(game_data, "c0m001.dat")
        assert resolver.resolve(0xB5, b"\x00\x00") == 217
        assert resolver.resolve(0xB6, b"\x01\x00") == 218
        assert resolver.resolve(0x97, b"\x02\x00") == 1691

    def test_an_empty_slot_resolves_to_nothing(self, game_data):
        resolver = SequenceSoundResolver(game_data, "c0m001.dat")
        assert resolver.resolve(0xB5, b"\x06\x00") is None

    def test_an_id_past_the_seven_slots_is_ignored_like_the_engine_does(self, game_data):
        resolver = SequenceSoundResolver(game_data, "c0m001.dat")
        assert resolver.resolve(0xB5, b"\x07\x00") is None

    def test_a_global_sound_is_the_byte_itself(self, game_data):
        resolver = SequenceSoundResolver(game_data, "c0m001.dat")
        assert resolver.resolve(0xB8, b"\x1f\x00") == 31
        assert resolver.resolve(0x98, b"\x2a\x00") == 42

    def test_a_non_sound_op_resolves_to_nothing(self, game_data):
        resolver = SequenceSoundResolver(game_data, "c0m001.dat")
        assert resolver.resolve(0xA1, b"") is None
        assert resolver.resolve(0xB5, b"") is None

    def test_a_file_with_no_table_row_still_resolves_global_sounds(self, game_data):
        resolver = SequenceSoundResolver(game_data, "mag184.dat")
        assert resolver.resolve(0xB5, b"\x00\x00") is None
        assert resolver.resolve(0xB8, b"\x1f\x00") == 31


class TestBakeCarriesTheParameters:

    def test_the_executed_command_keeps_its_parameter_bytes(self, game_data):
        # B5 01 00 then A9: the frame-0 command must still hold the bytes the sound
        # lookup needs - the description alone cannot give the id back.
        result = bake_sequence(game_data, {1: bytes([0xB5, 0x01, 0x00, 0xA9])}, 1, {})
        sound_command = [command for command in result.frame_list[0].command_list
                        if command.op_code in SOUND_OP_CODES]
        assert len(sound_command) == 1
        assert sound_command[0].parameters == b"\x01\x00"
