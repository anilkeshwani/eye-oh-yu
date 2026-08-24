from __future__ import annotations

import argparse
import json
import sys

from . import core, db


def render_table(headers, rows):
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()]
    for row in rows:
        lines.append(
            "  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
        )
    return "\n".join(lines)


def signed(cents):
    if cents > 0:
        return f"+{core.fmt_amount(cents)}"
    return core.fmt_amount(cents)


def person_rows(people):
    return [
        [
            p["id"],
            p["name"],
            p["slack_handle"] or "",
            p["slack_id"] or "",
            "yes" if p["archived"] else "",
        ]
        for p in people
    ]


def expense_line(expense):
    names = ", ".join(s["person"]["name"] for s in expense["shares"])
    return (
        f"expense #{expense['id']}: {expense['description']} "
        f"{expense['amount']} {expense['currency']} paid by {expense['payer']['name']}, "
        f"split among {names}"
    )


def expense_table(expenses, include_voided=False):
    headers = ["ID", "SPENT", "DESCRIPTION", "PAYER", "AMOUNT", "SPLIT", "CATEGORY"]
    if include_voided:
        headers.append("VOIDED")
    rows = []
    for e in expenses:
        names = ", ".join(s["person"]["name"] for s in e["shares"])
        row = [
            e["id"],
            e["spent_at"],
            e["description"],
            e["payer"]["name"],
            e["amount"],
            names,
            e["category"] or "",
        ]
        if include_voided:
            row.append("yes" if e["voided"] else "")
        rows.append(row)
    return render_table(headers, rows)


def expense_detail(expense):
    lines = [
        f"expense #{expense['id']}: {expense['description']}",
        f"  amount    {expense['amount']} {expense['currency']}",
        f"  payer     {expense['payer']['name']}",
        f"  spent     {expense['spent_at']}",
        f"  created   {expense['created_at']}",
        f"  split     {expense['split_mode']}",
    ]
    if expense["category"]:
        lines.append(f"  category  {expense['category']}")
    if expense["source"]:
        lines.append(f"  source    {expense['source']}")
    for share in expense["shares"]:
        lines.append(f"  share     {share['person']['name']}: {share['amount']}")
    if expense["voided"]:
        lines.append(f"  VOIDED    {expense['void_reason']}")
    if expense["superseded_by"] is not None:
        lines.append(f"  replaced by expense #{expense['superseded_by']}")
    return "\n".join(lines)


def settlement_line(settlement):
    text = (
        f"settlement #{settlement['id']}: {settlement['from']['name']} paid "
        f"{settlement['to']['name']} {settlement['amount']}"
    )
    if settlement["note"]:
        text += f" ({settlement['note']})"
    return text


def cmd_init(args, conn, db_path):
    return {"data": {"db": str(db_path)}, "human": f"database ready at {db_path}"}


def cmd_person_add(args, conn, db_path):
    person = core.add_person(conn, args.name, slack_id=args.slack_id, slack_handle=args.handle)
    return {"data": person, "human": f"added {person['name']} (id {person['id']})"}


def cmd_person_list(args, conn, db_path):
    people = core.list_people(conn, include_archived=args.include_archived)
    if not people:
        return {"data": {"people": []}, "human": "no people yet"}
    table = render_table(["ID", "NAME", "HANDLE", "SLACK_ID", "ARCHIVED"], person_rows(people))
    return {"data": {"people": people}, "human": table}


def cmd_person_rename(args, conn, db_path):
    person = core.rename_person(conn, args.ref, args.new_name)
    return {"data": person, "human": f"renamed to {person['name']} (id {person['id']})"}


def cmd_person_link(args, conn, db_path):
    person = core.link_person(conn, args.ref, slack_id=args.slack_id, slack_handle=args.handle)
    return {"data": person, "human": f"linked slack identity for {person['name']}"}


def cmd_person_unlink(args, conn, db_path):
    person = core.unlink_person(conn, args.ref)
    return {"data": person, "human": f"cleared slack identity for {person['name']}"}


def cmd_person_archive(args, conn, db_path):
    person = core.set_archived(conn, args.ref, True)
    return {"data": person, "human": f"archived {person['name']}"}


def cmd_person_unarchive(args, conn, db_path):
    person = core.set_archived(conn, args.ref, False)
    return {"data": person, "human": f"unarchived {person['name']}"}


def cmd_person_delete(args, conn, db_path):
    person = core.delete_person(conn, args.ref)
    return {"data": person, "human": f"deleted {person['name']}"}


def parse_share_arg(text):
    ref, sep, amount = text.partition("=")
    if not sep or not ref.strip():
        raise argparse.ArgumentTypeError("expected NAME=AMOUNT, e.g. alice=12.50")
    return ref.strip(), amount.strip()


def parse_split_arg(text):
    return [part.strip() for part in text.split(",") if part.strip()]


def build_shares(share_args):
    return [(ref, core.parse_amount(amount)) for ref, amount in (share_args or [])]


def cmd_expense_add(args, conn, db_path):
    expense = core.add_expense(
        conn,
        payer=args.payer,
        amount_cents=core.parse_amount(args.amount),
        description=args.desc,
        split=parse_split_arg(args.split) if args.split else None,
        shares=build_shares(args.share) or None,
        include_all=args.all,
        spent_at=args.spent_at,
        category=args.category,
        source=args.source,
    )
    return {"data": expense, "human": f"added {expense_line(expense)}"}


def cmd_expense_list(args, conn, db_path):
    expenses = core.list_expenses(
        conn,
        person=args.person,
        since=args.since,
        until=args.until,
        category=args.category,
        include_voided=args.include_voided,
        limit=args.limit,
    )
    if not expenses:
        return {"data": {"expenses": []}, "human": "no expenses found"}
    return {
        "data": {"expenses": expenses},
        "human": expense_table(expenses, include_voided=args.include_voided),
    }


def cmd_expense_show(args, conn, db_path):
    expense = core.get_expense(conn, args.id)
    return {"data": expense, "human": expense_detail(expense)}


def cmd_expense_void(args, conn, db_path):
    expense = core.void_expense(conn, args.id, args.reason)
    return {"data": expense, "human": f"voided expense #{expense['id']}: {args.reason}"}


def cmd_expense_correct(args, conn, db_path):
    expense = core.correct_expense(
        conn,
        args.id,
        reason=args.reason,
        payer=args.payer,
        amount_cents=core.parse_amount(args.amount) if args.amount is not None else None,
        description=args.desc,
        split=parse_split_arg(args.split) if args.split else None,
        shares=build_shares(args.share) or None,
        include_all=args.all,
        spent_at=args.spent_at,
        category=args.category,
        source=args.source,
    )
    return {
        "data": expense,
        "human": f"corrected expense #{args.id}, added {expense_line(expense)}",
    }


def cmd_settle_add(args, conn, db_path):
    settlement = core.add_settlement(
        conn,
        from_ref=args.from_ref,
        to_ref=args.to,
        amount_cents=core.parse_amount(args.amount),
        note=args.note,
        source=args.source,
    )
    return {"data": settlement, "human": f"recorded {settlement_line(settlement)}"}


def cmd_settle_list(args, conn, db_path):
    settlements = core.list_settlements(
        conn, person=args.person, include_voided=args.include_voided, limit=args.limit
    )
    if not settlements:
        return {"data": {"settlements": []}, "human": "no settlements found"}
    headers = ["ID", "FROM", "TO", "AMOUNT", "NOTE"]
    if args.include_voided:
        headers.append("VOIDED")
    rows = []
    for s in settlements:
        row = [s["id"], s["from"]["name"], s["to"]["name"], s["amount"], s["note"] or ""]
        if args.include_voided:
            row.append("yes" if s["voided"] else "")
        rows.append(row)
    return {"data": {"settlements": settlements}, "human": render_table(headers, rows)}


def cmd_settle_show(args, conn, db_path):
    settlement = core.get_settlement(conn, args.id)
    lines = [settlement_line(settlement)]
    if settlement["source"]:
        lines.append(f"  source    {settlement['source']}")
    lines.append(f"  created   {settlement['created_at']}")
    if settlement["voided"]:
        lines.append(f"  VOIDED    {settlement['void_reason']}")
    if settlement["superseded_by"] is not None:
        lines.append(f"  replaced by settlement #{settlement['superseded_by']}")
    return {"data": settlement, "human": "\n".join(lines)}


def cmd_settle_void(args, conn, db_path):
    settlement = core.void_settlement(conn, args.id, args.reason)
    return {"data": settlement, "human": f"voided settlement #{settlement['id']}: {args.reason}"}


def cmd_settle_correct(args, conn, db_path):
    settlement = core.correct_settlement(
        conn,
        args.id,
        reason=args.reason,
        from_ref=args.from_ref,
        to_ref=args.to,
        amount_cents=core.parse_amount(args.amount) if args.amount is not None else None,
        note=args.note,
        source=args.source,
    )
    return {
        "data": settlement,
        "human": f"corrected settlement #{args.id}, recorded {settlement_line(settlement)}",
    }


def cmd_balances(args, conn, db_path):
    items = core.balances(conn)
    if args.person is not None:
        row = core.resolve_person(conn, args.person, allow_archived=True)
        item = next(i for i in items if i["person"]["id"] == row["id"])
        return {
            "data": {"currency": core.CURRENCY, "balances": [item]},
            "human": f"{item['person']['name']}: {signed(item['balance_cents'])}",
        }
    if not items:
        return {"data": {"currency": core.CURRENCY, "balances": []}, "human": "no people yet"}
    rows = [[i["person"]["name"], signed(i["balance_cents"])] for i in items]
    return {
        "data": {"currency": core.CURRENCY, "balances": items},
        "human": render_table(["PERSON", "BALANCE"], rows),
    }


def cmd_settle_up(args, conn, db_path):
    transfers = core.settle_up(conn)
    data = {"currency": core.CURRENCY, "transfers": transfers}
    if not transfers:
        return {"data": data, "human": "all settled"}
    lines = [
        f"{t['from']['name']} pays {t['to']['name']} {t['amount']} {core.CURRENCY}"
        for t in transfers
    ]
    return {"data": data, "human": "\n".join(lines)}


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--db",
        metavar="PATH",
        help="path to the SQLite database (default: $IOU_DB or ./iou.db)",
    )
    common.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="print machine-readable JSON",
    )

    parser = argparse.ArgumentParser(
        prog="iou",
        description="Debt tracking for small teams: expenses, splits and settlements.",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("init", parents=[common], help="create the database and schema")
    p.set_defaults(func=cmd_init)

    person = sub.add_parser("person", help="manage people")
    psub = person.add_subparsers(dest="person_command", required=True, metavar="ACTION")

    p = psub.add_parser("add", parents=[common], help="add a person")
    p.add_argument("name")
    p.add_argument("--slack-id", dest="slack_id", help="Slack member id, e.g. U0123ABCDEF")
    p.add_argument("--handle", help="Slack handle without the leading @")
    p.set_defaults(func=cmd_person_add)

    p = psub.add_parser("list", parents=[common], help="list people")
    p.add_argument("--include-archived", action="store_true")
    p.set_defaults(func=cmd_person_list)

    p = psub.add_parser("rename", parents=[common], help="rename a person")
    p.add_argument("ref", help="name, @handle or slack id")
    p.add_argument("new_name")
    p.set_defaults(func=cmd_person_rename)

    p = psub.add_parser("link", parents=[common], help="attach slack identity to a person")
    p.add_argument("ref", help="name, @handle or slack id")
    p.add_argument("--slack-id", dest="slack_id")
    p.add_argument("--handle")
    p.set_defaults(func=cmd_person_link)

    p = psub.add_parser("unlink", parents=[common], help="clear a person's slack identity")
    p.add_argument("ref", help="name, @handle or slack id")
    p.set_defaults(func=cmd_person_unlink)

    p = psub.add_parser("archive", parents=[common], help="archive a person")
    p.add_argument("ref", help="name, @handle or slack id")
    p.set_defaults(func=cmd_person_archive)

    p = psub.add_parser("unarchive", parents=[common], help="unarchive a person")
    p.add_argument("ref", help="name, @handle or slack id")
    p.set_defaults(func=cmd_person_unarchive)

    p = psub.add_parser(
        "delete", parents=[common], help="delete a person with no transactions"
    )
    p.add_argument("ref", help="name, @handle or slack id")
    p.set_defaults(func=cmd_person_delete)

    expense = sub.add_parser("expense", help="record and inspect expenses")
    esub = expense.add_subparsers(dest="expense_command", required=True, metavar="ACTION")

    p = esub.add_parser("add", parents=[common], help="add an expense")
    p.add_argument("--payer", required=True, help="name, @handle or slack id of the payer")
    p.add_argument("--amount", required=True, help="total amount, e.g. 42.50")
    p.add_argument("--desc", required=True, help="what the expense was for")
    p.add_argument("--split", help="comma-separated people to split equally among")
    p.add_argument("--all", action="store_true", help="split equally among all active people")
    p.add_argument(
        "--share",
        dest="share",
        action="append",
        type=parse_share_arg,
        metavar="PERSON=AMOUNT",
        help="exact share, repeatable; must sum to --amount",
    )
    p.add_argument("--spent-at", dest="spent_at", help="YYYY-MM-DD, today or yesterday")
    p.add_argument("--category", help="e.g. lunch, taxi")
    p.add_argument("--source", help="origin reference, e.g. a Slack message permalink")
    p.set_defaults(func=cmd_expense_add)

    p = esub.add_parser("list", parents=[common], help="list expenses")
    p.add_argument("--person", help="name, @handle or slack id")
    p.add_argument("--since", help="YYYY-MM-DD, today or yesterday")
    p.add_argument("--until", help="YYYY-MM-DD, today or yesterday")
    p.add_argument("--category")
    p.add_argument("--include-voided", action="store_true")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_expense_list)

    p = esub.add_parser("show", parents=[common], help="show one expense")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_expense_show)

    p = esub.add_parser("void", parents=[common], help="void an expense")
    p.add_argument("id", type=int)
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_expense_void)

    p = esub.add_parser(
        "correct",
        parents=[common],
        help="replace an expense with a corrected copy; the original is voided and linked",
    )
    p.add_argument("id", type=int)
    p.add_argument("--reason", required=True)
    p.add_argument("--payer")
    p.add_argument("--amount")
    p.add_argument("--desc")
    p.add_argument("--split")
    p.add_argument("--all", action="store_true")
    p.add_argument(
        "--share", dest="share", action="append", type=parse_share_arg, metavar="PERSON=AMOUNT"
    )
    p.add_argument("--spent-at", dest="spent_at")
    p.add_argument("--category")
    p.add_argument("--source", default=core.UNSET)
    p.set_defaults(func=cmd_expense_correct)

    settle = sub.add_parser("settle", help="record and inspect repayments")
    ssub = settle.add_subparsers(dest="settle_command", required=True, metavar="ACTION")

    p = ssub.add_parser(
        "add", parents=[common], help="record that FROM repaid TO, i.e. FROM owed TO"
    )
    p.add_argument("from_ref", metavar="FROM", help="name, @handle or slack id of the debtor")
    p.add_argument("to", metavar="TO", help="name, @handle or slack id of the creditor")
    p.add_argument("amount")
    p.add_argument("--note")
    p.add_argument("--source", help="origin reference, e.g. a Slack message permalink")
    p.set_defaults(func=cmd_settle_add)

    p = ssub.add_parser("list", parents=[common], help="list settlements")
    p.add_argument("--person", help="name, @handle or slack id")
    p.add_argument("--include-voided", action="store_true")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_settle_list)

    p = ssub.add_parser("show", parents=[common], help="show one settlement")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_settle_show)

    p = ssub.add_parser("void", parents=[common], help="void a settlement")
    p.add_argument("id", type=int)
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_settle_void)

    p = ssub.add_parser(
        "correct",
        parents=[common],
        help="replace a settlement with a corrected copy; the original is voided and linked",
    )
    p.add_argument("id", type=int)
    p.add_argument("--reason", required=True)
    p.add_argument("--from", dest="from_ref", metavar="FROM")
    p.add_argument("--to", dest="to", metavar="TO")
    p.add_argument("--amount")
    p.add_argument("--note", default=core.UNSET)
    p.add_argument("--source", default=core.UNSET)
    p.set_defaults(func=cmd_settle_correct)

    p = sub.add_parser("balances", parents=[common], help="net balance per person")
    p.add_argument("--person", help="name, @handle or slack id")
    p.set_defaults(func=cmd_balances)

    p = sub.add_parser(
        "settle-up", parents=[common], help="suggested transfers to settle all debts"
    )
    p.set_defaults(func=cmd_settle_up)

    return parser


def fail(error: core.IOUError, json_output: bool) -> int:
    if json_output:
        payload = {"ok": False, "error": {"code": error.code, "message": error.message}}
        payload["error"].update(error.details)
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(f"error: {error.message}", file=sys.stderr)
    return 1


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        db_path = db.resolve_db_path(args.db)
        conn = db.connect(db_path)
    except (OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    try:
        result = args.func(args, conn, db_path)
    except core.IOUError as error:
        return fail(error, args.json_output)
    except Exception as error:
        if args.json_output:
            print(
                json.dumps(
                    {"ok": False, "error": {"code": "internal", "message": str(error)}},
                    indent=2,
                )
            )
        else:
            print(f"internal error: {error}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    if args.json_output:
        print(json.dumps({"ok": True, "data": result["data"]}, indent=2))
    else:
        print(result["human"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
