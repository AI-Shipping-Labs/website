"""Serializer functions for the JSON API (issue #433).

Pure-Python serializers (no DRF) that convert ``plans`` model instances
into the response dicts documented in the spec. Reused from list and
detail endpoints so the wire shape lives in one place.
"""
