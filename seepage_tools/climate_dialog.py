"""Local project editor and asynchronous climate runner for XSLOPE Studio."""
import json
import sys
from pathlib import Path
from PySide6.QtCore import QProcess,QProcessEnvironment,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog,QFileDialog,QHBoxLayout,QLabel,QLineEdit,
                               QMessageBox,QPlainTextEdit,QPushButton,QVBoxLayout)
from climate_run import template


class ClimateDialog(QDialog):
    def __init__(self,parent):
        super().__init__(parent);self.setWindowTitle('UKCP18 surface water balance');self.resize(850,720)
        layout=QVBoxLayout(self);bar=QHBoxLayout();layout.addLayout(bar)
        for label,fn in [('New from workbook',self.new),('Open project',self.load),('Save as',self.save)]:
            b=QPushButton(label);b.clicked.connect(fn);bar.addWidget(b)
        layout.addWidget(QLabel('Set the BNG site, climate files, recharge reaches and pit dimensions in the project below.'))
        self.edit=QPlainTextEdit();layout.addWidget(self.edit,3)
        self.key=QLineEdit();self.key.setEchoMode(QLineEdit.Password);self.key.setPlaceholderText('Met Office UKCP API key for online rainfall (this session only)')
        layout.addWidget(self.key)
        self.go=QPushButton('Save and run climate scenarios');self.go.clicked.connect(self.launch);layout.addWidget(self.go)
        self.log=QPlainTextEdit();self.log.setReadOnly(True);self.log.setMaximumBlockCount(150);layout.addWidget(self.log,1)
        self.process=QProcess(self);self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.output);self.process.finished.connect(self.finish)
        self.process.errorOccurred.connect(lambda err:self.log.appendPlainText(self.process.errorString()))
        self.path=None;self.tail=''

    def new(self):
        f,_=QFileDialog.getOpenFileName(self,'Choose saved XSLOPE workbook','','Workbooks (*.xlsx)')
        if not f:return
        try:data=template(Path(f))
        except Exception as e:QMessageBox.warning(self,'Workbook',str(e));return
        self.path=None;self.edit.setPlainText(json.dumps(data,indent=2))

    def load(self):
        f,_=QFileDialog.getOpenFileName(self,'Open climate project','','Project (*.json)')
        if f:
            self.path=Path(f);self.edit.setPlainText(self.path.read_text(encoding='utf-8-sig'))

    def save(self):
        try:data=json.loads(self.edit.toPlainText())
        except ValueError as e:QMessageBox.warning(self,'Project',str(e));return False
        f,_=QFileDialog.getSaveFileName(self,'Save climate project',str(self.path or 'climate_project.json'),'Project (*.json)')
        if not f:return False
        self.path=Path(f);self.path.write_text(json.dumps(data,indent=2),encoding='utf-8');return True

    def launch(self):
        if not self.save():return
        data=json.loads(self.edit.toPlainText())
        env=QProcessEnvironment.systemEnvironment()
        if self.key.text():env.insert('UKCP_API_KEY',self.key.text())
        if not (data.get('paired_csv') or data.get('rain_folder') or env.value('UKCP_API_KEY')):
            QMessageBox.warning(self,'Climate inputs','Enter the UKCP key or select local climate inputs in the project.');return
        self.process.setProcessEnvironment(env);self.tail='';self.log.clear()
        self.go.setEnabled(False);self.process.start(sys.executable,['-u',str(Path(__file__).with_name('climate_run.py')),str(self.path.resolve())])
        if not self.process.waitForStarted(3000):self.go.setEnabled(True)

    def output(self):
        chunk=bytes(self.process.readAllStandardOutput()).decode(errors='replace')
        self.tail=(self.tail+chunk)[-4000:];self.log.appendPlainText(chunk.rstrip())

    def finish(self,code,status):
        self.output();self.go.setEnabled(True)
        if code:QMessageBox.warning(self,'Climate run',self.tail[-800:]);return
        p=Path(self.tail.strip().splitlines()[-1])
        if p.is_file():QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    def reject(self):
        if self.process.state()!=QProcess.NotRunning:
            if QMessageBox.question(self,'Climate run','Stop the current run and close?')!=QMessageBox.Yes:return
            self.process.kill();self.process.waitForFinished(3000)
        super().reject()
