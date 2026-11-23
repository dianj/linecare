"""Mock enterprise systems of the fictional operator Quillnet: customers, network, diagnostics, billing, scheduling.

Deterministic and seeded (`seed.py`), so tests and evals are reproducible. The app calls the service layer
(`service.py`) in-process; `app.py` exposes the same functions as a REST API under /mock for inspection.
"""
