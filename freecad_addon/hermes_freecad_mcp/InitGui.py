"""
Hermes FreeCAD MCP — FreeCAD Addon
===================================
InitGui.py — executed when FreeCAD starts in GUI mode.

Registers the "Hermes MCP" workbench. When the user activates the
workbench, the TCP server starts and the addon is ready to receive
commands from the Hermes Agent via the MCP bridge.
"""

import FreeCAD
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

# Import the core addon module
import os
_addon_dir = os.path.dirname(os.path.abspath(__file__))
if _addon_dir not in __import__('sys').path:
    import sys
    sys.path.insert(0, _addon_dir)

from mcp_bridge import MCPBridgeServer, MCP_BRIDGE_PORT


class HermesMCPWorkbench:
    """FreeCAD workbench for the Hermes MCP bridge."""

    MenuText = "Hermes MCP"
    ToolTip = "Bridge for Hermes Agent to control FreeCAD via MCP"
    Icon = ""  # No icon for now

    def __init__(self):
        self.bridge = None
        self.status_label = None

    def Initialize(self):
        """Executed when the workbench is first activated."""
        # Create toolbar
        self.toolbar = Gui.addToolbar("Hermes MCP")

        # Start/Stop command
        self.cmd_start = CmdStartBridge()
        self.cmd_stop = CmdStopBridge()
        self.cmd_status = CmdShowStatus()

        self.toolbar.addCommand("Hermes_StartBridge", self.cmd_start)
        self.toolbar.addCommand("Hermes_StopBridge", self.cmd_stop)
        self.toolbar.addCommand("Hermes_Status", self.cmd_status)

        # Menu
        Gui.addCommand("Hermes_StartBridge", self.cmd_start)
        Gui.addCommand("Hermes_StopBridge", self.cmd_stop)
        Gui.addCommand("Hermes_Status", self.cmd_status)

        # Status bar message
        FreeCAD.Console.PrintMessage(
            "[HermesFreeCADMCP] Workbench loaded. "
            "Use 'Start Bridge' to begin.\n"
        )

    def Activated(self):
        """Executed whenever the workbench is activated."""
        pass

    def Deactivated(self):
        """Executed when the workbench is deactivated."""
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


class CmdStartBridge:
    """Command to start the MCP bridge TCP server."""

    def Activate(self):
        if _bridge_instance.is_running:
            FreeCAD.Console.PrintWarning(
                "[HermesFreeCADMCP] Bridge already running on port %d\n"
                % MCP_BRIDGE_PORT
            )
            return

        _bridge_instance.start()
        FreeCAD.Console.PrintMessage(
            "[HermesFreeCADMCP] Bridge started on port %d. "
            "Hermes Agent can now connect.\n" % MCP_BRIDGE_PORT
        )
        return True

    def IsActive(self):
        return not _bridge_instance.is_running


class CmdStopBridge:
    """Command to stop the MCP bridge TCP server."""

    def Activate(self):
        if not _bridge_instance.is_running:
            FreeCAD.Console.PrintWarning(
                "[HermesFreeCADMCP] Bridge is not running.\n"
            )
            return

        _bridge_instance.stop()
        FreeCAD.Console.PrintMessage(
            "[HermesFreeCADMCP] Bridge stopped.\n"
        )
        return True

    def IsActive(self):
        return _bridge_instance.is_running


class CmdShowStatus:
    """Command to show bridge status in the FreeCAD console."""

    def Activate(self):
        status = _bridge_instance.get_status()
        FreeCAD.Console.PrintMessage(
            "[HermesFreeCADMCP] Status: %s\n" % status
        )
        return True

    def IsActive(self):
        return True


# Global bridge instance
_bridge_instance = MCPBridgeServer(port=MCP_BRIDGE_PORT)

# Register the workbench
Gui.addWorkbench(HermesMCPWorkbench())
