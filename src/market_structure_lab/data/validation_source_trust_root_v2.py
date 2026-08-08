"""Reviewed verifier-evidence trust root for Phase 5 source admission.

This module is intentionally separate from the verifier implementation.  A
reviewed verifier-evidence digest can therefore be bound in a later checkpoint
without changing the verifier code whose bytes that evidence authenticates.
"""

from __future__ import annotations


TRUSTED_VERIFIER_EVIDENCE_SHA256_V2: frozenset[str] = frozenset()
