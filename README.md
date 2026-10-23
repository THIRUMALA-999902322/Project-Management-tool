# Fieldbook

Project tracking for civil engineering teams (water, sewer, drainage, paving; design and construction). Every person on a project gets an Assistant, an AI agent matched to their role. People send photos, voice notes, PDFs and texts from their phone; the Assistant turns them into evidence-backed reports. A manager approves, and approved history is locked for good, like ink in a surveyor's field book.

The Assistant runs on **open-weight models you host yourself** (Ollama, vLLM, LM Studio, llama.cpp). There is no paid API and no per-token bill. With no model configured, a built-in rules engine does the work.

## Run it

Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
make setup        # install
make demo         # load the sample company and start http://localhost:8000
```

`make demo` prints links:

- **Owner login.** The office app: an overview of what needs attention, an approvals queue with each report's evidence and the Assistant's review, projects with their locked history, jobs, people, and an Ask page.
- **Field app links**, one per person. Open J. Ortiz's link in a phone-sized window, send a photo and "Laid 80 feet, 15+20 to 16+00", then press Send report. It shows up in the office's approvals queue straight away; approve it and Line A's progress moves.

```sh
make test                          # ruff + pytest
uv run fieldbook verify            # check the ledger's hash chain
uv run fieldbook link "J. Ortiz"   # new field link for someone
uv run fieldbook doctor            # is the model server up, and are the models pulled?
uv run fieldbook usage             # model calls and tokens by purpose
```

API docs are served at `/docs` while the server runs.

## Use a local model (free, runs on your machine)

1. Install [Ollama](https://ollama.com) and pull a model that reads photos as well as text:

   ```sh
   ollama pull qwen2.5vl:7b        # ~6 GB; or gemma3:12b if you have ~10 GB of RAM/VRAM to spare
   ```

   Model names change; check the Ollama library for the current tag of a vision model.

2. Start Ollama with a context window big enough for a day's messages and a page of plans. Its default is small, and it cuts long prompts without saying so:

   ```sh
   OLLAMA_CONTEXT_LENGTH=8192 ollama serve
   ```

3. Tell Fieldbook which model to use (copy `.env.example` to `.env`):

   ```sh
   FIELDBOOK_LLM_MODEL=qwen2.5vl:7b
   ```

4. `uv run fieldbook doctor` should say the server is serving the model. Start Fieldbook with `make serve`; the sidebar shows **Local model · qwen2.5vl:7b**.

Any OpenAI-compatible server works the same way: set `FIELDBOOK_LLM_BASE_URL` (vLLM, LM Studio and llama.cpp examples are in `.env.example`). A text-only model works too: if the server rejects images, Fieldbook drops the photos for that model and keeps going. You can also point the photo work at a separate model with `FIELDBOOK_LLM_VISION_MODEL`.

What the model does and doesn't do:

- It **reads and judges**: what a message or photo says, which stations, what a scope PDF lists, what the photos actually show.
- It **never sets a number on its own**. Quantities and stations a person typed win over the model's reading. Plan quantities that aren't printed in the documents are blanked for you to fill in. Progress is arithmetic in code, from approved reports only.
- If the server is down, slow, or replies with something that isn't valid JSON (after one retry), that report falls back to the rules engine. Nothing blocks.

## What's here

| Path | What it is |
| --- | --- |
| `src/fieldbook/` | The MVP: FastAPI app, models, append-only ledger, Assistant layer, CLI. |
| `src/fieldbook/assistant/llm.py` | The only module that talks to a model (OpenAI-compatible Chat Completions). |
| `src/fieldbook/web/app.*` | The office app (owner and project managers): sidebar layout, approvals, projects, jobs, people, Ask. |
| `src/fieldbook/web/field.*` | The field app (everyone else): job, today's draft, a message thread, offline queue. |
| `tests/` | Ledger integrity, rules engine, end-to-end API flows, and the model client against a simulated OpenAI-compatible server. |
| `docs/ARCHITECTURE.md` | How it fits together, the rules the code enforces, decisions, known limits. |
| `docs/BRIEF.md` | The original design brief. |

All names, numbers and files in the seed are sample data from the brief.
