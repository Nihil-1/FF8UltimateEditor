"""The Ctrl+F search bar shared by every list-based tool (SmallWidget/listsearchbar.py).

The bar hides rows instead of removing them: the tools map their current row straight onto
their own entry list, so a filter must never shift an index."""
import sys

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QApplication, QListWidget

from SmallWidget.listsearchbar import ListSearchBar

ITEMS = ["Potion", "Hi-Potion", "Phoenix Down", "Remedy", "Elixir"]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def list_and_bar(qapp):
    list_widget = QListWidget()
    list_widget.addItems(ITEMS)
    bar = ListSearchBar(list_widget)
    list_widget.setCurrentRow(0)
    return list_widget, bar


def visible_texts(list_widget):
    return [list_widget.item(row).text()
            for row in range(list_widget.count()) if not list_widget.item(row).isHidden()]


class TestFiltering:
    def test_matching_is_case_insensitive_and_by_substring(self, list_and_bar):
        list_widget, bar = list_and_bar
        bar.search_field.setText("POTION")
        assert visible_texts(list_widget) == ["Potion", "Hi-Potion"]

    def test_rows_are_hidden_not_removed(self, list_and_bar):
        list_widget, bar = list_and_bar
        bar.search_field.setText("elixir")
        assert list_widget.count() == len(ITEMS)
        assert list_widget.item(4).text() == "Elixir"  # index 4 still means Elixir

    def test_result_label_counts_matches(self, list_and_bar):
        list_widget, bar = list_and_bar
        bar.search_field.setText("potion")
        assert bar.result_label.text() == "2/5"
        bar.search_field.setText("zzz")
        assert bar.result_label.text() == "0/5"

    def test_clearing_shows_everything_again(self, list_and_bar):
        list_widget, bar = list_and_bar
        bar.search_field.setText("potion")
        bar.clear_search()
        assert visible_texts(list_widget) == ITEMS
        assert bar.result_label.text() == ""

    def test_index_search_is_opt_in(self, qapp):
        list_widget = QListWidget()
        list_widget.addItems(ITEMS)
        bar = ListSearchBar(list_widget, match_index=True)
        bar.search_field.setText("3")
        assert visible_texts(list_widget) == ["Remedy"]  # row 3, no digit in any name

        plain_list = QListWidget()
        plain_list.addItems(ITEMS)
        plain_bar = ListSearchBar(plain_list)
        plain_bar.search_field.setText("3")
        assert visible_texts(plain_list) == []


class TestSelection:
    def test_selection_follows_the_filter(self, list_and_bar):
        list_widget, bar = list_and_bar
        bar.search_field.setText("elixir")  # hides row 0, which was selected
        assert list_widget.currentRow() == 4

    def test_selection_is_kept_when_nothing_matches(self, list_and_bar):
        list_widget, bar = list_and_bar
        list_widget.setCurrentRow(2)
        bar.search_field.setText("zzz")
        assert list_widget.currentRow() == 2  # the tool stays on the entry it was editing

    def test_visible_selection_is_left_alone(self, list_and_bar):
        list_widget, bar = list_and_bar
        list_widget.setCurrentRow(1)
        bar.search_field.setText("potion")  # row 1 still matches
        assert list_widget.currentRow() == 1

    def test_multi_selection_lists_never_lose_their_selection(self, qapp):
        list_widget = QListWidget()
        list_widget.addItems(ITEMS)
        list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        bar = ListSearchBar(list_widget)
        list_widget.item(0).setSelected(True)
        list_widget.item(1).setSelected(True)
        bar.search_field.setText("elixir")
        assert [item.text() for item in list_widget.selectedItems()] == ["Potion", "Hi-Potion"]

    def test_select_first_match_after_a_refill(self, list_and_bar):
        list_widget, bar = list_and_bar
        bar.search_field.setText("phoenix")
        list_widget.clear()  # what a tool does when it imports a file
        list_widget.addItems(ITEMS)
        assert visible_texts(list_widget) == ["Phoenix Down"]  # the filter is re-applied
        bar.select_first_match()
        assert list_widget.currentRow() == 2

    def test_select_first_match_is_row_zero_without_a_search(self, list_and_bar):
        list_widget, bar = list_and_bar
        list_widget.setCurrentRow(3)
        bar.select_first_match()
        assert list_widget.currentRow() == 0


class TestShortcuts:
    def test_ctrl_f_focuses_and_selects_the_search(self, list_and_bar):
        _, bar = list_and_bar
        assert bar.search_shortcut.key() == QKeySequence(QKeySequence.StandardKey.Find)
        bar.search_field.setText("potion")
        bar.focus_search_field()
        assert bar.search_field.selectedText() == "potion"  # typing replaces the old search

    def test_escape_is_bound_to_the_field_only(self, list_and_bar):
        _, bar = list_and_bar
        assert bar.clear_shortcut.key() == QKeySequence(Qt.Key.Key_Escape)
        assert bar.clear_shortcut.context() == Qt.ShortcutContext.WidgetShortcut

    def test_enter_goes_to_the_first_match(self, list_and_bar):
        list_widget, bar = list_and_bar
        list_widget.setCurrentRow(-1)
        bar.search_field.setText("remedy")
        bar.search_field.returnPressed.emit()
        assert list_widget.currentRow() == 3


class TestWrap:
    def test_wrap_stacks_the_bar_above_the_list(self, qapp):
        list_widget = QListWidget()
        list_widget.setFixedWidth(180)
        bar = ListSearchBar.wrap(list_widget)
        layout = bar.column.layout()
        assert [layout.itemAt(i).widget() for i in range(layout.count())] == [bar, list_widget]
        assert bar.column.width() == 180  # a fixed width set on the list drives the whole column
