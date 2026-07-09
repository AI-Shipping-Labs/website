# Member Account Page Audit (/account/)

Date: 2026-07-09
Scope: UX + UI of the member account/settings page. Evidence: screenshots of Main member, Free member, and mobile states on local dev (`templates/accounts/account.html`, `accounts/views/account.py`).

## UX findings

1. Paying members get no membership actions. The Membership card for a Main member shows only "Current Plan: Main" — no upgrade to Premium (upgrade shows for Free only, `show_upgrade_action` in `accounts/views/account.py:180-185`), no billing period info without Stripe subscription data, no manage-subscription for comped/override members. The page's top job (see/act on my membership) is served only for Free members. Show: tier benefits summary, next-tier upsell for Basic/Main, billing period end, and the Stripe portal link whenever a subscription exists.
2. Section order does not match member jobs. Profile (optional name, rarely used) is first; Membership is third, below the Slack card. Suggested order: Membership, Email Preferences, Slack, API keys, Display Preferences, Change Password, Profile, Account info.
3. API usage guide is linked twice in the API keys card: inline in the empty-state sentence and as a standalone link row directly below (`account.html:484` and `:490-495`). Keep one (the standalone row).
4. Account info shows "User ID: 26" — a raw internal ID with no explanation. Either drop it or label its purpose ("quote this in support requests").
5. Missing account-level capabilities (product questions): no email-change flow and no account deletion/data export. For an EU-priced product the deletion path is a GDPR-relevant gap.

## UI findings

6. "+ New key" button is oversized (reported by operator): inside the `flex-col gap-3 sm:flex-row` row (`account.html:394-410`) the button lacks `shrink-0`/`whitespace-nowrap`, so its label wraps to two lines and the button renders taller than the input. Fix: add `shrink-0 whitespace-nowrap` (mobile full-width state already looks right).
7. Flash message styling uses dark-tuned raw color classes (`bg-red-500/20 text-red-400` etc., `account.html:27`) — washed out on light theme; same class of defect as the Studio pill contrast issue (#1196).
8. Timezone card is the only one with a Save + Clear button pair, and the "Current timezone:" caption duplicates the select value. Align with the single-Save pattern; drop the caption.
9. Toggle confirmation sentences ("You are subscribed to newsletters.") permanently duplicate the toggle state; a transient confirmation on change would reduce noise.

## Related

Guest-facing audits and Studio audits from the same date in this folder. Issue: filed as a single account-page batch.
