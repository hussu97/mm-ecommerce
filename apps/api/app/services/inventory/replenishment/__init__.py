"""Replenishment forecast for the transfer & production form, and its shadow history.

A visual guide only: nothing here creates or changes a transfer, a production
order or stock. ``engine`` is the pure forecast; ``facts`` builds the per-day
demand observations it learns from; ``loaders`` assembles a point-in-time
snapshot; ``history`` stores the daily snapshot and scores it against what was
actually done.
"""
