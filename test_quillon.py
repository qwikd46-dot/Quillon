#!/usr/bin/env python3
import sys
import os
os.environ['QT_QPA_PLATFORM'] = 'xcb'
from PyQt6.QtWidgets import QApplication
# Import our browser
from quillon import QuillonWindow, URLBlocker

app = QApplication(sys.argv)
print('Creating QuillonWindow...')
browser = QuillonWindow(URLBlocker())
print('QuillonWindow created')
browser.show()
print('QuillonWindow shown')
print('Entering event loop...')
sys.exit(app.exec())
