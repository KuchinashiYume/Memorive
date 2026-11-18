"""What the page check opens, in order.

A mode fixes the language, the font size and the window size. In the five view modes the walk opens the home
view, each route, each settings category, and on each route the right panel and the bottom panel (opened,
recorded, closed again). The last mode, "actions", runs in Chinese after them and does what a person does with the
page: opens records, edits and saves settings, arms and cancels buttons that ask for a second click, and opens
dialogs and menus. It runs last because it changes saved settings.

A step is a JavaScript expression run in the page, or "wait N" to let N seconds pass. The steps use the page's own
entry points (window.Memorive?.app, the visible buttons, the window chrome). When a later change
renames one of those, change it here too, the same for the run before and the run after.

A scene whose name ends in "+armed" shows a countdown; compare.py ignores the digits of its texts.
"""
import json

ROUTES = ["inbox", "current-task", "messages", "sessions", "library", "work-log", "leaderboard", "chat", "settings"]
SETTINGS = ["user", "api", "cli", "local-models", "workflow", "research-workspace", "research-weights", "refinement",
            "viewer", "external-data", "literature", "notice", "behavior", "network", "privacy", "appearance", "update"]

MODES = {
    "zh": {"language": "zh-CN", "font": 100, "size": (1440, 900)},
    "en": {"language": "en-US", "font": 100, "size": (1440, 900)},
    "ja": {"language": "ja-JP", "font": 100, "size": (1440, 900)},
    "zh130": {"language": "zh-CN", "font": 130, "size": (1440, 900)},
    "narrow": {"language": "zh-CN", "font": 100, "size": (960, 640)},
    "actions": {"language": "zh-CN", "font": 100, "size": (1440, 900)},
}

APP = "window.Memorive?.app"
VISIBLE = '.desktop-route-surface:not([hidden]) '


def activate(route):
    return f"{APP}.activate({json.dumps(route)}); 'ok'"


def click(selector):
    return f"window.__check.click({json.dumps(selector)})"


def type_text(selector, text):
    return f"window.__check.type({json.dumps(selector)}, {json.dumps(text)})"


def key(name):
    return f"window.__check.key({json.dumps(name)})"


def run(expression):
    return f"{expression}; 'ok'"


def preferences(mode):
    values = {"language": mode["language"], "font_scale_percent": mode["font"]}
    return f"{APP}.applyPreferences({json.dumps(values)}); 'ok'"


def walk(mode_name):
    """Yield (scenario name, [steps], plain) for one mode; plain marks the route views that get a picture by
    default. Each scenario starts where the previous one left off."""
    if mode_name == "actions":
        yield from actions(mode_name)
        return
    yield f"{mode_name}/home", [activate("home")], True
    for route in ROUTES:
        yield f"{mode_name}/{route}", [activate(route)], True
    for category in SETTINGS:   # the last route opened above is settings
        yield f"{mode_name}/settings.{category}", [click(f'{VISIBLE}.desktop-settings-category[data-category="{category}"]')], False
    for route in ROUTES:
        yield f"{mode_name}/{route}+right", [activate(route), click(VISIBLE + "#desktop-toggle-right")], False
        yield f"{mode_name}/{route}+right+closed", [click(VISIBLE + "#desktop-toggle-right")], False
        yield f"{mode_name}/{route}+bottom", [click(VISIBLE + "#desktop-toggle-bottom")], False
        yield f"{mode_name}/{route}+bottom+closed", [click(VISIBLE + "#desktop-toggle-bottom")], False


def actions(m):
    """What a person does with the page, on the records seed.py put in."""
    right = VISIBLE + "#desktop-toggle-right"
    # Records and their details.
    yield f"{m}/inbox.detail", [activate("inbox"), click(VISIBLE + ".desktop-inbox-card:not(.new-file)")], True
    yield f"{m}/inbox.detail+closed", [click(right)], False
    yield f"{m}/messages.detail", [activate("messages"), click(VISIBLE + ".desktop-messages-card .desktop-messages-copy")], True
    yield f"{m}/messages.detail+closed", [click(right)], False
    yield f"{m}/sessions.detail", [activate("sessions"), click(VISIBLE + ".desktop-sessions-card")], True
    yield f"{m}/sessions.detail+closed", [click("#sessions-close")], False
    # A setting: edit, revert; edit, save, read back.
    user = VISIBLE + '.desktop-settings-category[data-category="user"]'
    yield f"{m}/settings.user+edited", [activate("settings"), click(user), type_text("#settings-username", "样例用户")], True
    yield f"{m}/settings.user+reverted", [click("#settings-revert")], False
    yield f"{m}/settings.user+saved", [type_text("#settings-username", "样例用户"), click("#settings-save")], False
    yield f"{m}/settings.user+reloaded", [run("window.Memorive?.settingsBridge.refresh()")], False
    # Restoring a category's defaults counts down; a second click cancels, waiting it out restores.
    appearance = VISIBLE + '.desktop-settings-category[data-category="appearance"]'
    yield f"{m}/settings.reset+armed", [click(appearance), click("#settings-reset")], False
    yield f"{m}/settings.reset+cancelled", [click("#settings-reset")], False
    yield f"{m}/settings.reset+done", [click("#settings-reset"), "wait 11"], False
    # The dialog that asks before the window closes.
    yield f"{m}/close-dialog", [run("window.Memorive?.windowChrome.requestClose(true)")], True
    yield f"{m}/close-dialog+cancelled", [click("[data-window-action=cancel]")], False
    # Menus and popovers.
    yield f"{m}/work-log.filter", [activate("work-log"), click(VISIBLE + "#worklog-filter-button")], False
    yield f"{m}/work-log.filter+closed", [click(VISIBLE + "#worklog-filter-button")], False
    # A button that asks for a second click: the inbox detail's delete button, armed and then let go.
    yield f"{m}/inbox.delete+armed", [activate("inbox"), click(VISIBLE + ".desktop-inbox-card:not(.new-file)"),
                                      click(VISIBLE + "#inbox-delete-task")], False
    yield f"{m}/inbox.delete+cancelled", [key("Escape")], False
