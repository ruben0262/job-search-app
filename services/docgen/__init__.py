"""CV and cover-letter builds. **Chrome lives only here** — that is why it is its own container.

Isolated for two measured reasons: builds are slow (~90–130 s each) and Chrome is spiky in memory
on a box where a memory crunch has already caused one incident. Keeping it out of every other
image also keeps ``api`` and ``notifier`` small and fast to rebuild (CODE_STRUCTURE.md §5).

**Generated documents are ephemeral** (decision D9 / GAPS_REVIEW G8): built, delivered, discarded.
The applied-log is the durable record. No object storage, no retention policy, and no user CVs
persisting under our control — which is also the smallest possible GDPR surface for the most
personal artifact the platform touches. Scratch directories are per-user and swept.

Port of ``pipeline/cv_builder.py`` and ``cv_eval.py``; the generation prompts it reads live in
``libs.prompts`` and are SACRED (CODE_STRUCTURE.md §7.4).

Restart blast radius: CV builds queue up.
"""
