"""Shared code — the only package a service is allowed to import from.

One dependency arrow, one direction (CODE_STRUCTURE.md §1)::

    services/*  ──imports──▶  libs/*
         │                        │
         └── never ───────────────┘   libs never imports services
         └── never imports another service

Services reach each other over HTTP or the queue, never by import. Without that rule eight
"independently deployable" containers are one program in eight folders — deployable separately on
paper, coupled in practice. It is enforced by import-linter in CI (`pyproject.toml`), not by
discipline, because a rule a linter checks survives a late night and a rule in a document does not.

Code here must therefore stay free of service-specific assumptions: no reading a service's env
vars, no reaching for a service's database session, no knowing which container it runs in.
"""
