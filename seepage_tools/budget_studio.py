"""Start the installed Studio with one additional menu, in this process only."""
from pathlib import Path
import sys

from PySide6.QtCore import QProcess, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QMessageBox, QInputDialog
from studio import app


class BudgetWindow(app.MainWindow):
    def __init__(self):
        super().__init__()
        menu = self.menuBar().addMenu("Water budget")
        self.build_action = menu.addAction("Build from saved workbook…")
        self.build_action.triggered.connect(self.build_budget)
        menu.addAction("UKCP18 surface water balance…").triggered.connect(self.open_climate)
        self.worker = QProcess(self)
        self.worker.setProcessChannelMode(QProcess.MergedChannels)
        self.worker.finished.connect(self.finished_budget)
        self.worker.errorOccurred.connect(self.worker_error)
        self.worker.readyReadStandardOutput.connect(self.read_output)
        self.log_tail = ""

    def open_climate(self):
        from climate_dialog import ClimateDialog
        dialog = ClimateDialog(self)
        dialog.exec()
        dialog.deleteLater()

    def read_output(self):
        text = bytes(self.worker.readAllStandardOutput()).decode(errors="replace")
        self.log_tail = (self.log_tail + text)[-6000:]
        for line in text.splitlines():
            if "frame saved" in line:
                self.statusBar().showMessage("Water budget: " + line.strip())

    def worker_error(self, error):
        if error == QProcess.FailedToStart:
            self.build_action.setEnabled(True)
            QMessageBox.warning(self, "Water budget", self.worker.errorString())

    def build_budget(self):
        project, _ = QFileDialog.getOpenFileName(self, "Select saved workbook", "", "XSLOPE workbook (*.xlsx)")
        if not project:
            return
        choices = ["Automatic (from workbook)", "Steady – BC set 1", "Steady – BC set 2", "Transient"]
        choice, ok = QInputDialog.getItem(self, "Seepage results", "Analysis", choices, 0, False)
        if not ok:
            return
        index = choices.index(choice)
        mode = ["auto", "steady", "steady", "transient"][index]
        bc_set = "2" if index == 2 else "1"
        suggested = Path(project).with_name(Path(project).stem + "_budget.html")
        dest, _ = QFileDialog.getSaveFileName(self, "Save interactive water budget", str(suggested), "HTML (*.html)")
        if not dest:
            return
        self.output_path = str(Path(dest).absolute())
        self.log_tail = ""
        self.build_action.setEnabled(False)
        self.statusBar().showMessage("Building water budget from the saved workbook and mesh…")
        self.worker.start(sys.executable, ["-u", str(Path(__file__).with_name("build_budget.py")),
                                          project, "--mode", mode, "--bc-set", bc_set, "--output", self.output_path])

    def finished_budget(self, code, status):
        self.read_output()
        self.build_action.setEnabled(True)
        if code or status != QProcess.NormalExit:
            self.statusBar().showMessage("Water budget build failed.")
            QMessageBox.warning(self, "Water budget", self.log_tail[-2000:] or "Solver did not finish.")
            return
        self.statusBar().showMessage("Water budget saved: " + self.output_path)
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.output_path))


if __name__ == "__main__":
    app.MainWindow = BudgetWindow
    sys.exit(app.main())
