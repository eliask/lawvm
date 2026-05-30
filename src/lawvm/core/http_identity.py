"""Shared HTTP identity for LawVM source-acquisition clients.

A single canonical User-Agent (and homepage URL) used by every LawVM fetcher, so
the project presents one identity to source servers and the homepage lives in one
place. Frontends that must advertise a data-source-specific contact URL (e.g. the
EU Cellar or legislation.gov.uk bootstrap) keep their own string; everything that
identified LawVM generically should import ``LAWVM_USER_AGENT`` from here.
"""
from __future__ import annotations

LAWVM_HOMEPAGE = "https://lawvm.org"
LAWVM_USER_AGENT = f"LawVM/1.0 (+{LAWVM_HOMEPAGE})"

__all__ = ["LAWVM_HOMEPAGE", "LAWVM_USER_AGENT"]
