# Fieldbook Office: the owner's home screen

The first brief (`docs/BRIEF.md`) described Fieldbook as a calm, chat-first PM tool. This round changes the **owner's home screen**. The owner should see the company as an illustrated office, not as a list.

## The metaphor

| In the office | What it is in Fieldbook |
| --- | --- |
| A desk with a person at it | That person's **Assistant**. It is an AI agent working for a real person, not the person. |
| Yellow headset with a blinking light | The Assistant is linked to the real person's phone or laptop. |
| A card dropping onto a desk ("2 photos · from J. Ortiz's phone") | The real person sent something: a photo, voice note, file or text. |
| Speech bubble over a desk | What the Assistant is doing now, in plain words ("Submitted · waiting for Dana"). |
| Folder flying to the PM's desk | A report going into the in-tray. |
| In-tray on the PM's side table | Reports waiting for approval. Folder count = reports. |
| Rubber stamp + lock | Approval. Permanent; corrections are amendments, never erasures. |
| Plan room cork board | One pinned sheet per project, with approved %. |
| Plan table + filing cabinet | Where a new project's scope documents get read and turned into a plan. |
| Site trailer on the job site | Field roles (crew leads, inspectors). Their Assistants sit near the work. |
| Contractor gate | External contractors: own scope only. |
| Lobby door + elevator, pickup truck | How new hires arrive (office roles through the lobby, field roles by truck). |
| Open desk (dashed nameplate) | A seat left by someone who was removed. Tap it to fill it. |
| Night windows, lamp glow | Dark mode. |

## What the owner can do from the office

- **Watch it live.** Every few seconds a real person sends something, an Assistant reacts, a report travels to the in-tray. The rail on the right keeps a time-stamped field book of it all. A Live/Paused switch stops the motion.
- **Tap any desk** to open that person's drawer:
  - a relay diagram (real person → Assistant → approver), so it is clear the desk is an agent;
  - what the Assistant is doing and the latest inputs from the real person;
  - jobs with the two-part progress bar, **Add a job** (pick an existing job or make a new one with a measurement method), **Take off this job**;
  - **Role** cards that say what the Assistant can do and what needs approval. Changing role re-dresses the character (hard hat and vest for field roles) and walks them to the right room;
  - name and contact for the field app;
  - **Remove from office** with a confirm; the character waves, walks out, and leaves an open desk. Their approved history stays locked; their jobs show "needs someone".
- **Add a person** (top bar, the "Now hiring" sign in the lobby, or an open desk): name, role, phone/email, starting jobs. The new hire walks in and sits down; the Assistant says hello.
- **New project**, with an animation the owner can watch:
  1. Name, client, kind of work.
  2. Pick the team.
  3. Drop scope documents (duplicates are skipped and said so).
  4. Documents fly into the filing cabinet, a counter reads the pages, the blueprint unrolls on the plan table, sticky notes with proposed quantities pop onto the board and fold into a new pinned sheet. Then the plan review opens: every item has a quantity, who reports it, and a source chip. **Approve plan** stamps and locks it, and job tags fly to each person's desk.
- **Open the in-tray**: Routine / Needs attention / Flagged, each with the Assistant's verdict, evidence chips and a review brief ("Claimed" vs "Evidence shows", "Checked against the original files, not the summary"). Approve (with the permanent-record confirm and stamp), Ask (suggested questions), Request changes (pre-filled from the Missing list), and batch-approve routine items.
- **Tap your own desk** to ask your Assistant questions across all jobs, with source chips.
- **Tap a project sheet** for jobs, people and the locked history; amend an entry with a required reason and see the original struck through above it.

## Decisions taken (2026-10-21)

| # | Decision | Reason |
| --- | --- | --- |
| O1 | The office replaces the owner's home screen only. Worker app (W1–W6) and the manager's Inbox/Review keep the brief's layouts. | Workers in the field need speed, not a scene. The office is for the person who wants the whole company at a glance. |
| O2 | Each desk shows the person's character with a yellow headset, not a robot. | The owner thinks in people. The headset, the relay diagram and the "from J. Ortiz's phone" packets make the agent part clear without turning staff into robots. |
| O3 | Rooms are set by role: owner, PM, design studio, QA/QC, site trailer, contractor gate. | Changing a role moves the desk, so the floor plan always matches who does what. |
| O4 | Everything is drawn in inline SVG and CSS. No image files, no animation library. | One self-contained file; it scales cleanly and supports a night (dark) version through color tokens. |
| O5 | The owner can approve in the prototype, labelled "approving as owner". | In small firms the owner often is the approver. The brief's rule stays: only humans approve, and the Assistant never does. |
| O6 | Removal leaves an open desk and keeps approved history. | Matches the field-book rule: nothing is erased. |
| O7 | All motion respects `prefers-reduced-motion`: walks, flights and stamps resolve instantly. | Accessibility; the state changes still happen. |

## Not in this prototype

- Real accounts, storage, or the field app on the other end. All data is sample data from the brief; nothing is sent.
- SharePoint/OneDrive connection and email forwarding are shown as UI only.
- The worker app screens (W1–W6) and the desktop Jobs list (M3).

## Next

1. Owner feedback on the metaphor and on how much motion is right by default.
2. Worker app W2 (job chat) in the same visual language, so a desk's drawer can open the actual conversation.
3. Decide whether "Delegate routine approvals" lives on the PM's desk.
