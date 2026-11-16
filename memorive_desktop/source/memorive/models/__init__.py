"""Model requests: who a model is, what a call may ask for, how calls are sent and recorded.

identity.py names the vendor and base model behind a model id; output_limits.py is the rule for how
large a reply may be. The call ledger, endpoints, command-line templates, model validation and model
execution move here from memorive_settings and memorive_model_execution during v2.00.
Nothing here imports the settings, the exams or the desktop.
"""
