# pvc_app/utils.py
"""Shared utility helpers used across the app."""


def safe_float(x):
    """Convert x to float safely; return 0.0 on any failure."""
    try:
        return float(str(x or 0).replace(",", "").strip())
    except Exception:
        return 0.0


def safe_round(x, n=2):
    """Round x to n decimals; return None on failure."""
    try:
        return round(float(x), n)
    except Exception:
        return None
