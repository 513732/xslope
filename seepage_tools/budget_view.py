"""Embedded, offline viewer for an XSLOPE nodal budget."""
import base64
import json
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QObject, QProcess, QUrl, Qt, Signal
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QMessageBox, QProgressDialog,
                               QPushButton, QVBoxLayout, QWidget)


class BudgetPage(QWebEnginePage):
    def __init__(self, profile, parent):
        super().__init__(profile, parent)
        self.root = None
        self.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)

    def acceptNavigationRequest(self, url, kind, is_main_frame):
        if not url.isLocalFile() or self.root is None:
            return False
        path = Path(url.toLocalFile()).resolve()
        return path.suffix.lower() == ".html" and path.is_relative_to(self.root)


class BudgetView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.browser = QWebEngineView()
        # Give the profile a longer lifetime than all pages using it.
        self.profile = QWebEngineProfile(self)
        self.page = BudgetPage(self.profile, self.profile)
        self.browser.setPage(self.page)
        self.profile.downloadRequested.connect(self._download)
        self.browser.loadStarted.connect(self._loading)
        self.browser.loadFinished.connect(self._loaded)

        self.back = QPushButton("Back")
        self.back.clicked.connect(self.browser.back)
        self.export_html = QPushButton("Export HTML…")
        self.export_html.clicked.connect(self._export_html)
        self.export_video = QPushButton("Save budget video…")
        self.export_video.clicked.connect(self._start_video)
        bar = QHBoxLayout()
        bar.addWidget(self.back)
        bar.addStretch()
        bar.addWidget(self.export_html)
        bar.addWidget(self.export_video)
        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(self.browser, 1)
        self._job = None
        self._loading()

    def open_report(self, path):
        path = Path(path).resolve()
        self.page.root = path.parent
        self.browser.load(QUrl.fromLocalFile(str(path)))

    def _loading(self):
        self.export_html.setEnabled(False)
        self.export_video.setEnabled(False)

    def _loaded(self, ok):
        self.back.setEnabled(self.browser.history().canGoBack())
        if ok:
            page_url = self.browser.url()
            self.page.runJavaScript(
                """JSON.stringify({
                  report: Boolean(document.getElementById('dataset')),
                  transient: typeof D !== 'undefined' && D.analysis === 'transient'})""",
                lambda state: self._set_exports(page_url, state))

    def _set_exports(self, url, state):
        if self.browser.url() == url and state:
            state = json.loads(state)
            self.export_html.setEnabled(bool(state.get("report")))
            self.export_video.setEnabled(bool(state.get("transient")) and self._job is None)

    def _download(self, request):
        name = Path(request.suggestedFileName()).name
        path, _ = QFileDialog.getSaveFileName(self, "Export budget data", name, "CSV (*.csv)")
        if not path:
            request.cancel()
            return
        destination = Path(path).resolve()
        if destination.suffix.lower() != ".csv":
            destination = destination.with_name(destination.name + ".csv")
            if destination.exists() and QMessageBox.question(
                    self, "Replace CSV?", f"Replace {destination.name}?") != QMessageBox.Yes:
                request.cancel()
                return
        request.setDownloadDirectory(str(destination.parent))
        request.setDownloadFileName(destination.name)
        request.accept()

    def _export_html(self):
        source = Path(self.browser.url().toLocalFile())
        path, _ = QFileDialog.getSaveFileName(self, "Export standalone water budget", source.name, "HTML (*.html)")
        if not path:
            return
        destination = Path(path).resolve()
        if destination.suffix.lower() != ".html":
            destination = destination.with_name(destination.name + ".html")
            if destination.exists() and QMessageBox.question(
                    self, "Replace HTML?", f"Replace {destination.name}?") != QMessageBox.Yes:
                return
        if source.resolve() == destination:
            return
        try:
            shutil.copyfile(source, destination)
        except OSError as exc:
            QMessageBox.warning(self, "Could not export HTML", str(exc))

    def _start_video(self):
        if self._job is not None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save water-budget video", "Water budget.mp4", "MP4 (*.mp4)")
        if not path:
            return
        destination = Path(path).resolve()
        if destination.suffix.lower() != ".mp4":
            destination = destination.with_name(destination.name + ".mp4")
            if destination.exists() and QMessageBox.question(
                    self, "Replace video?", f"Replace {destination.name}?") != QMessageBox.Yes:
                return
        self.export_video.setEnabled(False)
        self.page.runJavaScript("JSON.stringify(window.budgetVideo?.begin() || null)",
                                lambda state: self._video_ready(destination, json.loads(state) if state else None))

    def _video_ready(self, destination, state):
        if not state:
            self.export_video.setEnabled(True)
            QMessageBox.information(self, "Water-budget video",
                                    "Wait for the budget to load and select one or more nodes or a pit boundary.")
            return
        try:
            from imageio_ffmpeg import get_ffmpeg_exe
            encoder = get_ffmpeg_exe()
            folder = TemporaryDirectory(prefix=".xslope-video-", dir=destination.parent)
        except (ImportError, OSError, RuntimeError) as exc:
            self.page.runJavaScript("window.budgetVideo?.end()")
            self.export_video.setEnabled(True)
            QMessageBox.warning(self, "Could not start video export", str(exc))
            return
        self._job = BudgetVideo(self, destination, state, folder, encoder)
        self._job.finished.connect(self._video_finished)
        self._job.capture_next()

    def _video_finished(self, error):
        job, self._job = self._job, None
        self.export_video.setEnabled(True)
        self.page.runJavaScript("window.budgetVideo?.end()")
        if error:
            QMessageBox.warning(self, "Could not save water-budget video", error)
        job.deleteLater()




class BudgetVideo(QObject):
    """Capture the section and chart together, then encode an atomic MP4."""
    finished = Signal(str)

    def __init__(self, view, destination, state, folder, encoder):
        super().__init__(view)
        self.view = view
        self.destination = destination
        self.folder = folder
        self.directory = Path(folder.name)
        self.encoder = encoder
        self.fps = float(state["fps"])
        self.count = int(state["frames"])
        self.index = 0
        self.done = False
        self.progress = QProgressDialog("Rendering drawdown and flow…", "Cancel", 0, self.count+1, view)
        self.progress.setWindowModality(Qt.WindowModal)
        self.progress.setMinimumDuration(0)
        self.progress.setValue(0)
        self.progress.canceled.connect(self.cancel)
        self.process = QProcess(self)
        self.process.finished.connect(self._encoded)
        self.process.errorOccurred.connect(self._process_error)

    def capture_next(self):
        if not self.done:
            self.view.page.runJavaScript(f"window.budgetVideo.frame({self.index})", self._captured)

    def _captured(self, data):
        if self.done:
            return
        try:
            if not data:
                raise ValueError("The budget page stopped producing frames.")
            frame = base64.b64decode(data, validate=True)
            (self.directory / f"{self.index:06}.png").write_bytes(frame)
        except (OSError, ValueError) as exc:
            self._finish(str(exc))
            return
        self.index += 1
        self.progress.setValue(self.index)
        if self.done:
            return
        if self.index < self.count:
            self.capture_next()
            return
        self.progress.setLabelText("Encoding MP4…")
        self.process.start(self.encoder, [
            "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-framerate", str(self.fps), "-i", str(self.directory / "%06d.png"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(self.directory / "video.mp4")])

    def cancel(self):
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()
        else:
            self._finish("")

    def _process_error(self, error):
        if error == QProcess.FailedToStart:
            self._finish(self.process.errorString())

    def _encoded(self, code, status):
        if self.progress.wasCanceled():
            self._finish("")
            return
        if code or status != QProcess.NormalExit:
            error = bytes(self.process.readAllStandardError()).decode(errors="replace").strip()
            self._finish(error or "The video encoder exited unexpectedly.")
            return
        try:
            os.replace(self.directory / "video.mp4", self.destination)
        except OSError as exc:
            self._finish(str(exc))
        else:
            self._finish("")

    def _finish(self, error):
        if self.done:
            return
        self.done = True
        self.progress.close()
        self.progress.deleteLater()
        self.folder.cleanup()
        self.finished.emit(error)
