from __future__ import annotations

import datetime as dt
import sqlite3
from decimal import Decimal, InvalidOperation

CURRENCY = "EUR"

UNSET = object()


class IOUError(Exception):
    code = "error"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.message = message
        self.details = details


class UnknownPerson(IOUError):
    code = "unknown_person"


class DuplicatePerson(IOUError):
    code = "duplicate_person"


class ArchivedPerson(IOUError):
    code = "archived_person"


class PersonInUse(IOUError):
    code = "person_in_use"


class InvalidValue(IOUError):
    code = "invalid_value"


class InvalidAmount(IOUError):
    code = "invalid_amount"


class InvalidDate(IOUError):
    code = "invalid_date"


class InvalidSplit(IOUError):
    code = "invalid_split"


class SumMismatch(IOUError):
    code = "sum_mismatch"


class NotFound(IOUError):
    code = "not_found"


class AlreadyVoided(IOUError):
    code = "already_voided"


class SelfSettlement(IOUError):
    code = "self_settlement"


class DBError(IOUError):
    code = "db_error"


def parse_amount(text: str) -> int:
    s = str(text).strip().replace(",", ".")
    if "_" in s:
        raise InvalidAmount(f"invalid amount: {text!r}")
    try:
        value = Decimal(s)
    except InvalidOperation:
        raise InvalidAmount(f"invalid amount: {text!r}") from None
    if not value.is_finite():
        raise InvalidAmount(f"invalid amount: {text!r}")
    sign, digits, exponent = value.as_tuple()
    if exponent < -2:
        raise InvalidAmount(f"amount {text!r} has more than two decimal places")
    result = int("".join(str(digit) for digit in digits)) * 10 ** (exponent + 2)
    if sign:
        result = -result
    if result <= 0:
        raise InvalidAmount(f"amount must be positive, got {text!r}")
    return result


def fmt_amount(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}{abs(cents) // 100}.{abs(cents) % 100:02d}"


def normalize_spent_at(value) -> str:
    if value is None or str(value).strip() == "":
        return dt.date.today().isoformat()
    s = str(value).strip()
    lowered = s.lower()
    if lowered == "today":
        return dt.date.today().isoformat()
    if lowered == "yesterday":
        return (dt.date.today() - dt.timedelta(days=1)).isoformat()
    try:
        return dt.date.fromisoformat(s).isoformat()
    except ValueError:
        raise InvalidDate(
            f"invalid date {s!r}: expected YYYY-MM-DD, 'today' or 'yesterday'"
        ) from None


def _clean_optional(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_handle(value):
    text = _clean_optional(value)
    if text is None:
        return None
    return text.removeprefix("@")


def _require_reason(reason) -> str:
    text = _clean_optional(reason)
    if text is None:
        raise InvalidValue("--reason is required")
    return text


def person_dict(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "slack_id": row["slack_id"],
        "slack_handle": row["slack_handle"],
        "archived": bool(row["archived"]),
        "created_at": row["created_at"],
    }


def _person_by_id(conn, person_id):
    return conn.execute("SELECT * FROM person WHERE id = ?", (person_id,)).fetchone()


def _find_by_name(conn, name):
    row = conn.execute("SELECT * FROM person WHERE name = ?", (name,)).fetchone()
    if row is not None:
        return row
    target = name.casefold()
    return next(
        (row for row in conn.execute("SELECT * FROM person") if row["name"].casefold() == target),
        None,
    )


def _find_by_handle(conn, handle):
    handle = handle.strip()
    if not handle:
        return None
    row = conn.execute("SELECT * FROM person WHERE slack_handle = ?", (handle,)).fetchone()
    if row is not None:
        return row
    target = handle.casefold()
    return next(
        (
            row
            for row in conn.execute("SELECT * FROM person")
            if row["slack_handle"] is not None and row["slack_handle"].casefold() == target
        ),
        None,
    )


def resolve_person(conn, ref, *, allow_archived=False):
    text = str(ref or "").strip()
    if not text:
        raise UnknownPerson("empty person reference")
    row = conn.execute("SELECT * FROM person WHERE slack_id = ?", (text,)).fetchone()
    if row is None:
        row = _find_by_handle(conn, text.removeprefix("@"))
    if row is None:
        row = _find_by_name(conn, text)
    if row is None:
        known = [r["name"] for r in conn.execute("SELECT name FROM person ORDER BY name")]
        raise UnknownPerson(f"no person matches {text!r}", ref=text, known_people=known)
    if row["archived"] and not allow_archived:
        raise ArchivedPerson(
            f"{row['name']} is archived; unarchive them first", person=person_dict(row)
        )
    return row


def add_person(conn, name, *, slack_id=None, slack_handle=None):
    clean_name = _clean_optional(name)
    if clean_name is None:
        raise InvalidValue("person name must not be empty")
    slack_id = _clean_optional(slack_id)
    slack_handle = _clean_handle(slack_handle)
    existing = _find_by_name(conn, clean_name)
    if existing is not None:
        raise DuplicatePerson(
            f"a person named {existing['name']!r} already exists", person_id=existing["id"]
        )
    _check_slack_unique(conn, slack_id, slack_handle)
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO person (name, slack_id, slack_handle) VALUES (?, ?, ?)",
                (clean_name, slack_id, slack_handle),
            )
    except sqlite3.IntegrityError:
        raise DuplicatePerson(
            "name, handle or slack id is already in use",
        ) from None
    return person_dict(_person_by_id(conn, cur.lastrowid))


def _check_slack_unique(conn, slack_id=None, slack_handle=None, exclude_id=None):
    if slack_id is not None:
        row = conn.execute("SELECT * FROM person WHERE slack_id = ?", (slack_id,)).fetchone()
        if row is not None and row["id"] != exclude_id:
            raise DuplicatePerson(
                f"slack id {slack_id} already belongs to {row['name']!r}", person_id=row["id"]
            )
    if slack_handle is not None:
        row = _find_by_handle(conn, slack_handle)
        if row is not None and row["id"] != exclude_id:
            raise DuplicatePerson(
                f"handle @{slack_handle} already belongs to {row['name']!r}", person_id=row["id"]
            )


def list_people(conn, *, include_archived=False):
    sql = "SELECT * FROM person"
    if not include_archived:
        sql += " WHERE archived = 0"
    sql += " ORDER BY name"
    return [person_dict(row) for row in conn.execute(sql)]


def rename_person(conn, ref, new_name):
    row = resolve_person(conn, ref, allow_archived=True)
    clean = _clean_optional(new_name)
    if clean is None:
        raise InvalidValue("new name must not be empty")
    clash = _find_by_name(conn, clean)
    if clash is not None and clash["id"] != row["id"]:
        raise DuplicatePerson(
            f"a person named {clash['name']!r} already exists", person_id=clash["id"]
        )
    with conn:
        conn.execute("UPDATE person SET name = ? WHERE id = ?", (clean, row["id"]))
    return person_dict(_person_by_id(conn, row["id"]))


def link_person(conn, ref, *, slack_id=None, slack_handle=None):
    row = resolve_person(conn, ref, allow_archived=True)
    slack_id = _clean_optional(slack_id)
    slack_handle = _clean_handle(slack_handle)
    if slack_id is None and slack_handle is None:
        raise InvalidValue("provide --slack-id or --handle")
    _check_slack_unique(conn, slack_id, slack_handle, exclude_id=row["id"])
    try:
        with conn:
            if slack_id is not None:
                conn.execute(
                    "UPDATE person SET slack_id = ? WHERE id = ?", (slack_id, row["id"])
                )
            if slack_handle is not None:
                conn.execute(
                    "UPDATE person SET slack_handle = ? WHERE id = ?", (slack_handle, row["id"])
                )
    except sqlite3.IntegrityError:
        raise DuplicatePerson("name, handle or slack id is already in use") from None
    return person_dict(_person_by_id(conn, row["id"]))


def unlink_person(conn, ref):
    row = resolve_person(conn, ref, allow_archived=True)
    with conn:
        conn.execute(
            "UPDATE person SET slack_id = NULL, slack_handle = NULL WHERE id = ?", (row["id"],)
        )
    return person_dict(_person_by_id(conn, row["id"]))


def set_archived(conn, ref, archived: bool):
    row = resolve_person(conn, ref, allow_archived=True)
    with conn:
        conn.execute(
            "UPDATE person SET archived = ? WHERE id = ?", (1 if archived else 0, row["id"])
        )
    return person_dict(_person_by_id(conn, row["id"]))


def delete_person(conn, ref):
    row = resolve_person(conn, ref, allow_archived=True)
    used = conn.execute(
        """
        SELECT EXISTS (SELECT 1 FROM expense WHERE payer_id = ?)
            OR EXISTS (SELECT 1 FROM expense_share WHERE person_id = ?)
            OR EXISTS (SELECT 1 FROM settlement WHERE from_id = ? OR to_id = ?)
        """,
        (row["id"], row["id"], row["id"], row["id"]),
    ).fetchone()[0]
    if used:
        raise PersonInUse(
            f"{row['name']} appears in expenses or settlements; archive them instead",
            person_id=row["id"],
        )
    with conn:
        conn.execute("DELETE FROM person WHERE id = ?", (row["id"],))
    return person_dict(row)


def _split_rows(conn, split, include_all):
    if include_all:
        rows = conn.execute("SELECT * FROM person WHERE archived = 0 ORDER BY id").fetchall()
        if not rows:
            raise InvalidSplit("no active people to split among")
        return rows
    refs = list(split or [])
    if not refs:
        raise InvalidSplit("split is empty; use --split, --share or --all")
    rows = [resolve_person(conn, ref) for ref in refs]
    seen = set()
    for row in rows:
        if row["id"] in seen:
            raise InvalidSplit(f"{row['name']} appears twice in the split")
        seen.add(row["id"])
    return rows


def _equal_entries(rows, amount_cents):
    n = len(rows)
    if amount_cents < n:
        raise InvalidSplit(
            f"cannot split {fmt_amount(amount_cents)} among {n} people "
            "with at least 0.01 each"
        )
    base, remainder = divmod(amount_cents, n)
    return [(row, base + (1 if i < remainder else 0)) for i, row in enumerate(rows)]


def _exact_entries(conn, shares, amount_cents):
    entries = []
    seen = set()
    for ref, cents in shares:
        row = resolve_person(conn, ref)
        if row["id"] in seen:
            raise InvalidSplit(f"{row['name']} appears twice in the shares")
        seen.add(row["id"])
        if cents <= 0:
            raise InvalidAmount(f"share for {row['name']} must be positive")
        entries.append((row, cents))
    if not entries:
        raise InvalidSplit("no shares given; use --split, --share or --all")
    total = sum(cents for _, cents in entries)
    if total != amount_cents:
        raise SumMismatch(
            f"shares sum to {fmt_amount(total)} but the expense amount is "
            f"{fmt_amount(amount_cents)}",
            shares_total_cents=total,
            amount_cents=amount_cents,
        )
    return entries


def _check_split_selectors(shares, split, include_all):
    provided = sum((bool(shares), split is not None, bool(include_all)))
    if provided > 1:
        raise InvalidValue("use at most one of --split, --share or --all")


def _insert_expense(
    conn,
    *,
    description,
    amount_cents,
    payer_id,
    spent_at,
    category,
    source,
    split_mode,
    entries,
    supersedes=None,
):
    cur = conn.execute(
        """
        INSERT INTO expense
            (description, amount_cents, currency, payer_id, spent_at, category, source,
             split_mode, supersedes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            description,
            amount_cents,
            CURRENCY,
            payer_id,
            spent_at,
            category,
            source,
            split_mode,
            supersedes,
        ),
    )
    expense_id = cur.lastrowid
    for row, cents in entries:
        conn.execute(
            "INSERT INTO expense_share (expense_id, person_id, amount_cents) VALUES (?, ?, ?)",
            (expense_id, row["id"], cents),
        )
    return expense_id


def add_expense(
    conn,
    *,
    payer,
    amount_cents,
    description,
    split=None,
    shares=None,
    include_all=False,
    spent_at=None,
    category=None,
    source=None,
):
    payer_row = resolve_person(conn, payer)
    clean_desc = _clean_optional(description)
    if clean_desc is None:
        raise InvalidValue("description must not be empty")
    if amount_cents <= 0:
        raise InvalidAmount("amount must be positive")
    _check_split_selectors(shares, split, include_all)
    if shares:
        entries = _exact_entries(conn, shares, amount_cents)
        split_mode = "exact"
    else:
        entries = _equal_entries(_split_rows(conn, split, include_all), amount_cents)
        split_mode = "equal"
    with conn:
        expense_id = _insert_expense(
            conn,
            description=clean_desc,
            amount_cents=amount_cents,
            payer_id=payer_row["id"],
            spent_at=normalize_spent_at(spent_at),
            category=_clean_optional(category),
            source=_clean_optional(source),
            split_mode=split_mode,
            entries=entries,
        )
    return get_expense(conn, expense_id)


def get_expense(conn, expense_id):
    row = conn.execute("SELECT * FROM expense WHERE id = ?", (expense_id,)).fetchone()
    if row is None:
        raise NotFound(f"expense {expense_id} not found", id=expense_id)
    return expense_dict(conn, row)


def expense_dict(conn, row) -> dict:
    payer = _person_by_id(conn, row["payer_id"])
    shares = []
    for share in conn.execute(
        "SELECT * FROM expense_share WHERE expense_id = ? ORDER BY person_id", (row["id"],)
    ):
        shares.append(
            {
                "person": person_dict(_person_by_id(conn, share["person_id"])),
                "amount_cents": share["amount_cents"],
                "amount": fmt_amount(share["amount_cents"]),
            }
        )
    return {
        "id": row["id"],
        "description": row["description"],
        "amount_cents": row["amount_cents"],
        "amount": fmt_amount(row["amount_cents"]),
        "currency": row["currency"],
        "payer": person_dict(payer),
        "spent_at": row["spent_at"],
        "created_at": row["created_at"],
        "category": row["category"],
        "source": row["source"],
        "split_mode": row["split_mode"],
        "voided": bool(row["voided"]),
        "void_reason": row["void_reason"],
        "supersedes": row["supersedes"],
        "superseded_by": row["superseded_by"],
        "shares": shares,
    }


def void_expense(conn, expense_id, reason):
    clean_reason = _require_reason(reason)
    row = conn.execute("SELECT * FROM expense WHERE id = ?", (expense_id,)).fetchone()
    if row is None:
        raise NotFound(f"expense {expense_id} not found", id=expense_id)
    if row["voided"]:
        raise AlreadyVoided(f"expense {expense_id} is already voided")
    with conn:
        conn.execute(
            "UPDATE expense SET voided = 1, void_reason = ? WHERE id = ?",
            (clean_reason, expense_id),
        )
    return get_expense(conn, expense_id)


def correct_expense(
    conn,
    expense_id,
    *,
    reason,
    payer=None,
    amount_cents=None,
    description=None,
    split=None,
    shares=None,
    include_all=False,
    spent_at=None,
    category=None,
    source=UNSET,
):
    clean_reason = _require_reason(reason)
    original = conn.execute("SELECT * FROM expense WHERE id = ?", (expense_id,)).fetchone()
    if original is None:
        raise NotFound(f"expense {expense_id} not found", id=expense_id)
    if original["voided"]:
        raise AlreadyVoided(f"expense {expense_id} is already voided")
    _check_split_selectors(shares, split, include_all)

    new_description = (
        _clean_optional(description) if description is not None else original["description"]
    )
    if new_description is None:
        raise InvalidValue("description must not be empty")
    new_amount = amount_cents if amount_cents is not None else original["amount_cents"]
    if new_amount <= 0:
        raise InvalidAmount("amount must be positive")
    payer_id = (
        resolve_person(conn, payer)["id"] if payer is not None else original["payer_id"]
    )
    new_spent = normalize_spent_at(spent_at) if spent_at is not None else original["spent_at"]
    new_category = _clean_optional(category) if category is not None else original["category"]
    new_source = _clean_optional(source) if source is not UNSET else original["source"]

    original_shares = conn.execute(
        "SELECT person_id, amount_cents FROM expense_share WHERE expense_id = ? ORDER BY person_id",
        (expense_id,),
    ).fetchall()

    if shares:
        entries = _exact_entries(conn, shares, new_amount)
        split_mode = "exact"
    elif split is not None or include_all:
        entries = _equal_entries(_split_rows(conn, split, include_all), new_amount)
        split_mode = "equal"
    elif original["split_mode"] == "equal":
        rows = [_person_by_id(conn, s["person_id"]) for s in original_shares]
        entries = _equal_entries(rows, new_amount)
        split_mode = "equal"
    else:
        original_total = sum(s["amount_cents"] for s in original_shares)
        if new_amount != original_total:
            raise SumMismatch(
                "this expense uses exact shares; changing the amount requires "
                "new --share values or --split",
                amount_cents=new_amount,
                shares_total_cents=original_total,
            )
        entries = [
            (_person_by_id(conn, s["person_id"]), s["amount_cents"]) for s in original_shares
        ]
        split_mode = "exact"

    with conn:
        new_id = _insert_expense(
            conn,
            description=new_description,
            amount_cents=new_amount,
            payer_id=payer_id,
            spent_at=new_spent,
            category=new_category,
            source=new_source,
            split_mode=split_mode,
            entries=entries,
            supersedes=expense_id,
        )
        conn.execute(
            "UPDATE expense SET voided = 1, void_reason = ?, superseded_by = ? WHERE id = ?",
            (clean_reason, new_id, expense_id),
        )
    return get_expense(conn, new_id)


def list_expenses(
    conn, *, person=None, since=None, until=None, category=None, include_voided=False, limit=None
):
    where = []
    params = []
    if not include_voided:
        where.append("e.voided = 0")
    if person is not None:
        row = resolve_person(conn, person, allow_archived=True)
        where.append(
            "(e.payer_id = ? OR EXISTS ("
            "SELECT 1 FROM expense_share s WHERE s.expense_id = e.id AND s.person_id = ?))"
        )
        params.extend([row["id"], row["id"]])
    if since is not None:
        where.append("e.spent_at >= ?")
        params.append(normalize_spent_at(since))
    if until is not None:
        where.append("e.spent_at <= ?")
        params.append(normalize_spent_at(until))
    if category is not None:
        where.append("e.category = ? COLLATE NOCASE")
        params.append(category)
    sql = "SELECT e.id FROM expense e"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY e.spent_at DESC, e.id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [get_expense(conn, row["id"]) for row in conn.execute(sql, params)]


def add_settlement(conn, *, from_ref, to_ref, amount_cents, note=None, source=None):
    sender = resolve_person(conn, from_ref, allow_archived=True)
    receiver = resolve_person(conn, to_ref, allow_archived=True)
    if sender["id"] == receiver["id"]:
        raise SelfSettlement(f"from and to are the same person ({sender['name']})")
    if amount_cents <= 0:
        raise InvalidAmount("amount must be positive")
    with conn:
        cur = conn.execute(
            "INSERT INTO settlement (from_id, to_id, amount_cents, note, source) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                sender["id"],
                receiver["id"],
                amount_cents,
                _clean_optional(note),
                _clean_optional(source),
            ),
        )
    return get_settlement(conn, cur.lastrowid)


def get_settlement(conn, settlement_id):
    row = conn.execute("SELECT * FROM settlement WHERE id = ?", (settlement_id,)).fetchone()
    if row is None:
        raise NotFound(f"settlement {settlement_id} not found", id=settlement_id)
    return settlement_dict(conn, row)


def settlement_dict(conn, row) -> dict:
    return {
        "id": row["id"],
        "from": person_dict(_person_by_id(conn, row["from_id"])),
        "to": person_dict(_person_by_id(conn, row["to_id"])),
        "amount_cents": row["amount_cents"],
        "amount": fmt_amount(row["amount_cents"]),
        "note": row["note"],
        "source": row["source"],
        "created_at": row["created_at"],
        "voided": bool(row["voided"]),
        "void_reason": row["void_reason"],
        "supersedes": row["supersedes"],
        "superseded_by": row["superseded_by"],
    }


def void_settlement(conn, settlement_id, reason):
    clean_reason = _require_reason(reason)
    row = conn.execute("SELECT * FROM settlement WHERE id = ?", (settlement_id,)).fetchone()
    if row is None:
        raise NotFound(f"settlement {settlement_id} not found", id=settlement_id)
    if row["voided"]:
        raise AlreadyVoided(f"settlement {settlement_id} is already voided")
    with conn:
        conn.execute(
            "UPDATE settlement SET voided = 1, void_reason = ? WHERE id = ?",
            (clean_reason, settlement_id),
        )
    return get_settlement(conn, settlement_id)


def correct_settlement(
    conn,
    settlement_id,
    *,
    reason,
    from_ref=None,
    to_ref=None,
    amount_cents=None,
    note=UNSET,
    source=UNSET,
):
    clean_reason = _require_reason(reason)
    original = conn.execute("SELECT * FROM settlement WHERE id = ?", (settlement_id,)).fetchone()
    if original is None:
        raise NotFound(f"settlement {settlement_id} not found", id=settlement_id)
    if original["voided"]:
        raise AlreadyVoided(f"settlement {settlement_id} is already voided")
    if from_ref is not None:
        sender_id = resolve_person(conn, from_ref, allow_archived=True)["id"]
    else:
        sender_id = original["from_id"]
    if to_ref is not None:
        receiver_id = resolve_person(conn, to_ref, allow_archived=True)["id"]
    else:
        receiver_id = original["to_id"]
    if sender_id == receiver_id:
        raise SelfSettlement("from and to are the same person")
    new_amount = amount_cents if amount_cents is not None else original["amount_cents"]
    if new_amount <= 0:
        raise InvalidAmount("amount must be positive")
    new_note = _clean_optional(note) if note is not UNSET else original["note"]
    new_source = _clean_optional(source) if source is not UNSET else original["source"]
    with conn:
        cur = conn.execute(
            "INSERT INTO settlement (from_id, to_id, amount_cents, note, source, supersedes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sender_id, receiver_id, new_amount, new_note, new_source, settlement_id),
        )
        conn.execute(
            "UPDATE settlement SET voided = 1, void_reason = ?, superseded_by = ? WHERE id = ?",
            (clean_reason, cur.lastrowid, settlement_id),
        )
    return get_settlement(conn, cur.lastrowid)


def list_settlements(conn, *, person=None, include_voided=False, limit=None):
    where = []
    params = []
    if not include_voided:
        where.append("t.voided = 0")
    if person is not None:
        row = resolve_person(conn, person, allow_archived=True)
        where.append("(t.from_id = ? OR t.to_id = ?)")
        params.extend([row["id"], row["id"]])
    sql = "SELECT t.id FROM settlement t"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY t.created_at DESC, t.id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [get_settlement(conn, row["id"]) for row in conn.execute(sql, params)]


def balances(conn):
    people = {row["id"]: person_dict(row) for row in conn.execute("SELECT * FROM person")}
    totals = {pid: 0 for pid in people}
    for pid, amount in conn.execute(
        "SELECT payer_id, SUM(amount_cents) FROM expense WHERE voided = 0 GROUP BY payer_id"
    ):
        totals[pid] += amount
    for pid, amount in conn.execute(
        """
        SELECT s.person_id, SUM(s.amount_cents)
        FROM expense_share s JOIN expense e ON e.id = s.expense_id
        WHERE e.voided = 0
        GROUP BY s.person_id
        """
    ):
        totals[pid] -= amount
    for pid, amount in conn.execute(
        "SELECT from_id, SUM(amount_cents) FROM settlement WHERE voided = 0 GROUP BY from_id"
    ):
        totals[pid] += amount
    for pid, amount in conn.execute(
        "SELECT to_id, SUM(amount_cents) FROM settlement WHERE voided = 0 GROUP BY to_id"
    ):
        totals[pid] -= amount
    items = [
        {"person": people[pid], "balance_cents": amount, "balance": fmt_amount(amount)}
        for pid, amount in totals.items()
    ]
    items.sort(key=lambda item: (-item["balance_cents"], item["person"]["name"].lower()))
    return items


def settle_up(conn):
    people = {}
    creditors = []
    debtors = []
    for item in balances(conn):
        people[item["person"]["id"]] = item["person"]
        if item["balance_cents"] > 0:
            creditors.append([item["person"]["id"], item["balance_cents"]])
        elif item["balance_cents"] < 0:
            debtors.append([item["person"]["id"], -item["balance_cents"]])
    creditors.sort(key=lambda entry: -entry[1])
    debtors.sort(key=lambda entry: -entry[1])
    transfers = []
    i = j = 0
    while i < len(creditors) and j < len(debtors):
        amount = min(creditors[i][1], debtors[j][1])
        transfers.append(
            {
                "from": people[debtors[j][0]],
                "to": people[creditors[i][0]],
                "amount_cents": amount,
                "amount": fmt_amount(amount),
            }
        )
        creditors[i][1] -= amount
        debtors[j][1] -= amount
        if creditors[i][1] == 0:
            i += 1
        if debtors[j][1] == 0:
            j += 1
    return transfers
