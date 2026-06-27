"""
ApexAutomation.pyw — double-click this to launch the GUI with no terminal window.
The .pyw extension tells Windows to use pythonw.exe (silent, no console).
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gui
gui.main()
