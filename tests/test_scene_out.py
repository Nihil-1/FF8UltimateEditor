"""Tests for the scene.out encounter reader (FF8GameData/sceneout.py).

The preview uses this to place a fight for real - "where does THIS monster stand, and
how far is the party" - so what is pinned is the record layout against the actual game
file: the 128-byte stride, the MSB-first slot flags, the enemy-id offset, and the
coordinate triplets. A silently mis-parsed record would put the target at a plausible
but wrong distance, which is worse than no placement at all.
"""
import pathlib

import pytest

from FF8GameData.sceneout import (read_encounters, encounters_with_monster,
                                  ENCOUNTER_SIZE, NB_SLOT, ENEMY_ID_BASE)

PROJECT_ROOT = pathlib.Path(__file__).parent.parent
SCENE_OUT_PATH = PROJECT_ROOT / "extracted_files" / "battle" / "scene.out"


@pytest.fixture(scope="module")
def encounter_list():
    if not SCENE_OUT_PATH.is_file():
        pytest.skip("extracted scene.out not available")
    with open(SCENE_OUT_PATH, "rb") as scene_file:
        return read_encounters(scene_file.read())


class TestTheRecordLayout:

    def test_the_file_is_1024_records_of_128_bytes(self, encounter_list):
        assert len(encounter_list) == 1024
        assert SCENE_OUT_PATH.stat().st_size == 1024 * ENCOUNTER_SIZE

    def test_every_encounter_has_eight_slots(self, encounter_list):
        assert all(len(encounter.slots) == NB_SLOT for encounter in encounter_list)

    def test_an_enabled_slot_names_a_real_monster_file(self, encounter_list):
        # Enemy id = c0m file number + 0x10, and the game ships c0m000..c0m199.
        for encounter in encounter_list:
            for slot in encounter.enabled_slots():
                assert slot.enemy_id >= ENEMY_ID_BASE
                assert 0 <= slot.com_file_id < 200

    def test_enabled_monsters_stand_in_front_of_the_party(self, encounter_list):
        # The convention the preview's placement rests on: monsters are laid out at
        # negative Z, the party faces them from the other side. If this ever failed,
        # the stand-in and the approach direction would both be mirrored.
        z_list = [slot.position[2] for encounter in encounter_list
                  for slot in encounter.enabled_slots()]
        assert z_list and all(z <= 0 for z in z_list)

    def test_the_slot_flag_bits_are_msb_first(self, encounter_list):
        # Bit 0x80 is slot 0. A record with exactly one enabled monster must therefore
        # have its high bit set - the classic place to get the order backwards.
        single = [encounter for encounter in encounter_list
                  if len(encounter.enabled_slots()) == 1
                  and encounter.enabled_slots()[0].slot_index == 0]
        assert single
        assert single[0].raw[0x07] & 0x80


class TestFindingAMonster:

    def test_a_known_monster_is_found_with_its_coordinates(self, encounter_list):
        # GIM52A = c0m001, enemy id 0x11. Encounter 111 fields two of them (verified
        # against the file), the first at (-1100, 0, -2900).
        match_list = encounters_with_monster(encounter_list, 1)
        assert match_list
        by_index = {encounter.index: slot for encounter, slot in match_list}
        assert 111 in by_index
        encounter_111 = [e for e, _s in match_list if e.index == 111][0]
        assert [slot.position for slot in encounter_111.enabled_slots()][0] == \
            (-1100, 0, -2900)

    def test_only_enabled_slots_are_returned(self, encounter_list):
        for _encounter, slot in encounters_with_monster(encounter_list, 1):
            assert slot.enabled

    def test_an_id_past_the_table_finds_nothing(self, encounter_list):
        assert encounters_with_monster(encounter_list, 250) == []
