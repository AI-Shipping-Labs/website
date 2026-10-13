"""Skill-gap page: maps a member's background to platform next steps (#1716).

Stateless, deterministic, GET-only page at ``/skill-gap`` for any signed-in
member. The data lives in ``skill_gap/data/backgrounds.yaml``; the app has
no models and writes nothing to the database.
"""
