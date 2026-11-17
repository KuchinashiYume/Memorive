"""ProductApi: every call from the window arrives here and goes to the part that owns it
(settings, inbox, library, sessions, research, messages, the desk assistant ...). It starts and
watches the background service (ServiceProcessManager) and builds the page controllers at start.
product/desktop/app.py creates it.

  common.py             the method sets the window may call, the service IPC names, and helpers
                        for terminal-job presentations, the source import path and the shutdown order
  service_process.py    ServiceProcessManager: starts, calls and stops the background service
  page_events.py        pushing changes to the page: the Core job event pump, sample rows, and the
                        refinement observers that refresh the page bridges
  assistant.py          the desk assistant's window calls, forwarded to AssistantDesktop
  notifications.py      Windows notifications and bringing the main window to the front
  window.py             the main window and the app's lifetime: attach, close and exit, window state,
                        panel layout, the control state
  inbox_files.py        files the window picks or drops, and the inbox items it previews and opens
  research_memo.py      the research workspace's desktop actions: vault, materials, evidence and
                        handing work to desktop agent apps
  artifacts.py          opening and locating library and work-log files in Explorer
  settings_page.py      the settings page's desktop actions: console connection, directories,
                        external library test, import and export files, readiness, logs and cache
  browser_companion.py  the browser companion's window calls, forwarded to BrowserCompanionDesktop
  links.py              the project page, the developer SDK folder, local help and locators
  product_api.py        ProductApi: construction, preferences, dispatch, call(), status and close;
                        everything else comes from the mixins above

ProductApi, ServiceProcessManager and the method sets stay importable from this package.
"""
from __future__ import annotations

from memorive.desktop.api.common import (
    ALLOWED_EVENTS,
    QUERY_METHODS,
    SETTINGS_METHODS,
    ACCOUNTING_METHODS,
    REVIEW_METHODS,
    INBOX_METHODS,
    CURRENT_TASK_METHODS,
    MESSAGES_METHODS,
    SESSIONS_METHODS,
    LOCAL_MODEL_METHODS,
    ASSISTANT_PREFERENCE_METHODS,
    LIBRARY_METHODS,
    WORK_LOG_METHODS,
    SECRET_ENV_NAME,
    SERVICE_REVISION,
    core_terminal_transition_presentations,
    process_shutdown_level,
)
from memorive.desktop.api.service_process import (
    ServiceProcessManager,
)
from memorive.desktop.api.product_api import (
    ProductApi,
)

__all__ = [
    "ALLOWED_EVENTS",
    "QUERY_METHODS",
    "SETTINGS_METHODS",
    "ACCOUNTING_METHODS",
    "REVIEW_METHODS",
    "INBOX_METHODS",
    "CURRENT_TASK_METHODS",
    "MESSAGES_METHODS",
    "SESSIONS_METHODS",
    "LOCAL_MODEL_METHODS",
    "ASSISTANT_PREFERENCE_METHODS",
    "LIBRARY_METHODS",
    "WORK_LOG_METHODS",
    "SECRET_ENV_NAME",
    "SERVICE_REVISION",
    "core_terminal_transition_presentations",
    "process_shutdown_level",
    "ServiceProcessManager",
    "ProductApi",
]
