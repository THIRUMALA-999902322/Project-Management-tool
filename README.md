# Fieldbook

Project tracking for civil engineering teams (water, sewer, drainage, paving; design and construction). Every person on a project gets an Assistant, an AI agent matched to their role. People send photos, voice notes, PDFs and texts; the Assistant turns them into evidence-backed reports. A manager approves, and approved history is locked for good, like ink in a surveyor's field book.

## Run the MVP

Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
make setup        # install
make demo         # load the sample company and start http://localhost:8000
```

`make demo` prints links:

- **Owner login.** Open it to see the live office: every desk is a person's Assistant. Hire and remove people, change roles, assign jobs, set up a project from its bid form, approve reports.
- **Field app links**, one per person. Open J. Ortiz's link in a phone-sized window, send a photo and "Laid 80 feet, 15+20 to 16+00", then press Send report. The office animates it into the in-tray; approve it and Line A's progress moves.

The Assistant runs on a built-in rules engine until you set `ANTHROPIC_API_KEY` (see `.env.example`); then it uses Claude to read photos, PDFs and messages. Everything else works the same.

```sh
make test         # ruff + pytest
uv run fieldbook verify    # check the ledger's hash chain
uv run fieldbook link "J. Ortiz"   # new field link for someone
uv run fieldbook usage     # Claude token use by purpose
```

API docs are served at `/docs` while the server runs.

## What's here

| Path | What it is |
| --- | --- |
| `src/fieldbook/` | The MVP: FastAPI app, models, append-only ledger, Assistant layer, CLI. |
| `src/fieldbook/web/` | The office app (owner and PMs) and the field app (everyone else). |
| `tests/` | Ledger integrity, rules engine, the Claude client (with a fake SDK), and end-to-end API flows. |
| `prototype/office.html` | The original clickable design prototype (sample data only). |
| `docs/ARCHITECTURE.md` | How it fits together, the rules the code enforces, decisions, known limits. |
| `docs/OFFICE_CONCEPT.md` | The office metaphor and what each thing in the scene means. |
| `docs/BRIEF.md` | The original design brief. |

All names, numbers and files in the seed are sample data from the brief.
