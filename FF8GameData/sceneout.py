"""scene.out: the game's encounter list, read for real battle placement.

The file is a headerless array of 1024 records of 128 bytes (see the wiki's
BattleStructure page): stage id, flags, the two intro-camera bytes, four per-slot flag
bitmasks (MSB = slot 0), 8 slots x 3 int16 coordinates, 8 enemy id bytes (c0m file
number + 0x10) and 8 levels. Only what the sequence preview needs is modeled - enough
to answer "where does THIS monster stand in a real fight" - but the whole record is
kept raw so nothing is lost for a future editor.

No Qt: pure parsing, testable headless, usable by the CLI.
"""

ENCOUNTER_SIZE = 128
NB_ENCOUNTER = 1024
NB_SLOT = 8
ENEMY_ID_BASE = 0x10   # enemy id byte = c0m file number + 0x10


class EncounterSlot:
    """One of the 8 monster slots of an encounter."""

    __slots__ = ("slot_index", "enemy_id", "position", "level",
                 "enabled", "visible", "loaded", "targetable")

    def __init__(self, slot_index, enemy_id, position, level, enabled, visible,
                 loaded, targetable):
        self.slot_index = slot_index
        self.enemy_id = enemy_id        # raw byte (c0m number + 0x10)
        self.position = position        # (x, y, z) signed, battle world units
        self.level = level
        self.enabled = enabled
        self.visible = visible
        self.loaded = loaded
        self.targetable = targetable

    @property
    def com_file_id(self):
        """The c0mNNN.dat number this slot loads."""
        return self.enemy_id - ENEMY_ID_BASE


class Encounter:
    """One 128-byte scene.out record."""

    __slots__ = ("index", "stage_id", "flags", "camera_main", "camera_secondary",
                 "slots", "raw")

    def __init__(self, index, record):
        self.index = index
        self.raw = bytes(record)
        self.stage_id = record[0x00]
        self.flags = record[0x01]
        self.camera_main = record[0x02]
        self.camera_secondary = record[0x03]
        not_visible, not_loaded, not_targetable, enabled = record[0x04:0x08]
        self.slots = []
        for slot_index in range(NB_SLOT):
            bit = 0x80 >> slot_index   # MSB = slot 0, per the wiki
            base = 0x08 + slot_index * 6
            position = tuple(int.from_bytes(record[base + axis * 2:base + axis * 2 + 2],
                                            "little", signed=True) for axis in range(3))
            self.slots.append(EncounterSlot(
                slot_index, record[0x38 + slot_index], position,
                record[0x78 + slot_index],
                enabled=bool(enabled & bit), visible=not (not_visible & bit),
                loaded=not (not_loaded & bit), targetable=not (not_targetable & bit)))

    def enabled_slots(self):
        return [slot for slot in self.slots if slot.enabled]


def read_encounters(data) -> list:
    """Every encounter of a scene.out byte string, in file order."""
    nb_encounter = len(data) // ENCOUNTER_SIZE
    return [Encounter(index, data[index * ENCOUNTER_SIZE:(index + 1) * ENCOUNTER_SIZE])
            for index in range(nb_encounter)]


def encounters_with_monster(encounter_list, com_file_id) -> list:
    """[(encounter, slot)] for every ENABLED appearance of c0m file `com_file_id`."""
    enemy_id = com_file_id + ENEMY_ID_BASE
    found = []
    for encounter in encounter_list:
        for slot in encounter.slots:
            if slot.enabled and slot.enemy_id == enemy_id:
                found.append((encounter, slot))
    return found
