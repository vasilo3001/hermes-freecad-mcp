"""
Hermes FreeCAD MCP — FreeCAD Addon
===================================
Init.py — executed when FreeCAD starts (GUI or console mode).

This file registers the addon module. The heavy lifting (TCP server,
command dispatch) lives in the main module and is started from
InitGui.py when the user activates the workbench.
"""

import os
import sys

# Add the addon directory to sys.path so submodules can be imported
_addon_dir = os.path.dirname(os.path.abspath(__file__))
if _addon_dir not in sys.path:
    sys.path.insert(0, _addon_dir)

print("[HermesFreeCADMCP] Addon loaded. Activate the workbench to start the MCP bridge.")
