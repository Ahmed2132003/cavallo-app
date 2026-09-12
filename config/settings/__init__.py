# Intentionally empty.
#
# This package holds the environment-specific settings modules
# (base.py, dev.py, staging.py, prod.py). Nothing is re-exported here —
# DJANGO_SETTINGS_MODULE must point at one of the concrete modules
# (e.g. "config.settings.dev"), never at "config.settings" alone.