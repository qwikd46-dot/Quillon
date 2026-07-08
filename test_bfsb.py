#!/usr/bin/env python3
import sys
import os
os.environ['QT_QPA_PLATFORM'] = 'xcb'
from PyQt6.QtWidgets import QApplication
# Import our browser
from bfsb import BFSBrowser

app = QApplication(sys.argv)
print('Creating BFSBrowser...')
browser = BFSBrowser()
print('BFSBrowser created')
browser.show()
print('BFSBrowser shown')
print('Entering event loop...')
sys.exit(app.exec())
