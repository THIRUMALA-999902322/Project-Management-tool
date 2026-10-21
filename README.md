# Fieldbook

Project tracking for civil engineering teams (water, sewer, drainage, paving; design and construction). Every person on a project gets an Assistant, an AI agent matched to their role. People send photos, voice notes, PDFs and texts; the Assistant turns them into evidence-backed reports. A manager approves, and approved history is locked for good, like ink in a surveyor's field book.

## What's here

| Path | What it is |
| --- | --- |
| `prototype/office.html` | Interactive prototype of the owner's home screen: the company as a live, animated office where every desk is a person's Assistant. |
| `prototype/preview.sh` | Wraps the prototype in a full HTML page so it opens straight from disk. |
| `docs/BRIEF.md` | The original design brief: principles, components, screens and sample data. |
| `docs/OFFICE_CONCEPT.md` | The office metaphor: what each thing in the scene means, what the owner can do, decisions taken, what's next. |

## Open the prototype

`prototype/office.html` is an HTML fragment (it is also published as a claude.ai Artifact, which adds the page skeleton). To open it locally:

```sh
prototype/preview.sh            # writes /tmp/fieldbook-office.html
open /tmp/fieldbook-office.html # or xdg-open on Linux
```

All names, numbers and files in it are sample data from the brief. Nothing is sent anywhere.
