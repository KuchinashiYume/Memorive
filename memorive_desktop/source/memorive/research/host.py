"""What the research runtime uses from the rest of the app, in one object.

ProductApi (memorive/desktop/api.py) builds one ResearchHost when it starts and passes it to ResearchRuntime;
the runtime and its helpers (home.py, briefing/jobs.py, discovery/indexing.py, reports/periodic.py,
reports/models.py and the others) reach other parts of the app only through these fields.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class ResearchHost:
    service: Any
    """The settings service: service.call('settings.research_get' / 'list_jobs' / 'get_job' ...)."""
    library: Any
    """The library controller: cached_rows, get_artifact, core_activity_rows, refinement_activity_rows."""
    messages: Any
    """The messages controller: post_event adds a message (discovery done, report ready, run failed)."""
    work_log: Any
    """The work log controller: age_bucket groups the research runs shown in the work log."""
    local_models: Any
    """The local models controller, for the model profiles reports and the AI briefing may use."""
    profile_path: Callable[[str], Path]
    """The folder of a profile area: 'WORKSPACE', 'INBOX', 'Core_JOBS'."""
    desktop_call: Callable[[str, dict], Any]
    """ProductApi.call, for methods another area answers (work_log.list_entries)."""
    open_target: Callable[[dict], Any]
    """Open a web page or file for the user (ProductApi.assistant_launch_target)."""
    library_job_rows: Callable[[], list]
    """Document processing jobs with their states, for the periodic reports."""
    notify_window: Callable[[str], None]
    """Dispatch a named event on the page, such as 'desktop:research-published'."""
    research_workspace: Callable[[], Any]
    """The research workspace if the window has opened it, else None."""
