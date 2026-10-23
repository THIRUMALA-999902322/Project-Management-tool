# Design brief: Fieldbook — AI-assisted work tracking for civil projects

> Original brief, kept as the source of requirements. The product has since moved to a standard business layout (sidebar, approvals queue, tables); see `ARCHITECTURE.md`.

## What you're designing
Fieldbook is a project-tracking app for civil engineering teams (water, sewer, drainage, paving; both design and construction work). Every person on a project gets an AI agent matched to their role, shown in the UI simply as "Assistant." People drop in whatever they have (photos, voice notes, PDFs, Word files, quick texts) and the assistant turns it into a structured, evidence-backed report. The manager has an assistant too: it checks each report against the original files and the job plan. The manager approves, progress updates, and approved history is locked for good.

The name comes from the surveyor's field book: every entry in ink, nothing erased. Mistakes get a single strike-through line and a new entry. Carry that idea through the whole design.

Deliver high-fidelity screens: mobile frames at 390×844, desktop at 1440×900.

## Who uses it
- **Field worker, inspector or crew lead (mobile, outdoors):** reports progress through the day, often in sun, gloves and weak signal. Wants to snap, talk and get back to work.
- **Office roles (CAD tech, design engineer, QA/QC reviewer):** report progress on drawings, submittals and reviews, on desktop or phone.
- **Project manager (desktop and phone):** creates jobs and roles, uploads scope documents, reviews and approves reports, tracks progress.
- **External contractor (mobile, limited):** sees and reports on their own scope only.

## Design principles (non-negotiable)
1. **Simpler than any PM tool.** No Gantt charts, dense tables, 20-item sidebars or forms. The worker app is two screens; the manager app has three tabs.
2. **Chat-first.** People talk or snap; the assistant builds the structure and shows it back as a clean card to confirm.
3. **Evidence everywhere.** Every number or claim the assistant shows has a small source chip (photo, PDF page, sheet). Tapping it opens the original with the relevant spot highlighted.
4. **Humans decide.** Assistants draft, summarize and check; only the manager approves. Approving looks and feels deliberate and permanent.
5. **Plain language.** "Waiting on pressure test," never status codes.
6. **One primary action per screen.**
7. **Field-ready.** Primary buttons 56px tall, nothing tappable under 48px, high contrast for sunlight, clear offline behavior.
8. **Never color alone.** Every status has an icon and a text label.

## Visual direction
- Mood: calm, trustworthy, paper and ink. A digital surveyor's field book, not a SaaS dashboard.
- Background warm off-white #FAF8F3; cards white with hairline borders #E7E2D9; text ink navy #14213D; secondary text slate #5B6475.
- Brand accent: field-book yellow #F2C230, used sparingly (logo, the capture button with an ink-navy icon, small highlights).
- Primary buttons ink navy with white text; Approve buttons green.
- Status colors, always paired with icon and label:
  - Approved / verified: green #2F7D4F, check or lock icon
  - Pending approval: slate blue #3B5B8C, clock icon
  - Needs attention: amber #B45309, alert icon
  - Flagged / blocked: red #B42318, flag icon
- Type: Inter or IBM Plex Sans for the UI; IBM Plex Mono with tabular figures for stations (12+00), quantities (320 LF), dates and IDs.
- Generous spacing, 16–17px body text on mobile, large progress numbers, 12px card radius, minimal shadows, simple line icons.
- Integrations (Outlook, SharePoint) appear as text labels with generic icons, no third-party logos.
- If a default design system is set, use its components and type, and keep the status color meanings above.
- Meet WCAG AA contrast everywhere.

## Signature components
- **Job card:** job name, responsible person, progress bar with %, one plain status line, and a badge when something needs action.
- **Two-part progress bar:** solid fill for approved progress, a hatched extension for submitted-but-not-approved. Label: "62% · 71% when approved."
- **Report card (assistant's draft):** structured bullets (quantity, stations or location, progress change), a "Missing" section, evidence chips, and two actions: [Report to ▾] and [Email].
- **Evidence chip:** thumbnail plus a short label ("Photo 3 · 12+40", "Test report p.2").
- **Review brief (manager's assistant):** "Claimed" next to "Evidence shows," flags, comparison with the plan and past output, suggested questions as tappable chips, and a quiet line: "Checked against the original files, not the summary."
- **Status pill:** Draft · Submitted · Changes requested · Approved (lock) · Amended.
- **Locked history entry:** lock icon, "Approved by Dana Brooks · Oct 20, 4:12 PM," and a short fingerprint in mono ("#a41f…9c2"). Managers see an "Amend" link.
- **Amendment:** the original stays readable under a single strike-through line, with the amendment below it showing who, when and why.
- **Upload tray:** per-file status and background progress ("Reading 128 of 412 pages · you can leave this screen," "3 files already uploaded, skipped").
- **Offline banner:** "Offline · 12 items queued · they'll upload automatically."

## Screens

### Worker app (mobile)
**W1. My jobs (home).** Greeting and job cards. Anything needing action floats to the top ("Dana asked 1 question"). A large yellow capture button: snap first, then the app asks which job or suggests one from location.

**W2. Job chat (the core screen).**
- Header: job name and two-part progress bar.
- Pinned, collapsible "Today's draft" summary.
- Thread: worker messages (photo grids, voice notes with waveform and transcript, PDF and Word chips, text) and assistant messages (short clarifying questions such as "Which stations?", then the report card).
- Composer: attach, camera, mic and a text field.

**W3. Report to (bottom sheet).** The suggested recipient first (the job's manager), then others on the job. A preview of what gets sent: summary, claims, evidence count. Primary button "Send report." Afterwards the status reads "Submitted · waiting for Dana."

**W4. Email draft (bottom sheet).** Preview with To, Subject ("Line A water main · daily progress · Oct 20"), a short bulleted body, and files as a secure link ("Secure link · expires Oct 27 · access logged"), not attachments. Primary button "Open in Outlook," with helper text "You'll review and send it from Outlook."

**W5. Manager question in the chat.** The manager's question appears in the thread, anchored to the report it's about. The assistant suggests a reply and what to attach ("Add today's photos of 13+50 to 14+00?").

**W6. Job details (tap the header).** Three sections only: Plan (items with mini progress bars), Documents (auto-filed and searchable, no folder trees) and History (locked entries).

### Manager app (desktop first; mobile versions of Inbox and Review)
Navigation: a project switcher at the top, then three items only: Inbox, Jobs, Ask.

**M1. Inbox.** Grouped into Routine (evidence complete, matches plan), Flagged and Blocked. Each item shows the person, job, what changed ("+320 LF"), the assistant's one-line verdict, and [Approve] [Ask] [Open]. Batch action: "Approve 4 routine items," with a confirm sheet listing them. A small "Delegate routine approvals" link. On mobile, a push notification opens straight into Review.

**M2. Review.**
- Left: the review brief, including history context ("320 LF today vs. this crew's usual 180 LF").
- Right: the evidence viewer (photo with GPS pin and capture time, PDF page with the highlighted region).
- Bottom bar: [Request changes] [Ask] [Approve].
- Request changes opens a short sheet pre-filled from the Missing list, with an optional note.
- Approve opens a confirmation: "Approve and lock? This becomes a permanent record. Mistakes can be corrected later with an amendment, never erased." After approval, show a lock animation and the progress bar filling in.

**M3. Jobs.** Overall project progress at the top, then every job: name, responsible person, two-part progress bar, plain status line, last update. Minimal filters (person, status).

**M4. Job page.** Plan items with their measurement method and progress: quantity ("2,230 of 3,600 LF"), weighted steps ("Manhole MH-5: set, 50%") or done / not done ("Permit approved"). Then the history timeline with locked entries and amendments, documents, and people and roles.

**M5. Ask.** A chat with the manager's assistant across all jobs. Example: "What's holding up Line A?" gets a short answer with source chips and links to the items. Show suggested questions on the empty state.

**M6. Job setup (four steps).**
1. Name the job.
2. Add people and pick roles. Each role card says in plain language what its assistant can do and what needs the manager's approval.
3. Upload scope documents: a large drop zone that accepts whole folders and ZIP files, plus "Connect a SharePoint or OneDrive folder" and "Forward emails to this job's address." Show processing progress.
4. Review the plan the assistant proposes: items, quantities, measurement method, weights, and a source chip on each ("Sheet W-12," "Spec p.47"). Inline edits, then "Approve plan."

**M7. Amend a locked entry.** From history: Amend → required reason → preview showing the original struck through above the new entry → confirm.

## Sample data (use it consistently)
Project: Oak Hollow Water & Sewer Replacement. Manager: Dana Brooks. Today: Tuesday, Oct 20, 2026.
- Line A · 8-in water main · J. Ortiz (crew lead) · 2,230 of 3,600 LF approved (62%) · pending +320 LF, Sta 12+00 to 15+20 (71% when approved) · missing: pressure test.
- Sewer MH-4 to MH-7 · 8-in sanitary · K. Lee (inspector) · CCTV report submitted · flagged: 2 photos match ones submitted Oct 13.
- Services and meter boxes · M. Alvarez · 46 of 72 reconnected (64%).
- 90% submittal, water sheets · D. Chen (CAD tech) · 42 of 60 sheets drafted (70%).
- SW3P inspections · S. Patel · weekly · next one due Thursday.

## Voice and copy
Short, plain and specific, using construction vocabulary, with no hype. Don't stamp "AI-powered" or sparkles everywhere; the assistant is simply labeled "Assistant" with one small, consistent avatar. Examples: "Waiting on pressure test" · "Needs 2 new photos" · "Approved by Dana · locked" · "Nothing yet. Snap a photo or tell me what you did today."

## States to include
An empty job, a large upload processing, offline with queued items, a skipped duplicate, changes requested, approved and locked, and an amended entry.

## Build order
- Round 1: W2 Job chat, M1 Inbox, M2 Review (with the approve confirm).
- Round 2: W1 My jobs, M3 Jobs, M4 Job page.
- Round 3: M6 Job setup, W3 Report to, W4 Email draft.
- Round 4: the states above, M7 Amend, M5 Ask, W5 and W6.
