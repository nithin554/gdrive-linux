"""Shared fixtures and configuration for all tests."""

import os
import sys

# Ensure the project root is on sys.path so imports work
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
