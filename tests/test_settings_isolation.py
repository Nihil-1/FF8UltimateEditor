"""The test run must not read or write the machine's own GUI preferences.

The tools remember their state in QSettings - Ifrit's "show skeleton" checkbox, last folders,
window geometry - which on Windows is the real registry of whoever runs the tests. That cut both
ways: a test asserting a default failed on a machine where the developer had once ticked the box
in the real application (test_add_bone_shortcut_adds_child_of_selected did exactly that, and only
for them), and the tests wrote their own values back into the developer's settings.

conftest.py reroutes QSettings to a throwaway ini for the whole session. These tests check the
reroute is in place, because when it silently stops working the symptom is a test that fails on
one machine and passes on every other.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings


def test_settings_go_to_a_file_and_not_the_registry():
    """The constructor the tools use - QSettings(organisation, application) - is the one that has
    to be rerouted; setDefaultFormat() does NOT reach it, which is the trap this guards."""
    settings = QSettings("FF8UltimateEditor", "FF8UltimateEditor")
    assert settings.format() == QSettings.Format.IniFormat
    assert "HKEY" not in settings.fileName().upper()
    assert settings.fileName().endswith(".ini")


def test_an_unwritten_key_comes_back_as_the_callers_default():
    """Nothing the developer once clicked in the real application may leak in.

    The namespace is this test's own, and deliberately not the application's: the isolation is per
    SESSION, not per test, so by the time this runs other tests have legitimately written real
    settings (toggling Ifrit's skeleton checkbox saves ifrit/3d/show_skeleton) into the same
    throwaway file. Reading one of those back would be testing the rest of the suite, not the
    reroute. That the reroute is in place at all is what the test above pins down.
    """
    settings = QSettings("FF8UltimateEditor", "settings-isolation-check")
    assert settings.value("never/written/by/anything", False, type=bool) is False
    assert settings.value("never/written/by/anything", 42, type=int) == 42


def test_a_value_written_by_a_test_stays_inside_the_run():
    settings = QSettings("FF8UltimateEditor", "FF8UltimateEditor")
    settings.setValue("tests/scratch_value", 1234)
    settings.sync()
    assert os.path.exists(settings.fileName())
    # ...in the throwaway folder, not anywhere the user's own settings live
    assert "ff8ue-test-settings-" in settings.fileName().replace("\\", "/")
    settings.remove("tests/scratch_value")
