# retired — what the project no longer runs, kept to read

Moved out of `libs/`, `services/`, `deploy/` and `tests/` on 2026-11-03 (owner), so those folders hold
only what runs. Nothing here is imported, built, deployed or tested: `pytest tests/` is the suite, and
ruff and the import contracts skip this folder. The paths below mirror where each file used to be.

| Here | What it was | Why it stopped |
|---|---|---|
| `services/embedder/`, `deploy/compose.embedder.yml`, `deploy/docker/Dockerfile.embedder-gateway` | The shared embedding service (BGE behind HF TEI and a FastAPI gateway) | Nothing embeds: jobs are matched on their words since the learned model was switched off (2026-10-28) |
| `libs/contracts/embedder.py`, `libs/corpus/embedtext.py`, `tests/test_embedtext.py` | The embedder's client, and the text a job was embedded from, with its tests | The same |
| `libs/contracts/profile.py` | The captured profile schema the interview produced | The profile is files now (`libs/profiles`); only a test still built one, for a name |
| `services/harvester/sources/port_from_reference.py` | The one-time tool that copied the source adapters from the reference instance (2026-07-27) | Done; the adapters' docstrings still point here for how they were made |

The rest of the switched-off model (training, scoring, labelling, the setup agents) is not here: it was
removed on 2026-10-30 and is kept in git under the tag `model-on-postgres` (CLAUDE.md §3).

**Bringing something back** means moving it to where it was, giving it tests in `tests/`, and porting
it to whatever it now depends on: the model, for one, was written for Postgres.
