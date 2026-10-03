"""Opt-in stages that condense structured chart content (imaging, labs, meds, vitals)
and add context (hospital day). Each module exposes ``DEFAULTS`` and a
``run(text, cfg, ctx) -> (text, count, details)`` stage runner; all are off by
default so existing output never changes until a user turns one on.
"""
