#! python 3
# r: numpy
"""Avvia lo slicer interno di Generative Print in Rhino 8.

Seleziona cio che vuoi stampare, poi in Rhino: comando _ScriptEditor, apri questo file ed esegui (F5),
oppure _-RunPythonScript e il percorso di questo file.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.dirname(HERE), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import gp_slicer  # noqa: E402

gp_slicer.start()
