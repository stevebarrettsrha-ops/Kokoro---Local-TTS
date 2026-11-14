"""Setup and launch steps shared by start.sh (Linux, macOS) and start.bat / start.ps1 (Windows).

The shell launchers only make the Python environment (.venv); everything after that is here,
written once in Python so it behaves the same on every system.
"""
