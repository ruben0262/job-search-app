"""Generation prompts and the label/rubric templates. **SACRED** — change control applies.

Carried over unchanged from the reference instance (CODE_STRUCTURE.md §7.4): no edit without
explicit approval, A/B measured, changelogged. The rule now also covers ``label_template.md``,
because editing that template re-versions **every user** — the template hash is what scopes each
user's training data, so a casual wording change silently invalidates labels and models across the
whole platform at once.

Prompt text is data, not code. It lives in ``libs`` so trainer, scorer, docgen, and capture all
read the same bytes rather than each shipping a copy that drifts.
"""
