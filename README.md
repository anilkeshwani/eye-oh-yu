# iou

Debt tracking for a small team. One SQLite database, one CLI, no server.
Built for the "who owes what from lunch" problem at a 10-50 person startup:
someone pays for the group, the cost is split, balances accumulate, people
settle up when they feel like it.

Phase 1 is backend only. The CLI prints human-readable tables by default and
machine-readable JSON with `--json`, so an agent (for example one living in a
Slack channel) can drive it directly. See `AGENTS.md` for that contract.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```sh
uv sync
uv run iou init            # creates ./iou.db
```

The database path defaults to `$IOU_DB`, then `./iou.db`. Every command also
accepts `--db PATH`.

## People

```sh
iou person add Alice --handle alice --slack-id U0123ABCDEF
iou person list [--include-archived]
iou person rename Alice Alicia
iou person link Bob --handle bob --slack-id U0456DEF012
iou person unlink Bob
iou person archive Bob
iou person unarchive Bob
iou person delete Bob      # only works if Bob has no transactions
```

Anywhere a person is referenced (`--payer`, `--split`, `--share`, `settle`
endpoints, `--person` filters) you can use the name, the Slack handle with or
without the leading `@`, or the Slack member id. Resolution tries Slack id
first, then handle, then name. Names and handles are matched
case-insensitively using full Unicode case folding, so `Élodie` and
`élodie` are the same name.

Archived people are excluded from new expenses and from `--all` splits, but
can still make or receive settlements so their old debts can be closed out.

## Expenses

```sh
# equal split among listed people
iou expense add --payer Alice --amount 42.50 --desc "Lunch order" \
    --split Alice,Bob,Carol --category lunch

# equal split among everyone active
iou expense add --payer Alice --amount 42.50 --desc "Lunch order" --all

# exact shares (must sum to the amount)
iou expense add --payer Bob --amount 25.00 --desc "Taxi" \
    --share Alice=15.00 --share Bob=10.00

# backdating and provenance
iou expense add --payer Alice --amount 42.50 --desc "Lunch order" --all \
    --spent-at yesterday --source "https://acme.slack.com/archives/C1/p123"
```

Notes:

- Amounts are decimal strings with at most two decimal places. Both `.` and
  `,` work as the decimal separator. Storage is integer cents.
- Equal splits that do not divide evenly hand out the leftover cents one at a
  time to the first people in the list. 100.00 split three ways becomes
  33.34, 33.33, 33.33.
- The payer does not have to be in the split (paid for others, shares
  nothing).
- `--spent-at` accepts `YYYY-MM-DD`, `today` or `yesterday`. Defaults to
  today.

```sh
iou expense list [--person X] [--since D] [--until D] [--category C] \
    [--include-voided] [--limit N]
iou expense show 3
```

## Corrections instead of edits

The ledger is append-only. A wrong expense is never mutated.

```sh
iou expense void 3 --reason "entered twice"
iou expense correct 3 --reason "Carol left early" --split Alice,Bob
iou expense correct 3 --reason "amount was wrong" --amount 45.50
```

`void` marks the row as voided with a reason. `correct` inserts a corrected
copy and links the two in both directions: the original is voided and its
`superseded_by` points at the replacement, and the replacement's `supersedes`
points back at the original. Fields you do not pass are copied from the
original. On an equal-split expense, changing the amount recomputes the
equal shares; if no new `--split` is given, the same people keep equal
shares and leftover cents are assigned in person id order. On an exact-split
expense, changing the amount requires new `--share` values.
Voided expenses are excluded from balances but stay in the database, so the
full history of a correction is one query away.

Settlements work the same way: `iou settle void ID --reason R` and
`iou settle correct ID --reason R [--from X] [--to Y] [--amount A]`.

## Settlements and balances

```sh
iou settle add Bob Alice 15.00 --note "bank transfer"
iou settle list [--person X] [--include-voided]
iou settle show 2
iou balances [--person X]
iou settle-up
```

`iou settle add FROM TO AMOUNT` records that FROM repaid TO, meaning FROM
owed TO. A positive balance means the group owes that person, a negative
balance means they owe the group. The sum of all balances is always zero.

`iou settle-up` suggests transfers that clear every debt using a greedy
matching of creditors and debtors. It prints suggestions only. Run
`iou settle add` to actually record a repayment.

## Inspecting the database directly

The schema is four tables: `person`, `expense`, `expense_share`,
`settlement`, plus a one-row `schema_version`.

```sh
sqlite3 iou.db '.schema'

sqlite3 -header iou.db '
  SELECT e.id, e.description, e.amount_cents, p.name AS payer,
         e.spent_at, e.voided, e.superseded_by
  FROM expense e JOIN person p ON p.id = e.payer_id
  ORDER BY e.id;'

sqlite3 -header iou.db '
  SELECT p.name, e.description, s.amount_cents
  FROM expense_share s
  JOIN expense e ON e.id = s.expense_id
  JOIN person p ON p.id = s.person_id
  WHERE e.voided = 0
  ORDER BY e.id;'
```

## Running the tests

```sh
uv run pytest
uv run ruff check .
```

## Roadmap

Phase 2 adds an HTTP front-end over the same core module (`iou/core.py` has
no CLI or IO concerns by design). Phase 3 adds a dedicated Slack bot if the
tagged-agent setup turns out not to be enough.
