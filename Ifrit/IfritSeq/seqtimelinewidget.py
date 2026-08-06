"""A baked sequence as a graphical timeline: lanes over a frame axis, not a table.

The HTML timeline (sequencetimeline.format_timeline_html) answers "what happens, in
order"; this widget answers "WHEN" at a glance - a frame ruler, one lane showing which
animation the model plays over time, and one lane per kind of event (sound, effect, ...)
with a marker on the frame it lands on. It is also the scrub bar of the 3D preview: the
playhead tracks playback and clicking/dragging seeks it (frame_selected).

What is drawn comes from build_timeline_model(), a pure fold of a BakeResult into blocks
and bars - kept out of the widget so the layout (which frames merge into one bar, which
lanes exist) can be tested without a screen, the same reason the bake itself holds no Qt.
The folding rule matches the HTML timeline's: a command that runs on N consecutive frames
(a per-frame poll) is ONE bar spanning them, not N markers burying the frames that differ.
"""
from PyQt6.QtCore import Qt, QSize, QPointF, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QWidget, QToolTip

from FF8GameData.dat.sequencebake import (STOP_HANG, STOP_ERROR,
                                          EVENT_SOUND, EVENT_EFFECT, EVENT_TEXT,
                                          EVENT_TARGET, EVENT_MODEL, EVENT_FLOW,
                                          EVENT_MOVE)
from FF8GameData.dat.sequencetimeline import COLOUR_BY_KIND
from FF8GameData.monsterdata import PositionType

# The lanes, in a fixed top-to-bottom order so two sequences read the same way. A lane
# only appears when the sequence has at least one event of its kind (most have 2-3 kinds;
# eight mostly-empty lanes would just push the ruler away from the animation lane).
# EVENT_STATE is not a lane at all - state work is bookkeeping, not something that lands.
# Not a bake event kind: the timeline's own lane for the value flow - a bar wherever an
# E5 write lands current_value in an engine variable, its tooltip carrying the formula
# ("pos_y ← (0 - (speed - 100)) * anim_frame / anim_total"). A per-frame arc write folds
# into one continuous bar, which is exactly the shape of the thing.
VALUE_KIND = "value"

LANE_ORDER = [(EVENT_SOUND, "sound"), (EVENT_EFFECT, "effect"), (EVENT_TEXT, "text"),
              (EVENT_TARGET, "target"), (EVENT_MOVE, "move"), (VALUE_KIND, "value"),
              (EVENT_MODEL, "model"), (EVENT_FLOW, "flow")]

COLOUR_BY_EXTRA_KIND = {VALUE_KIND: "#7f9ec0"}

# ---------------------------------------------------------------- value curves
# The motion-shaping variables plotted as curves under the lanes: a jump literally draws
# its arc. Position axes are flipped to WORLD orientation (raw PSX Y-down would draw the
# jump as a dip), which the "(up+)" label flags; each series keeps its raw magnitude.
_WORLD_SIGN = tuple(1 if axis_scale > 0 else -1
                    for axis_scale in PositionType.AXIS_SCALE)

# frame.position is the ENTITY-LOCAL offset (lateral, vertical, forward): vertical is
# flipped to up-positive, forward to toward-the-target-positive (the engine convention
# is down/backward-positive), so both curves rise when the modder's intuition says so.
CURVE_SERIES = [
    ("side", "#c08a4f", lambda frame: frame.position[0]),
    ("up (+)", "#7fa650", lambda frame: -frame.position[1]),
    ("→ target (+)", "#4f8fc0", lambda frame: -frame.position[2]),
    ("rot_y", "#9a7fb0", lambda frame: frame.rotation_y),
    ("move_x", "#b06a8f", lambda frame: frame.move_position[0] * _WORLD_SIGN[0]),
    ("move → target (+)", "#4f9f9f", lambda frame: -frame.move_position[2]),
]


def build_value_series(result) -> dict:
    """{name: (colour, [value per frame])} for every plotted variable that actually
    CHANGES during the run - a flat line is dropped, so most sequences plot nothing and
    pay nothing."""
    series = {}
    for name, colour, extract in CURVE_SERIES:
        value_list = [extract(frame) for frame in result.frame_list]
        if any(value != value_list[0] for value in value_list):
            series[name] = (colour, value_list)
    return series

# How long an effect's symbolic presence lasts on screen (bursts, overlay notice, and
# the bar drawn here) - the real duration lives in the exe's effect code.
EFFECT_HOLD_FRAMES = 8


def _fire_signal_command(frame):
    """The E5 08 write raising the 0x20 "fire" bit for the parallel effect code, or
    None. Duplicated tiny (rather than imported from the panel) to keep this module
    Qt-widget only on the widget side and free of the panel's Julia/multimedia imports."""
    if not frame.current_value & 0x20:
        return None
    last = None
    for command in frame.command_list:
        if (not command.is_background and command.op_code == 0xE5
                and command.parameters[:1] == b"\x08"):
            last = command
    return last


class AnimationBlock:
    """A stretch of frames playing the same animation id."""

    __slots__ = ("anim_id", "first_frame", "last_frame")

    def __init__(self, anim_id, first_frame):
        self.anim_id = anim_id
        self.first_frame = first_frame
        self.last_frame = first_frame

    @property
    def nb_frame(self):
        return self.last_frame - self.first_frame + 1


class EventBar:
    """One command occurrence on the timeline: a single frame, or the span of consecutive
    frames the SAME command (same address) ran on - a per-frame poll folded into one bar."""

    __slots__ = ("kind", "op_code", "description", "first_frame", "last_frame",
                 "parameters")

    def __init__(self, kind, op_code, description, first_frame, parameters=b""):
        self.kind = kind
        self.op_code = op_code
        self.description = description
        self.first_frame = first_frame
        self.last_frame = first_frame
        self.parameters = bytes(parameters)   # for duration lookups (a sound's length)

    @property
    def nb_frame(self):
        return self.last_frame - self.first_frame + 1


class TimelineModel:
    """Everything the widget draws, folded once per bake (not per paint)."""

    def __init__(self):
        self.nb_frame = 0
        self.animation_blocks = []       # [AnimationBlock] in frame order
        self.waiting_frame_set = set()   # frames spent waiting for the animation
        self.lane_list = []              # [(kind, label, [EventBar])], only non-empty kinds
        self.loop_from = None
        self.stop_reason = None
        self.stop_detail = ""


def build_timeline_model(result, sound_duration_of=None) -> TimelineModel:
    """Fold a BakeResult into the blocks and bars the widget draws.

    Background (9A) commands are left out, exactly as the HTML timeline leaves them out:
    they run on every single frame, so drawing them would paint a solid stripe that says
    nothing. EVENT_STATE commands are bookkeeping and get no marker - except E5 writes
    carrying a value_note, which get the value lane (formula in the tooltip).

    `sound_duration_of(op_code, parameters) -> nb frames or None` (from a caller holding
    the audio archive) stretches each sound bar to how long the sound actually plays.
    """
    model = TimelineModel()
    model.nb_frame = len(result.frame_list)
    model.loop_from = result.loop_from
    model.stop_reason = result.stop_reason
    model.stop_detail = result.stop_detail

    bar_by_kind = {kind: [] for kind, _label in LANE_ORDER}
    open_bar = {}   # (kind, seq_id, address, op_code) -> EventBar still being extended
    block = None
    for frame in result.frame_list:
        if frame.is_waiting_animation:
            model.waiting_frame_set.add(frame.index)
        if frame.anim_id is not None:
            if block is None or block.anim_id != frame.anim_id:
                block = AnimationBlock(frame.anim_id, frame.index)
                model.animation_blocks.append(block)
            else:
                block.last_frame = frame.index
        fire_command = _fire_signal_command(frame)
        for command in frame.command_list:
            if command.is_background:
                continue
            kind = command.kind
            if kind not in bar_by_kind:
                if command is fire_command:
                    # The flag-0x20 "fire" signal to the parallel effect code: an
                    # effect leaves the entity here without any effect op, so it
                    # belongs on the effect lane (the missile case - c0m001 seq 12).
                    kind = EVENT_EFFECT
                    command_description = "effect fire signal (flag 0x20)"
                elif command.op_code == 0xE5 and command.value_note:
                    # An engine variable took a computed value: the value lane, with
                    # the formula as the bar's story.
                    kind = VALUE_KIND
                    command_description = command.value_note
                else:
                    continue
            else:
                command_description = command.description
            key = (kind, command.seq_id, command.address, command.op_code)
            bar = open_bar.get(key)
            if bar is not None and bar.last_frame == frame.index - 1:
                bar.last_frame = frame.index   # the same poll, one frame later: extend
            else:
                bar = EventBar(kind, command.op_code, command_description, frame.index,
                               command.parameters)
                bar_by_kind[kind].append(bar)
                open_bar[key] = bar

    # A bar so far marks when a command RAN; for what the player SEES, text and effects
    # have a duration. A text banner stays up until the next text replaces it (or the
    # run ends); an effect holds the same few frames its burst and overlay notice do.
    last_frame_index = max(model.nb_frame - 1, 0)
    text_bar_list = sorted(bar_by_kind[EVENT_TEXT], key=lambda bar: bar.first_frame)
    for bar, next_bar in zip(text_bar_list, text_bar_list[1:] + [None]):
        bar.last_frame = (next_bar.first_frame - 1 if next_bar is not None
                          else last_frame_index)
    effect_bar_list = sorted(bar_by_kind[EVENT_EFFECT], key=lambda bar: bar.first_frame)
    for bar, next_bar in zip(effect_bar_list, effect_bar_list[1:] + [None]):
        limit = (next_bar.first_frame - 1 if next_bar is not None else last_frame_index)
        limit = max(limit, bar.first_frame)   # two effects on one frame: never negative
        bar.last_frame = min(max(bar.last_frame,
                                 bar.first_frame + EFFECT_HOLD_FRAMES - 1), limit)
    bar_by_kind[EVENT_TEXT] = text_bar_list
    bar_by_kind[EVENT_EFFECT] = effect_bar_list
    # A sound plays for its audio length, not for the frame its command ran on: stretch
    # each bar to its real duration when the caller can tell it (archive loaded).
    if sound_duration_of is not None:
        sound_bar_list = sorted(bar_by_kind[EVENT_SOUND],
                                key=lambda bar: bar.first_frame)
        for bar, next_bar in zip(sound_bar_list, sound_bar_list[1:] + [None]):
            duration = sound_duration_of(bar.op_code, bar.parameters)
            if not duration:
                continue
            limit = (next_bar.first_frame - 1 if next_bar is not None
                     else last_frame_index)
            limit = max(limit, bar.first_frame)
            bar.last_frame = min(max(bar.last_frame,
                                     bar.first_frame + duration - 1), limit)
        bar_by_kind[EVENT_SOUND] = sound_bar_list

    model.lane_list = [(kind, label, bar_by_kind[kind])
                       for kind, label in LANE_ORDER if bar_by_kind[kind]]
    return model


class SequenceTimelineWidget(QWidget):
    """The painted timeline. set_result() gives it a bake; frame_selected reports seeks."""

    frame_selected = pyqtSignal(int)

    RULER_H = 20
    ANIM_H = 22
    LANE_H = 15
    LABEL_W = 52          # left gutter for the lane names, before frame 0
    PAD_RIGHT = 26        # room for the stop marker past the last frame
    PAD_BOTTOM = 4
    MIN_PX_PER_FRAME = 2.0
    MAX_PX_PER_FRAME = 30.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._result = None
        self._model = None
        self._series = {}
        self._current_frame = 0
        self._px_per_frame = 7.0
        self.setMouseTracking(True)   # hover tooltips without a click

    # ------------------------------------------------------------------ data
    CURVE_H = 46   # the curve band under the lanes, when any variable varies

    def set_result(self, result, sound_duration_of=None):
        """Show this bake (or clear with None). Keeps the playhead where it was when the
        new bake still has that frame - a re-bake after a keystroke should not jump."""
        self._result = result
        self._model = (build_timeline_model(result, sound_duration_of)
                       if result is not None else None)
        self._series = build_value_series(result) if result is not None else {}
        nb = self._model.nb_frame if self._model else 0
        self._current_frame = min(self._current_frame, max(nb - 1, 0))
        self.updateGeometry()
        self.update()

    def set_current_frame(self, frame):
        nb = self._model.nb_frame if self._model else 0
        self._current_frame = max(0, min(frame, max(nb - 1, 0)))
        self.update()

    def current_frame(self) -> int:
        return self._current_frame

    def playhead_x(self) -> int:
        """The playhead's x in widget coordinates, for the panel to keep it scrolled
        into view during playback."""
        return int(self._x(self._current_frame + 0.5))

    # ------------------------------------------------------------------ layout
    def _x(self, frame) -> float:
        return self.LABEL_W + frame * self._px_per_frame

    def _frame_at(self, x) -> int:
        nb = self._model.nb_frame if self._model else 0
        if nb == 0:
            return 0
        return max(0, min(int((x - self.LABEL_W) / self._px_per_frame), nb - 1))

    def _content_height(self) -> int:
        nb_lane = len(self._model.lane_list) if self._model else 0
        curve_height = self.CURVE_H if getattr(self, "_series", None) else 0
        return (self.RULER_H + self.ANIM_H + nb_lane * self.LANE_H + curve_height
                + self.PAD_BOTTOM)

    def minimumSizeHint(self) -> QSize:
        nb = self._model.nb_frame if self._model else 0
        return QSize(self.LABEL_W + int(nb * self._px_per_frame) + self.PAD_RIGHT,
                     self._content_height())

    def sizeHint(self) -> QSize:
        return self.minimumSizeHint()

    # ------------------------------------------------------------------ painting
    @staticmethod
    def _animation_colour(anim_id) -> QColor:
        """A stable colour per animation id, mid-tone so its label stays readable on the
        light and the dark palette alike (the app does not fix one)."""
        return QColor.fromHsv((37 + anim_id * 53) % 360, 110, 185, 175)

    def _tick_step(self) -> int:
        """Frame-number step of the ruler: the smallest 1/2/5 * 10^k giving labels room."""
        min_px = 36
        scale = 1
        while True:
            for base in (1, 2, 5):
                step = base * scale
                if step * self._px_per_frame >= min_px:
                    return step
            scale *= 10

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self.palette()
        text_colour = palette.text().color()
        faint = QColor(text_colour)
        faint.setAlpha(110)
        painter.fillRect(self.rect(), palette.base())
        model = self._model
        if model is None or model.nb_frame == 0:
            painter.setPen(faint)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Nothing to show - the sequence stops before its first frame")
            painter.end()
            return

        nb = model.nb_frame
        x_end = self._x(nb)
        anim_top = self.RULER_H
        lanes_top = anim_top + self.ANIM_H

        # The loop region: everything from loop_from to the end repeats forever. A tint
        # says so without a word, the ↻ marker at its start gives the frame.
        if model.loop_from is not None:
            loop_colour = QColor(text_colour)
            loop_colour.setAlpha(14)
            painter.fillRect(int(self._x(model.loop_from)), 0,
                             int(x_end - self._x(model.loop_from)), self._content_height(),
                             loop_colour)

        # Ruler: baseline, ticks and frame numbers.
        painter.setPen(faint)
        painter.drawLine(int(self._x(0)), self.RULER_H - 1, int(x_end), self.RULER_H - 1)
        step = self._tick_step()
        for frame in range(0, nb + 1, step):
            x = int(self._x(frame))
            painter.drawLine(x, self.RULER_H - 5, x, self.RULER_H - 1)
            painter.drawText(x + 2, self.RULER_H - 6, str(frame))
        if model.loop_from is not None:
            painter.drawText(int(self._x(model.loop_from)) + 2, lanes_top - 8, "↻")

        # Animation lane: one block per stretch of the same animation, waiting frames
        # marked by a thin darker strip along the block's bottom edge.
        painter.setPen(faint)
        painter.drawText(2, anim_top + self.ANIM_H - 7, "anim")
        for block in model.animation_blocks:
            x0 = self._x(block.first_frame)
            x1 = self._x(block.last_frame + 1)
            colour = self._animation_colour(block.anim_id)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            painter.drawRoundedRect(int(x0), anim_top + 2, max(int(x1 - x0) - 1, 2),
                                    self.ANIM_H - 5, 3, 3)
            label = str(block.anim_id)
            if painter.fontMetrics().horizontalAdvance(label) + 6 < (x1 - x0):
                painter.setPen(QColor(20, 20, 20))
                painter.drawText(int(x0) + 3, anim_top + self.ANIM_H - 7, label)
        strip = QColor(0, 0, 0, 90)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(strip)
        for frame in model.waiting_frame_set:
            painter.drawRect(int(self._x(frame)), anim_top + self.ANIM_H - 5,
                             max(int(self._px_per_frame), 1), 2)

        # Event lanes.
        for lane_index, (kind, label, bar_list) in enumerate(model.lane_list):
            top = lanes_top + lane_index * self.LANE_H
            painter.setPen(faint)
            painter.drawText(2, top + self.LANE_H - 4, label)
            colour = QColor(COLOUR_BY_KIND.get(
                kind, COLOUR_BY_EXTRA_KIND.get(kind, "#909090")))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            for bar in bar_list:
                x0 = self._x(bar.first_frame)
                width = max((bar.last_frame + 1 - bar.first_frame) * self._px_per_frame - 1,
                            4.0)
                painter.drawRoundedRect(int(x0), top + 2, int(width), self.LANE_H - 5, 2, 2)

        # Value curves: each varying variable as a polyline, normalised to its own
        # min..max inside the band (scales differ wildly - rotation is 0..4096, a
        # position a few thousand). Legend stacked in the label gutter.
        if self._series:
            band_top = lanes_top + len(model.lane_list) * self.LANE_H + 3
            band_bottom = band_top + self.CURVE_H - 6
            painter.setPen(faint)
            painter.drawLine(int(self._x(0)), band_top - 2, int(x_end), band_top - 2)
            for series_index, (name, (colour_name, value_list)) \
                    in enumerate(self._series.items()):
                minimum, maximum = min(value_list), max(value_list)
                span = (maximum - minimum) or 1
                colour = QColor(colour_name)
                painter.setPen(QPen(colour, 1))
                point_list = [QPointF(self._x(frame + 0.5),
                                      band_bottom - (value - minimum)
                                      / span * (band_bottom - band_top))
                              for frame, value in enumerate(value_list)]
                painter.drawPolyline(QPolygonF(point_list))
                painter.drawText(2, band_top + 9 + series_index * 10, name)

        # End-of-timeline marker: where and why it stopped. Red only when the engine
        # would actually be in trouble there (hang / error), gray otherwise.
        is_bad = model.stop_reason in (STOP_HANG, STOP_ERROR)
        stop_colour = QColor("#bf616a") if is_bad else faint
        painter.setPen(QPen(stop_colour, 2))
        painter.drawLine(int(x_end) + 1, 0, int(x_end) + 1, self._content_height())
        painter.setPen(stop_colour)
        painter.drawText(int(x_end) + 4, self.RULER_H - 6, "■" if is_bad else "▪")

        # Playhead, over everything: line + a small triangle in the ruler.
        playhead_x = self._x(self._current_frame + 0.5)
        playhead_colour = QColor("#d9534f")
        painter.setPen(QPen(playhead_colour, 2))
        painter.drawLine(int(playhead_x), 0, int(playhead_x), self._content_height())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(playhead_colour)
        painter.drawPolygon(QPolygonF([QPointF(playhead_x - 4, 0), QPointF(playhead_x + 4, 0),
                                       QPointF(playhead_x, 7)]))
        painter.end()

    # ------------------------------------------------------------------ interaction
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._model is not None:
            self.frame_selected.emit(self._frame_at(event.position().x()))

    def mouseMoveEvent(self, event):
        if self._model is None:
            return
        if event.buttons() & Qt.MouseButton.LeftButton:   # drag = scrub
            self.frame_selected.emit(self._frame_at(event.position().x()))
            return
        if event.position().x() >= self.LABEL_W:
            frame = self._frame_at(event.position().x())
            QToolTip.showText(event.globalPosition().toPoint(),
                              self._tooltip_html(frame), self)

    def wheelEvent(self, event):
        """Ctrl+wheel zooms the frame axis; a plain wheel is left to the scroll area."""
        if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            event.ignore()
            return
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        self._px_per_frame = max(self.MIN_PX_PER_FRAME,
                                 min(self._px_per_frame * factor, self.MAX_PX_PER_FRAME))
        self.updateGeometry()
        self.update()
        event.accept()

    def _tooltip_html(self, frame_index) -> str:
        """What is happening on the hovered frame: the animation state and the commands
        that ran, coloured like their lanes."""
        frame = self._result.frame_list[frame_index]
        line_list = [f"<b>Frame {frame.index}</b>"]
        if frame.anim_id is not None:
            text = f"anim {frame.anim_id}"
            if frame.anim_total:
                text += f" [{frame.anim_frame}/{frame.anim_total}]"
            if frame.is_waiting_animation:
                text += " (waiting for it)"
            line_list.append(text)
        for command in frame.command_list:
            if command.is_background:
                continue
            colour = COLOUR_BY_KIND.get(command.kind, "#909090")
            # A value note tells what really happened (formula, branch outcome) - more
            # useful under the cursor than the generic description.
            description = (command.value_note or command.description
                           or f"op {command.op_code:02X}")
            line_list.append(f"<span style='color:{colour}'>●</span> "
                             f"{command.op_code:02X} {description}")
        for name, (colour_name, value_list) in self._series.items():
            if frame_index < len(value_list):
                line_list.append(f"<span style='color:{colour_name}'>—</span> "
                                 f"{name} = {value_list[frame_index]}")
        return "<br/>".join(line_list)
