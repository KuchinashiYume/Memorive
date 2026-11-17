"""The desktop app's own code: the window's API, settings, the background service and the pages.

  api/               ProductApi, which receives every call from the window and passes it on
  settings/          the settings service: stored settings, credentials, model nodes and profiles
  service/           the background service that runs jobs, and the window's client for it
  assistant/         the floating desk assistant
  current_task/      the current-task page: jobs, refinement tasks and their controls
  messages/          the message center
  work_log/          the work log
  accounting/        model cost records shown to the user
  test_console/      the bridge the test console uses to drive the app
  harness/           an integration harness run by hand
  release_links.py   links to the release pages
"""
