"""Literature discovery, the AI briefing, research reports on a schedule, and the home page summary.

The window reaches it through research.* (runtime.py: discovery runs), ai.* (ai_briefing.py) and
home.* (home.py) methods. discovery.py, slate.py and identity.py find and rank candidate papers;
transport.py and wos.py fetch public metadata; periodic.py and report_schedule.py produce reports
when they are due; report_models.py lists the model profiles research may choose. routes.py
answers the messages and current-task requests about research runs. ProductApi gives the package a
ResearchHost (host.py), the only way it reaches the settings service, library, messages, work log
and window.
"""
