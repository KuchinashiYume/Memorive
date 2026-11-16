"""Which language new content is written in: answers, messages, reports, refinements.

Source material and machine keys are never translated. policy.py makes the decision; the other
modules apply it per feature. Most packages that write text for the user use it.
"""
from .policy import freeze, from_settings, locale, instruction, validate

__all__ = ['freeze', 'from_settings', 'locale', 'instruction', 'validate']
