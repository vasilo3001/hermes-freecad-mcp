"""
Hermes FreeCAD MCP — FreeCAD Addon
===================================
InitGui.py — executed when FreeCAD starts in GUI mode.

Registers the "Hermes MCP" workbench. The bridge starts automatically
when the workbench is activated.
"""

import FreeCAD
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets


class HermesMCPWorkbench(Gui.Workbench):
    """FreeCAD workbench for the Hermes MCP bridge."""

    MenuText = "Hermes MCP"
    ToolTip = "Bridge for Hermes Agent to control FreeCAD via MCP"
    Icon = ""

    def __init__(self):
        self._bridge = None

    def Initialize(self):
        """Executed when the workbench is first activated."""
        FreeCAD.Console.PrintMessage(
            "[HermesFreeCADMCP] Workbench loaded.\n"
        )

    def Activated(self):
        """Executed whenever the workbench is activated."""
        # Inline everything to avoid FreeCAD's restricted scope issues
        if self._bridge is None:
            import os
            import sys
            _addon_dir = os.path.expanduser(
                "~/.var/app/org.freecad.FreeCAD/data/FreeCAD/v1-1/Mod/hermes_freecad_mcp"
            )
            if not os.path.isdir(_addon_dir):
                _addon_dir = os.path.expanduser(
                    "~/.local/share/FreeCAD/Mod/hermes_freecad_mcp"
                )
            if _addon_dir not in sys.path:
                sys.path.insert(0, _addon_dir)
            from mcp_bridge import MCPBridgeServer, MCP_BRIDGE_PORT
            self._bridge = MCPBridgeServer(port=MCP_BRIDGE_PORT)

        if not self._bridge.is_running:
            self._bridge.start()
            FreeCAD.Console.PrintMessage(
                "[HermesFreeCADMCP] Bridge started on port %d.\n"
                % self._bridge.port
            )

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


# Register the workbench
Gui.addWorkbench(HermesMCPWorkbench())
