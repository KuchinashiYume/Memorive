"""Explicit fixed text selection. Dynamic user and source strings are never translated."""
from memorive.platform.language.policy import locale

def choose(language, zh, en, ja):
    return {'zh-CN': zh, 'en-US': en, 'ja-JP': ja}[locale(language)]
