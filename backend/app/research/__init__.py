"""Offline research: labelling, evaluation, and models.

Deliberately NOT under `app/services/`. Nothing in here may import the monitor,
the FastAPI app, or a job broker — it must be importable and runnable from a bare
Python process so that evaluation never depends on the live trading path being up.

The one-line summary of why this package exists: the previous "learning" layer
scored setups it had never seen the outcome of, was promoted by a test a constant
predictor wins, and then held a veto over arming. Everything here is built so that
each of those three failures is impossible by construction rather than by care.
"""
