# AGENTS.md

How to drive the iou backend as an agent. This file is written for LLM
agents (for example one tagged in a Slack channel) and for the humans
debugging them.

## Commands

```sh
uv run iou <command> ...     # run the CLI
uv run pytest                # tests
uv run ruff check .          # lint
```

## Database

- Default path: `$IOU_DB`, else `./iou.db` (relative to the working
  directory). Every command accepts `--db PATH`.
- The database is created and initialized implicitly on first use.
  `iou init` forces it.
- Inspect it directly with `sqlite3 iou.db`. Schema: `person`, `expense`,
  `expense_share`, `settlement`, `schema_version`.

## Flag placement

`--db` and `--json` are defined on each subcommand, so they go after the
subcommand: `iou expense list --json`, not `iou --json expense list`.

## JSON contract

Every command supports `--json`. Prefer it over parsing tables.

Success, exit code 0:

```json
{"ok": true, "data": {}}
```

Domain failure, exit code 1:

```json
{"ok": false, "error": {"code": "unknown_person", "message": "..."}}
```

`error` may carry extra details, e.g. `known_people` on `unknown_person`,
`shares_total_cents` and `amount_cents` on `sum_mismatch`. Usage errors
(bad flags) exit 2 and print argparse help to stderr.

Error codes: `unknown_person`, `duplicate_person`, `archived_person`,
`person_in_use`, `invalid_value`, `invalid_amount`, `invalid_date`,
`invalid_split`, `sum_mismatch`, `not_found`, `already_voided`,
`self_settlement`, `internal`.

## Person identifiers

Every person reference accepts a name, a Slack handle (with or without `@`),
or a Slack member id. Resolution order: exact Slack id, then handle
(case-insensitive), then name (case-insensitive). Slack events and mentions
carry member ids, so pass those straight through when you have them. Use
`iou person list --json` to map names people type to ids when you need to.

When creating a person from Slack context, record both fields:

```sh
iou person add "Anil Keshwani" --slack-id U0123ABCDEF --handle anilkeshwani
```

## Slack conventions

This app is driven from a Slack channel: a user tags the agent, the agent
runs CLI commands, the agent replies in the thread.

- Pass the Slack message permalink as `--source` on every `expense add` and
  `settle add` you create on behalf of a message. That makes every ledger
  row traceable back to the request that produced it.
- When a user asks to fix a mistake, use `iou expense correct` /
  `iou settle correct` or `void` with a `--reason` that echoes what the user
  said. Never try to edit rows directly in SQLite.

## Ledger rules

- Append-only. Rows are never mutated. `void` marks a row with a reason,
  `correct` inserts a fixed copy and links it via `superseded_by`.
- Positive balance: the group owes that person. Negative: they owe the
  group. All balances always sum to zero.
- `iou settle add FROM TO AMOUNT` means FROM repaid TO, i.e. FROM owed TO.
- `iou settle-up` only suggests transfers; recording them requires
  `iou settle add`.
- Exact shares (`--share PERSON=AMOUNT`) must sum to `--amount`.

## Amounts and dates

- Amounts are decimal strings, at most two decimal places, `.` or `,` as
  separator: `42.50`, `42,50`. Stored as integer cents.
- `--spent-at` takes `YYYY-MM-DD`, `today` or `yesterday`. Default is today.

## Typical flows

Add an expense from a Slack request:

```sh
iou person list --json
iou expense add --payer U0123 --amount 42.50 --desc "Lunch order" \
    --split U0123,U0456,U0789 --category lunch \
    --source "https://acme.slack.com/archives/C123/p1234567890" --json
```

Who owes what, and how to clear it:

```sh
iou balances --json
iou settle-up --json
```
