"""Start the installed Studio with one additional menu, in this process only."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory, mkdtemp

from PySide6.QtCore import QProcess, QProcessEnvironment
from PySide6.QtWidgets import QFileDialog, QMessageBox, QInputDialog
from studio import app
from result_tools import install_result_tools
from budget_view import BudgetView


class BudgetWindow(app.MainWindow):
    def __init__(self):
        install_result_tools()
        super().__init__()
        self.budget_view = None
        self._budget_directory = TemporaryDirectory(prefix="xslope-budget-")
        menu = self.menuBar().addMenu("Water budget")
        self.current_budget_action = menu.addAction("Build for open workbook…")
        self.current_budget_action.triggered.connect(self.build_current_budget)
        self.build_action = menu.addAction("Build from saved workbook…")
        self.build_action.triggered.connect(self.build_budget)
        menu.addAction("UKCP18 surface water balance…").triggered.connect(self.open_climate)
        self.worker = QProcess(self)
        self.worker.setProcessChannelMode(QProcess.MergedChannels)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUTF8", "1")
        self.worker.setProcessEnvironment(environment)
        self.worker.finished.connect(self.finished_budget)
        self.worker.errorOccurred.connect(self.worker_error)
        self.worker.readyReadStandardOutput.connect(self.read_output)
        self.log_tail = ""

    def open_climate(self):
        from climate_dialog import ClimateDialog
        dialog = ClimateDialog(self)
        dialog.results_ready.connect(self.show_budget_report)
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
            self.set_budget_building(False)
            QMessageBox.warning(self, "Water budget", self.worker.errorString())

    def set_budget_building(self, building):
        self.current_budget_action.setEnabled(not building)
        self.build_action.setEnabled(not building)

    def show_budget_report(self, path):
        if self.budget_view is None:
            self.budget_view = BudgetView(self)
            self.view_tabs.addTab(self.budget_view, "Water budget")
        self.budget_view.open_report(path)
        self.view_tabs.setCurrentWidget(self.budget_view)

    def build_current_budget(self):
        if not self.doc.path or self.doc.dirty:
            QMessageBox.information(self, "Water budget", "Save the open workbook and its mesh before building the budget.")
            return
        self._build_budget(self.doc.path)

    def build_budget(self):
        project, _ = QFileDialog.getOpenFileName(self, "Select saved workbook", "", "XSLOPE workbook (*.xlsx)")
        if project:
            self._build_budget(project)

    def _build_budget(self, project):
        choices = ["Automatic (from workbook)", "Steady – BC set 1", "Steady – BC set 2", "Transient"]
        choice, ok = QInputDialog.getItem(self, "Seepage results", "Analysis", choices, 0, False)
        if not ok:
            return
        index = choices.index(choice)
        mode = ["auto", "steady", "steady", "transient"][index]
        bc_set = "2" if index == 2 else "1"
        suggested = Path(project).stem + "_budget.html"
        folder = Path(mkdtemp(dir=self._budget_directory.name))
        self.output_path = str(folder / suggested)
        self.log_tail = ""
        self.set_budget_building(True)
        self.statusBar().showMessage("Building water budget from the saved workbook and mesh…")
        self.worker.start(sys.executable, ["-u", str(Path(__file__).with_name("build_budget.py")),
                                          project, "--mode", mode, "--bc-set", bc_set, "--output", self.output_path])

    def finished_budget(self, code, status):
        self.read_output()
        self.set_budget_building(False)
        if code or status != QProcess.NormalExit:
            self.statusBar().showMessage("Water budget build failed.")
            QMessageBox.warning(self, "Water budget", self.log_tail[-2000:] or "Solver did not finish.")
            return
        self.show_budget_report(self.output_path)
        self.statusBar().showMessage("Water budget ready. Use Export HTML to save a standalone copy.")


if __name__ == "__main__":
    app.MainWindow = BudgetWindow
    sys.exit(app.main())
