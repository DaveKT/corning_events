"""Aggregates public events around Corning, New York into iCal feeds.

Pipeline order, driven by main.py: fetch, persist, dedupe, resolve, detect
cancellations, filter and emit, validate. See plans/ for the build plan.
"""

__version__ = "0.1.0"
