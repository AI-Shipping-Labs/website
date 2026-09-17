# Take-home challenge of the week

A recurring community challenge for the Main tier: each week the operator posts
one real take-home assignment in Slack, members ship a small repo in 7 days, and
a monthly live defence round lets volunteers present their solutions. Everything
runs on existing machinery: a manual Slack post and the standard event-series
machinery. No platform code.

## Sources

| What | Where |
|---|---|
| Public assignment bank (linkable, member-facing) | `/interview/home-assignments` on the site, published from `interview-questions/home-assignments.md` in `AI-Shipping-Labs/content` |
| Source bank (100+ source-cited assignments) | `alexeygrigorev/ai-engineering-field-guide`, file `interview/questions/06-home-assignments.md` (local checkout `~/git/ai-engineering-field-guide`) |
| Bank refresh procedure | `_docs/content.md`, section "Refresh from the field guide" (`sync_field_guide_interview`) |

The Slack post links to `/interview/home-assignments` so members can browse the
full bank; the guide repo stays the upstream source.

## Weekly loop

1. Take the next unclaimed row from the rota table below. If the rota is
   exhausted, pick a new assignment from `interview/questions/06-home-assignments.md`
   in the guide: prefer ones with a `[^source]` citation, keep the category
   spread roughly at the guide's distribution (RAG around 40%, agents around
   30%, multi-agent, document processing, LLM-as-judge filling the rest).
2. Adapt the text, do not dump it: keep the assignment bullet as posted in the
   bank, add one line of context, and keep the citation link. The guide's prose
   stays in the repo.
3. Post to `#announcements` and create a thread for submissions (template below).
4. Track who submits in the thread: submitters are the presenter pool for the
   next defence round.
5. If posting into `#announcements` proves noisy, moving to a dedicated channel
   is an operator decision - update this runbook and the rota location when it
   happens.

### Slack thread template

```text
Take-home challenge of the week

<One line of context: where this assignment comes from and why it is a
common interview format.>

The assignment:
<Assignment bullet adapted from the bank.>

Category: <RAG / Agents / Multi-agent / Document processing / LLM-as-judge>

Deadline: 7 days (post your repo by <date>).

How to submit: reply in this thread with a link to your repo. Include a short
README with your design decisions and trade-offs - that is what interviewers
look for. Best submissions get invited to present at the monthly Take-home
Defence Round.

Full bank of real assignments: https://aishippinglabs.com/interview/home-assignments
Source: <citation link from the guide>
```

### Posting checklist

- Rota row picked and marked as claimed (edit the table below: add the posted date)
- Assignment text adapted, citation link kept
- Deadline date computed (post date + 7 days) and spelled out
- Thread created under the `#announcements` post; submission instruction says "reply with repo link"
- Link to `/interview/home-assignments` included
- Submitters noted for the defence-round presenter pool

## Monthly defence round

Format mirrors the real defence interviews from the guide (a 45-90 minute
walkthrough): volunteers present their solution, 15-20 minutes each plus Q&A.
The operator moderates, asks the trade-off questions ("why this approach",
"what would you change with more time"), and keeps the session on time.

Presenters are picked from the weekly thread submissions of the past month.

### Studio checklist (first setup, then monthly)

1. Create the series once: `/studio/event-series/new`, name `Take-home Defence
   Round`, public description ("Monthly live session where community members
   present their take-home challenge solutions and defend design decisions,
   mirroring real AI-engineer interview defence rounds"), cadence
   `No fixed cadence` (a plain collection - sessions are added manually, one
   per month).
2. Each month: add the session with `Add occurrence` on the series page
   (`/studio/event-series/<id>/add-occurrence`), scheduled roughly 4 weeks out.
3. Create the Zoom meeting for the occurrence via `Create Zoom`
   (`/studio/event-series/<id>/create-zoom`).
4. Publish the event so it renders on the public events pages:
   `/studio/event-series/<series-id>/events/<event-id>/publish`.
5. Fill the presenter slots from the monthly submission threads before
   announcing the session in Slack.

## First-quarter rota

Twelve pre-vetted assignments from the guide bank, one per week. Each source
link is the citation carried over from `06-home-assignments.md`.

| Week | Assignment | Category | Source |
|---|---|---|---|
| 1 | Build a RAG chatbot that ingests PDFs, embeds them in a vector DB, and answers questions with citations; must answer "I don't have that information" when the answer is not in the retrieved context | RAG | [RokomariTask](https://github.com/gazitanbhir/RokomariTask) |
| 2 | Build a policy-document RAG assistant with mandatory source citations and safe fallbacks for out-of-scope questions; includes a 7-question evaluation set | RAG | [Company-Policy-Assistant](https://github.com/LAWSA07/Company-Policy-Assistant---Neura-Dynamics) |
| 3 | Build a document Q&A system with citation tracking that handles multi-hop questions (answers spanning multiple documents) | RAG | [PromptLayer, the agentic system design interview](https://blog.promptlayer.com/the-agentic-system-design-interview-how-to-evaluate-ai-engineers/) |
| 4 | Build an agentic RAG system for government documents, 100% open-source (Ollama + CrewAI + pgvector), evaluated with RAGAS metrics | RAG | [govgpt-agentic-rag](https://github.com/AsharAhmad/govgpt-agentic-rag) |
| 5 | Build an assistant agent handling database queries, document search, and bash commands, with bash requiring explicit user approval | Agents | [hiring-challenge-alpha](https://github.com/Curling-AI/hiring-challenge-alpha) |
| 6 | Build a sales insights agent over subscription/revenue data that detects and refuses PII requests and is evaluated on accuracy, refusal correctness, and reasoning quality | Agents | [cohere_sales_agent](https://github.com/Aaronxvc/cohere_sales_agent) |
| 7 | Build a public transport query agent that fetches live data from 7 LTA APIs about buses, trains, traffic, and station conditions | Agents | [Transport-Query-Agent](https://github.com/vaishnavip-23/Transport-Query-Agent) |
| 8 | Build a multi-agent content generation system with 5 agents (research, writing, editing, SEO, publishing) producing strictly formatted outputs | Multi-agent | [kasparro content generation](https://github.com/rak-shi/kasparro-ai-agentic-content-generation-system-Rakshitha_Valipireddy) |
| 9 | Implement a minimal workflow engine with graph-based nodes, state management, branching/looping, tool-based logic, a 50-step cap, and mandatory unit tests | Multi-agent | [Minimal-Workflow-Agent](https://github.com/abhishuman18/Minimal-Workflow-Agent-Enigne-Tredence-) |
| 10 | Build a marksheet extraction API that parses complex table layouts and handwriting from academic marksheets into structured JSON | Document processing | [Trestle assignment](https://github.com/gulmittal/Trestle_AI_Engineer_Intern_Assignment-) |
| 11 | Build a question deduplication and clustering pipeline: exact dedup, semantic dedup, LLM-based cluster discovery, evaluated with ARI/NMI/homogeneity metrics | Document processing | [krisp_ai_engineer_role_task](https://github.com/Artush-Baghdasaryan/krisp_ai_engineer_role_task) |
| 12 | Build a 4-stage story pipeline where an LLM judge evaluates outputs against a spec and a rewriter iterates (Spec Builder, Storyteller, LLM Judge, Rewriter) | LLM-as-judge | [hippocratic-ai-bedtime-stories](https://github.com/tasnimhossen/hippocratic-ai-bedtime-stories) |

Weeks 5-12 of the calendar quarter: after week 12 (or if a row turns out to be
a poor fit), draw the next row from the full bank at
`/interview/home-assignments`, following the picking rules in the weekly loop.
