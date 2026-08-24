import json

import pytest

from iou.cli import main


def run(tmp_path, capsys, *argv, json_output=False):
    args = list(argv) + ["--db", str(tmp_path / "cli.db")]
    if json_output:
        args.append("--json")
    code = main(args)
    out, err = capsys.readouterr()
    return code, out, err


def add_people(tmp_path, capsys, *names):
    for name in names:
        code, _, _ = run(tmp_path, capsys, "person", "add", name)
        assert code == 0


def test_init_creates_database(tmp_path, capsys):
    code, out, _ = run(tmp_path, capsys, "init")
    assert code == 0
    assert "database ready" in out


def test_json_success_contract(tmp_path, capsys):
    code, out, _ = run(tmp_path, capsys, "person", "add", "Alice", json_output=True)
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["data"]["name"] == "Alice"


def test_json_error_contract(tmp_path, capsys):
    add_people(tmp_path, capsys, "Alice")
    code, out, _ = run(
        tmp_path, capsys,
        "expense", "add", "--payer", "Nobody", "--amount", "10", "--desc", "lunch", "--all",
        json_output=True,
    )
    assert code == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "unknown_person"
    assert "Alice" in payload["error"]["known_people"]


def test_human_error_goes_to_stderr(tmp_path, capsys):
    code, out, err = run(tmp_path, capsys, "balances", "--person", "Nobody")
    assert code == 1
    assert out == ""
    assert err.startswith("error: ")


def test_usage_error_exits_two(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        main(["nonsense", "--db", str(tmp_path / "cli.db")])
    assert excinfo.value.code == 2


def test_full_flow(tmp_path, capsys):
    add_people(tmp_path, capsys, "Alice", "Bob")
    code, out, _ = run(
        tmp_path, capsys,
        "expense", "add", "--payer", "Alice", "--amount", "30.00", "--desc", "Lunch",
        "--split", "Alice,Bob", json_output=True,
    )
    assert code == 0
    expense = json.loads(out)["data"]
    assert expense["amount_cents"] == 3000
    assert sum(s["amount_cents"] for s in expense["shares"]) == 3000

    code, out, _ = run(tmp_path, capsys, "balances", json_output=True)
    items = json.loads(out)["data"]["balances"]
    balances = {b["person"]["name"]: b["balance_cents"] for b in items}
    assert balances == {"Alice": 1500, "Bob": -1500}

    code, out, _ = run(tmp_path, capsys, "settle-up", json_output=True)
    transfers = json.loads(out)["data"]["transfers"]
    assert transfers == [
        {
            "from": transfers[0]["from"],
            "to": transfers[0]["to"],
            "amount_cents": 1500,
            "amount": "15.00",
        }
    ]
    assert transfers[0]["from"]["name"] == "Bob"
    assert transfers[0]["to"]["name"] == "Alice"

    code, _, _ = run(tmp_path, capsys, "settle", "add", "Bob", "Alice", "15.00")
    assert code == 0

    code, out, _ = run(tmp_path, capsys, "balances", json_output=True)
    items = json.loads(out)["data"]["balances"]
    balances = {b["person"]["name"]: b["balance_cents"] for b in items}
    assert balances == {"Alice": 0, "Bob": 0}

    code, out, _ = run(tmp_path, capsys, "settle-up", json_output=True)
    assert json.loads(out)["data"]["transfers"] == []


def test_slack_identity_resolution_via_cli(tmp_path, capsys):
    code, _, _ = run(
        tmp_path, capsys,
        "person", "add", "Anil", "--slack-id", "U0123", "--handle", "@anil",
    )
    assert code == 0
    code, _, _ = run(
        tmp_path, capsys,
        "expense", "add", "--payer", "U0123", "--amount", "10.00", "--desc", "Lunch",
        "--split", "@anil",
    )
    assert code == 0
    code, out, _ = run(tmp_path, capsys, "balances", "--person", "anil", json_output=True)
    assert json.loads(out)["data"]["balances"][0]["balance_cents"] == 0


def test_expense_void_and_correct_via_cli(tmp_path, capsys):
    add_people(tmp_path, capsys, "Alice", "Bob")
    code, out, _ = run(
        tmp_path, capsys,
        "expense", "add", "--payer", "Alice", "--amount", "20.00", "--desc", "Lunch",
        "--split", "Alice,Bob", json_output=True,
    )
    expense_id = json.loads(out)["data"]["id"]

    code, out, _ = run(
        tmp_path, capsys,
        "expense", "correct", str(expense_id), "--reason", "amount wrong", "--amount", "30.00",
        json_output=True,
    )
    assert code == 0
    corrected = json.loads(out)["data"]
    assert corrected["amount_cents"] == 3000
    assert corrected["shares"][0]["amount_cents"] + corrected["shares"][1]["amount_cents"] == 3000

    code, out, _ = run(tmp_path, capsys, "expense", "show", str(expense_id), json_output=True)
    original = json.loads(out)["data"]
    assert original["voided"] is True
    assert original["superseded_by"] == corrected["id"]

    code, out, _ = run(
        tmp_path, capsys, "expense", "void", str(corrected["id"]), "--reason", "duplicate",
        json_output=True,
    )
    assert code == 0
    code, out, _ = run(
        tmp_path, capsys, "expense", "list", "--include-voided", json_output=True
    )
    expenses = json.loads(out)["data"]["expenses"]
    assert len(expenses) == 2
    assert all(e["voided"] for e in expenses)

    code, out, _ = run(tmp_path, capsys, "expense", "list", json_output=True)
    assert json.loads(out)["data"]["expenses"] == []


def test_share_sum_mismatch_via_cli(tmp_path, capsys):
    add_people(tmp_path, capsys, "Alice", "Bob")
    code, out, _ = run(
        tmp_path, capsys,
        "expense", "add", "--payer", "Alice", "--amount", "30.00", "--desc", "Lunch",
        "--share", "Alice=10.00", "--share", "Bob=10.00", json_output=True,
    )
    assert code == 1
    assert json.loads(out)["error"]["code"] == "sum_mismatch"


def test_source_recorded_via_cli(tmp_path, capsys):
    add_people(tmp_path, capsys, "Alice")
    permalink = "https://acme.slack.com/archives/C1/p123"
    code, out, _ = run(
        tmp_path, capsys,
        "expense", "add", "--payer", "Alice", "--amount", "10.00", "--desc", "Lunch",
        "--split", "Alice", "--source", permalink, json_output=True,
    )
    assert code == 0
    assert json.loads(out)["data"]["source"] == permalink


def test_person_list_shows_slack_columns(tmp_path, capsys):
    run(tmp_path, capsys, "person", "add", "Anil", "--slack-id", "U0123", "--handle", "anil")
    code, out, _ = run(tmp_path, capsys, "person", "list")
    assert code == 0
    assert "anil" in out
    assert "U0123" in out


def test_settle_correct_via_cli(tmp_path, capsys):
    add_people(tmp_path, capsys, "Alice", "Bob")
    code, out, _ = run(
        tmp_path, capsys, "settle", "add", "Bob", "Alice", "15.00", json_output=True
    )
    settlement_id = json.loads(out)["data"]["id"]
    code, out, _ = run(
        tmp_path, capsys,
        "settle", "correct", str(settlement_id), "--reason", "wrong amount", "--amount", "12.00",
        json_output=True,
    )
    assert code == 0
    assert json.loads(out)["data"]["amount_cents"] == 1200


def test_io_not_created_on_help():
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0


def test_directory_as_db_returns_json_error(tmp_path, capsys):
    code = main(["balances", "--db", str(tmp_path), "--json"])
    out, _ = capsys.readouterr()
    assert code == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "db_error"


def test_corrupt_db_returns_json_error(tmp_path, capsys):
    bad = tmp_path / "bad.db"
    bad.write_text("this is not a sqlite database")
    code = main(["balances", "--db", str(bad), "--json"])
    out, _ = capsys.readouterr()
    assert code == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "db_error"


def test_db_error_without_json_goes_to_stderr(tmp_path, capsys):
    code = main(["balances", "--db", str(tmp_path)])
    out, err = capsys.readouterr()
    assert code == 1
    assert out == ""
    assert err.startswith("error: ")


def test_empty_db_flag_is_usage_error(tmp_path, capsys):
    code = main(["balances", "--db", ""])
    _, err = capsys.readouterr()
    assert code == 2
    assert "usage error" in err


def test_negative_limit_is_usage_error(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        main(["expense", "list", "--limit", "-1", "--db", str(tmp_path / "cli.db")])
    assert excinfo.value.code == 2


def test_conflicting_split_selectors_via_cli(tmp_path, capsys):
    add_people(tmp_path, capsys, "Alice", "Bob")
    code, out, _ = run(
        tmp_path, capsys,
        "expense", "add", "--payer", "Alice", "--amount", "10.00", "--desc", "Lunch",
        "--all", "--split", "Alice,Bob", json_output=True,
    )
    assert code == 1
    assert json.loads(out)["error"]["code"] == "invalid_value"


def test_person_link_unlink_archive_delete_via_cli(tmp_path, capsys):
    add_people(tmp_path, capsys, "Alice", "Bob")
    code, out, _ = run(
        tmp_path, capsys,
        "person", "link", "Alice", "--handle", "alice", "--slack-id", "U001",
        json_output=True,
    )
    assert code == 0
    linked = json.loads(out)["data"]
    assert linked["slack_handle"] == "alice"
    assert linked["slack_id"] == "U001"

    code, out, _ = run(tmp_path, capsys, "person", "unlink", "U001", json_output=True)
    assert code == 0
    assert json.loads(out)["data"]["slack_id"] is None

    code, out, _ = run(tmp_path, capsys, "person", "link", "Alice", "--slack-id", "U001")
    assert code == 0
    code, out, _ = run(tmp_path, capsys, "person", "archive", "Bob", json_output=True)
    assert json.loads(out)["data"]["archived"] is True
    code, out, _ = run(tmp_path, capsys, "person", "unarchive", "Bob", json_output=True)
    assert json.loads(out)["data"]["archived"] is False

    code, _, _ = run(tmp_path, capsys, "person", "delete", "Bob")
    assert code == 0
    code, out, _ = run(tmp_path, capsys, "person", "list", json_output=True)
    assert [p["name"] for p in json.loads(out)["data"]["people"]] == ["Alice"]


def test_init_json(tmp_path, capsys):
    code, out, _ = run(tmp_path, capsys, "init", json_output=True)
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["data"]["db"].endswith("cli.db")
