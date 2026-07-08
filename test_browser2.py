#!/usr/bin/env python3
import sys
import os
os.environ['QT_QPA_PLATFORM'] = 'xcb'
from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import QUrl

app = QApplication(sys.argv)
window = QMainWindow()
view = QWebEngineView()
view.setUrl(QUrl('http://localhost:8888'))
window.setCentralWidget(view)
window.resize(1024, 768)
window.setWindowTitle('Test Browser')
window.show()
print('Window shown, entering event loop')
sys.exit(app.exec())
