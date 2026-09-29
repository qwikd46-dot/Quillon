#!/usr/bin/env python3
import sys
import os
os.environ['QT_QPA_PLATFORM'] = 'xcb'
from PyQt6.QtWidgets import QApplication
# Import our browser
from quillon import Quillonrowser

app = QApplication(sys.argv)
print('Creating Quillonrowser...')
browser = Quillonrowser()
print('Quillonrowser created')
browser.show()
print('Quillonrowser shown')
print('Entering event loop...')
sys.exit(app.exec())
