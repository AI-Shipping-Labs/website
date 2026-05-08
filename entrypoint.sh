#!/bin/sh
# Container entrypoint. Hands off to a single Python process that
# imports Django settings ONCE and runs migrate / check before serving
# (gunicorn for web, qcluster for worker). The django_q_cache table is
# created by an email_app migration during migrate, not on every boot.
#
# History: previously this was three separate `manage.py` invocations
# followed by `exec gunicorn`. Each subprocess re-imported
# `website/settings.py` and re-paid the eager AWS-network cost
# (Secrets Manager + RDS DatabaseCache + IntegrationSetting query),
# adding ~30s of cold-start that raced the ALB unhealthy-threshold.
# `scripts/entrypoint_init.py` collapses the steps into one interpreter.
exec uv run python -m scripts.entrypoint_init
