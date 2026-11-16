"""The research area: literature discovery, recommendations, reports, analysis and retrieval.

  workspace/          the research workspace: projects, questions, answers, and the agent entry
  discovery/          literature connectors and bibliographic identity resolution
  recommendations/    exposure feedback, candidate staging and card promotion
  reports/            research reports: sources, rendering, the report command line
  analysis/           the analysis of a question against a paper's cards (AnalysisResult)
  retrieval/          finding the cards and chunks for a question (ContextPack)
  ranking/            field vectors, candidate indexes and the weights that rank candidates
  opportunities/      research opportunities found in answers
  knowledge_feedback/ knowledge drafts from answers, and their review
  briefing/           the AI briefing: its jobs, the official sources it reads, its prompt

runtime.py runs literature discovery, periodic reports and the AI briefing, one at a time (research.*,
ai.* and home.* from the window); host.py is what it uses from the rest of the app, routes.py what it
answers itself, home.py the home page summary, task_projection.py its task cards and common.py its
state files and settings.
"""
