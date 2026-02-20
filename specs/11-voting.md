# 11 - Voting

## Overview

Members propose and vote on future content topics and mini-course topics.

## Data Model

```
Poll:
  id: uuid
  title: string                   # e.g. "What topic should we cover next?"
  description: string | null
  poll_type: enum                 # "topic" (for sessions/articles), "course" (for mini-courses)
  required_level: int             # 2 for topic polls (Main+), 3 for course polls (Premium)
  status: enum                    # "open", "closed"
  allow_proposals: bool           # true = members can add options, false = admin-defined options only
  max_votes_per_user: int         # default 3 (user can vote on up to 3 options)
  created_at: datetime
  closes_at: datetime | null      # null = stays open until manually closed

PollOption:
  id: uuid
  poll_id: FK -> Poll
  title: string                   # e.g. "Building RAG pipelines with LangChain"
  description: string | null
  proposed_by: FK -> User | null  # null if created by admin
  created_at: datetime

PollVote:
  poll_id: FK -> Poll
  option_id: FK -> PollOption
  user_id: FK -> User
  created_at: datetime
  UNIQUE(poll_id, user_id, option_id)  # one vote per option per user
```

## Pages

### `/vote` — Active polls

- Lists all open polls the user has access to (based on `required_level`)
- Each poll shows: title, description, number of options, number of votes cast, close date if set
- Clicking goes to poll detail

### `/vote/{id}` — Poll detail

- Shows all options sorted by vote count descending
- Each option: title, description, vote count, "Vote" button (or "Voted ✓" if already voted)
- User can vote on up to `max_votes_per_user` options. Voting is toggle: click again to remove vote.
- If `allow_proposals`: form at bottom to propose a new option (title + optional description). New proposals appear immediately in the list with 0 votes.
- If poll is closed: show results (sorted by votes), no voting or proposing allowed

## Admin

### `/admin/polls`

- List all polls, filterable by status
- Actions: Edit, Close, Delete

### `/admin/polls/new`

- Form: title, description, poll_type (dropdown: "Topic" or "Mini-course"), status, allow_proposals (checkbox), max_votes_per_user, closes_at (optional datetime picker)
- Below: list of pre-defined options (admin can add options before publishing)
- `required_level` is auto-set: 2 for "topic" type, 3 for "course" type

## Requirements

- R-VOT-1: Create `polls`, `poll_options`, `poll_votes` tables with schemas above.
- R-VOT-2: `GET /api/polls` returns open polls where `user.tier.level >= poll.required_level`. Each poll includes total vote count and whether user has voted.
- R-VOT-3: `GET /api/polls/{id}` returns poll with all options, each option's vote count, and which options the current user has voted for. Returns 403 if user's tier is too low.
- R-VOT-4: `POST /api/polls/{id}/vote` with `{option_id}`. Toggles vote: if vote exists, delete it; if not, create it. Returns 400 if user already has `max_votes_per_user` votes on this poll (and is trying to add another).
- R-VOT-5: `POST /api/polls/{id}/propose` with `{title, description}`. Creates a new PollOption with `proposed_by = current_user`. Returns 403 if `allow_proposals` is false or poll is closed.
- R-VOT-6: Admin endpoints: `POST /api/admin/polls` (create), `PUT /api/admin/polls/{id}` (edit/close), `DELETE /api/admin/polls/{id}`, `POST /api/admin/polls/{id}/options` (add option), `DELETE /api/admin/polls/{id}/options/{option_id}`.
