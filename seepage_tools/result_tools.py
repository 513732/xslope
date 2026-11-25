"""Extra tools for the native XSLOPE Studio seepage results."""
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory

from matplotlib.animation import FFMpegWriter
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.contour import ContourSet
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox, QProgressDialog

from studio import main_window, transient
from studio.canvas import BASE_DPI, MplCanvas
from xslope.plot import declared_unit_labels


class ResultsCanvas(MplCanvas):
    """Use Studio's existing contour renderer and image export."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.vertical_scale = 1.0
        self._labels = {}
        self._seep_data = None
        self._label_key = None
        self._units = ""
        self._result_tools = None
        self._seep_drawing = False

    def _add_result_tools(self):
        label = self.add_tool_button("Label contour", "Click near a contour to place its value", lambda: None)
        label.setCheckable(True)
        label.toggled.connect(self.set_click_pick_enabled)
        self.clicked.connect(lambda x, y, _tol: self.label_contour(x, y))
        clear = self.add_tool_button("Clear labels", "Clear labels for this result and frame", self._clear_labels)
        scale = self.add_tool_button("Vertical scale…", "Set the vertical scale relative to horizontal", self._choose_scale)
        self._result_tools = (label, clear, scale)

    def render_seep_solution(self, seep_data, solution, opts, style=None):
        if self._result_tools is None:
            self._add_result_tools()
        if self._seep_data is not seep_data:
            self._labels = {}
            self._seep_data = seep_data
        variable = (opts or {}).get("variable", "head")
        frame = solution.get("time")
        self._label_key = (variable, frame)
        unit_key = {"head": "length", "u": "stress", "v_mag": "k"}.get(variable)
        self._units = (declared_unit_labels(seep_data) or {}).get(unit_key, "")
        self._seep_drawing = True
        try:
            super().render_seep_solution(seep_data, solution, opts, style)
        finally:
            self._seep_drawing = False

    def _draw(self, draw_fn, dxf=True):
        if self._seep_drawing:
            original_draw = draw_fn

            def draw_fn(figure):
                original_draw(figure)
                ax = figure.axes[0]
                ax.set_aspect(self.vertical_scale, adjustable="box")
                for x, y, text in self._labels.get(self._label_key, ()):
                    self._draw_label(ax, x, y, text)

        if self._result_tools:
            for button in self._result_tools:
                button.setVisible(self._seep_drawing)
            if not self._seep_drawing:
                self._result_tools[0].setChecked(False)
        super()._draw(draw_fn, dxf)

    @staticmethod
    def _draw_label(ax, x, y, text):
        ax.annotate(text, (x, y), xytext=(4, 4), textcoords="offset points",
                    fontsize=9, color="#152635", zorder=30, annotation_clip=True,
                    bbox={"boxstyle": "round,pad=0.22", "fc": "white", "ec": "#637786", "alpha": 0.94})

    def label_contour(self, x, y):
        # Convert the snapping radius to the rendered figure's pixels. A data-space
        # radius would distort the hit area when vertical scale differs from 1.
        self.render_now()
        ax = self._main_axes()
        if ax is None or self._label_key is None:
            return False
        contour = next((artist for artist in ax.collections
                        if isinstance(artist, ContourSet) and artist.get_gid() == "HEAD_CONTOURS"), None)
        if contour is None or not any(len(path.vertices) for path in contour.get_paths()):
            return False
        px, py = ax.transData.transform((x, y))
        level, _, _, cx, cy, distance_squared = contour.find_nearest_contour(px, py)
        radius = 10 * self.figure.dpi / (BASE_DPI * self.view.transform().m11())
        if level is None or distance_squared > radius**2 or not ax.bbox.contains(cx, cy):
            return False
        label_x, label_y = ax.transData.inverted().transform((cx, cy))
        text = f"{contour.levels[level]:.6g}"
        if self._units:
            text += f" {self._units}"
        self._labels.setdefault(self._label_key, []).append((label_x, label_y, text))
        self._draw_label(ax, label_x, label_y, text)
        self._rasterize(self._target_dpi())
        return True

    def _clear_labels(self):
        self._labels.pop(self._label_key, None)
        self._rendered_vp = None
        self.render_now()

    def _choose_scale(self):
        value, accepted = QInputDialog.getDouble(
            self, "Vertical scale", "Vertical to horizontal scale (1 = equal):",
            self.vertical_scale, 0.1, 20.0, 2)
        if accepted:
            self.set_vertical_scale(value)

    def set_vertical_scale(self, value):
        self.vertical_scale = value
        self._rendered_vp = None
        self.render_now()


class StudioVideoWriter(FFMpegWriter):
    @classmethod
    def bin_path(cls):
        from imageio_ffmpeg import get_ffmpeg_exe
        return get_ffmpeg_exe()


def export_mp4(view, destination, progress=None, *, frames=None):
    """Render saved frames at the playback rate, leaving the active frame alone."""
    frames = tuple(view._frames if frames is None else frames)
    if not frames:
        raise ValueError("No saved transient frames are available.")
    destination = Path(destination)
    options = dict(view._opts_getter() or {})
    style = view._style_getter()
    source_canvas = view.canvas
    render_canvas = ResultsCanvas()
    render_canvas.vertical_scale = source_canvas.vertical_scale
    render_canvas._seep_data = view._seep_data
    render_canvas._labels = {key: tuple(labels) for key, labels in source_canvas._labels.items()}
    figure = Figure(figsize=(12.8, 7.2), dpi=100)
    FigureCanvasAgg(figure)
    writer = StudioVideoWriter(fps=1000 / view._interval_ms(), codec="libx264",
                               extra_args=["-pix_fmt", "yuv420p", "-movflags", "+faststart"])
    cancelled = False
    try:
        with TemporaryDirectory(dir=destination.parent) as folder:
            temporary = Path(folder) / "transient.mp4"
            with writer.saving(figure, str(temporary), dpi=100):
                for number, frame in enumerate(frames, 1):
                    figure.clear()
                    render_canvas.render_seep_solution(view._seep_data, frame, options, style)
                    render_canvas._draw_fn(figure)
                    writer.grab_frame()
                    if progress is not None and not progress(number):
                        cancelled = True
                        break
            if not cancelled:
                temporary.replace(destination)
    finally:
        render_canvas.deleteLater()
    return not cancelled


class ResultsTransientView(transient.TransientSeepView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.video_button = self.canvas.add_tool_button(
            "Save video…", "Export all saved transient frames as an MP4", self._save_video)

    def _save_video(self):
        if not self._frames:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save transient seepage video", "Transient seepage.mp4", "MP4 video (*.mp4)")
        if not path:
            return
        destination = Path(path)
        if destination.suffix.lower() != ".mp4":
            destination = destination.with_suffix(".mp4")
        self._btn_play.setChecked(False)
        progress = QProgressDialog("Rendering saved seepage frames…", "Cancel", 0, len(self._frames), self)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        def advance(count):
            progress.setValue(count)
            return not progress.wasCanceled()

        try:
            saved = export_mp4(self, destination, advance)
        except Exception as exc:
            progress.close()
            QMessageBox.warning(self, "Could not save video", str(exc))
        else:
            progress.close()
            if saved:
                QMessageBox.information(self, "Video saved", str(destination))


def install_result_tools():
    """Use the enhanced canvas only within the Budget Studio process."""
    main_window.MplCanvas = ResultsCanvas
    transient.MplCanvas = ResultsCanvas
    transient.TransientSeepView = ResultsTransientView
