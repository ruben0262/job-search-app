"""Shared test fixtures/paths.

Puts the repo root on sys.path so tests can import `examples.client` (the reference caller) and
`gateway.*` without an install step.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
