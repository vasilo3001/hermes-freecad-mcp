#!/usr/bin/env bash
#
# Hermes FreeCAD MCP — One-command installer
# Usage: ./install.sh
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADDON_SRC="$SCRIPT_DIR/freecad_addon/hermes_freecad_mcp"

# ── Detect OS ────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
    Linux*)  FREECAD_MOD_DIR="$HOME/.local/share/FreeCAD/Mod" ;;
    Darwin*) FREECAD_MOD_DIR="$HOME/Library/Application Support/FreeCAD/Mod" ;;
    *)
        echo "ERROR: Unsupported OS: $OS"
        echo "Please manually copy the addon to your FreeCAD Mod directory."
        exit 1
        ;;
esac

echo "══════════════════════════════════════════════"
echo "  Hermes FreeCAD MCP — Installer"
echo "══════════════════════════════════════════════"
echo ""

# ── Check FreeCAD ────────────────────────────────────
FC_FOUND=false
if command -v freecad &>/dev/null; then
    FC_VERSION=$(freecad --version 2>&1 | head -1 || echo "unknown")
    echo "✓ FreeCAD found (system): $FC_VERSION"
    FC_FOUND=true
elif flatpak list 2>/dev/null | grep -qi freecad; then
    FC_VERSION=$(flatpak list 2>/dev/null | grep -i freecad | awk '{print $3}')
    echo "✓ FreeCAD found (Flatpak): v${FC_VERSION}"
    FC_FOUND=true
elif [[ -d "$HOME/.local/share/FreeCAD" ]]; then
    echo "✓ FreeCAD data directory found (checking version...)"
    FC_FOUND=true
else
    echo "⚠ FreeCAD not found."
    echo "  Install it first:"
    echo "    Ubuntu/Debian: sudo apt install freecad"
    echo "    Flatpak:       flatpak install flathub org.freecad.FreeCAD"
    echo "    macOS:         brew install --cask freecad"
    echo "    Or:            https://www.freecad.org/downloads.html"
    echo ""
    read -p "Continue anyway? [y/N] " -n 1 -r
    echo
    [[ $REPLY =~ ^[Yy]$ ]] || exit 1
fi

# ── Check Python + mcp ───────────────────────────────
PYTHON=$(command -v python3 || command -v python)
if [[ -z "$PYTHON" ]]; then
    echo "✗ Python 3 not found. Install Python 3.10+ first."
    exit 1
fi
echo "✓ Python: $($PYTHON --version 2>&1)"

if ! $PYTHON -c "import mcp" 2>/dev/null; then
    echo "⚠ 'mcp' package not installed. Installing..."
    $PYTHON -m pip install mcp
    echo "✓ mcp installed"
else
    echo "✓ mcp package already installed"
fi

# ── Install FreeCAD Addon ────────────────────────────
echo ""
echo "Installing FreeCAD addon to: $FREECAD_MOD_DIR"
mkdir -p "$FREECAD_MOD_DIR"
cp -r "$ADDON_SRC" "$FREECAD_MOD_DIR/"
echo "✓ Addon installed"

# ── Print Hermes config ──────────────────────────────
MCP_SERVER_PATH="$SCRIPT_DIR/mcp_server.py"
echo ""
echo "══════════════════════════════════════════════"
echo "  Next Steps"
echo "══════════════════════════════════════════════"
echo ""
echo "1. Add this to ~/.hermes/config.yaml:"
echo ""
echo "   mcp_servers:"
echo "     freecad:"
echo "       command: \"$PYTHON\""
echo "       args: [\"$MCP_SERVER_PATH\"]"
echo "       timeout: 120"
echo ""
echo "2. Launch FreeCAD"
echo "3. Switch to 'Hermes MCP' workbench"
echo "4. Click 'Start Bridge'"
echo "5. Restart Hermes Agent"
echo ""
echo "Then just ask Hermes to create something in FreeCAD!"
echo ""
echo "Test:  Ask Hermes 'Create a 50mm box in FreeCAD'"
echo ""
