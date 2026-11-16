"""Which language new content is written in: answers, messages, reports, refinements.

Source material and machine keys are never translated. policy.py makes the decision; the other
modules apply it per feature. Most packages that write text for the user use it. source.py tells
a source text's language; source_preservation.py keeps text added by a model or template out of
an artifact whose source is in another script.
"""
from memorive.platform.language.policy import freeze, from_settings, locale, instruction, validate

__all__ = ['freeze', 'from_settings', 'locale', 'instruction', 'validate']
