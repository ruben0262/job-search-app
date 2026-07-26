"""Pydantic models shared *between* services, plus their JSON Schema export.

Exists so a shape is defined once and cannot drift. Two consumers depend on that:

- **Service-to-service calls.** Every service exposes ``GET /health`` with a version, and callers
  assert compatibility and fail closed rather than guessing (PLATFORM_ARCHITECTURE.md §5). The
  embedder's ``(model, dim, version)`` assert is the pattern being generalised.
- **The config form.** ``config.py`` here becomes the single Pydantic model behind both the
  server's validation and the React form's fields, exported as JSON Schema
  (TENANT_SAFETY.md §4, CODE_STRUCTURE.md §8). A hand-written second copy in TypeScript is how
  frontend and backend silently disagree.

Migration map (CODE_STRUCTURE.md §3): ``pipeline/lib/config.py`` → ``config.py``;
``scorer/embedder_client.py`` → ``embedder.py`` (already contract-shaped).
"""
