import pytest
from conftest import balances_by_name

from iou import core


def test_parse_amount():
    assert core.parse_amount("42.50") == 4250
    assert core.parse_amount("42,50") == 4250
    assert core.parse_amount("42") == 4200
    assert core.parse_amount("42.5") == 4250
    assert core.parse_amount("0.01") == 1
    assert core.parse_amount("1e2") == 10000
    assert core.parse_amount(".5") == 50
    assert core.parse_amount("5.") == 500
    assert core.parse_amount("+5") == 500
    assert core.parse_amount("9" * 26 + ".99") == int("9" * 26 + "99")


@pytest.mark.parametrize(
    "bad",
    ["0", "0.00", "-3", "1.999", "abc", "", "nan", "1_000", "9" * 26 + ".999"],
)
def test_parse_amount_rejects_bad_input(bad):
    with pytest.raises(core.InvalidAmount):
        core.parse_amount(bad)


def test_equal_split_rounding(conn, three_people):
    expense = core.add_expense(
        conn, payer="Alice", amount_cents=10000, description="lunch",
        split=["Alice", "Bob", "Carol"],
    )
    assert {s["person"]["name"]: s["amount_cents"] for s in expense["shares"]} == {
        "Alice": 3334, "Bob": 3333, "Carol": 3333,
    }
    assert expense["split_mode"] == "equal"


def test_equal_split_leftover_follows_list_order(conn, three_people):
    expense = core.add_expense(
        conn, payer="Carol", amount_cents=10000, description="lunch",
        split=["Carol", "Alice", "Bob"],
    )
    assert {s["person"]["name"]: s["amount_cents"] for s in expense["shares"]} == {
        "Carol": 3334, "Alice": 3333, "Bob": 3333,
    }


def test_equal_split_too_small(conn, three_people):
    with pytest.raises(core.InvalidSplit):
        core.add_expense(
            conn, payer="Alice", amount_cents=2, description="candy",
            split=["Alice", "Bob", "Carol"],
        )


def test_exact_split_mismatch(conn, three_people):
    with pytest.raises(core.SumMismatch):
        core.add_expense(
            conn, payer="Alice", amount_cents=3000, description="lunch",
            shares=[("Alice", 1000), ("Bob", 1500)],
        )


def test_exact_split_ok(conn, three_people):
    expense = core.add_expense(
        conn, payer="Alice", amount_cents=3000, description="lunch",
        shares=[("Alice", 1000), ("Bob", 1500), ("Carol", 500)],
    )
    assert expense["split_mode"] == "exact"
    assert {s["person"]["name"]: s["amount_cents"] for s in expense["shares"]} == {
        "Alice": 1000, "Bob": 1500, "Carol": 500,
    }


def test_duplicate_person_in_split(conn, three_people):
    with pytest.raises(core.InvalidSplit):
        core.add_expense(
            conn, payer="Alice", amount_cents=1000, description="lunch",
            split=["Alice", "Alice"],
        )


def test_payer_outside_split(conn, three_people):
    core.add_expense(
        conn, payer="Alice", amount_cents=6000, description="lunch", split=["Bob", "Carol"]
    )
    assert balances_by_name(conn) == {"Alice": 6000, "Bob": -3000, "Carol": -3000}


def test_balance_invariant_zero(conn, three_people):
    core.add_expense(
        conn, payer="Alice", amount_cents=10000, description="a", split=["Alice", "Bob"]
    )
    core.add_expense(conn, payer="Bob", amount_cents=3333, description="b", split=["Bob", "Carol"])
    core.add_settlement(conn, from_ref="Carol", to_ref="Alice", amount_cents=500)
    total = sum(item["balance_cents"] for item in core.balances(conn))
    assert total == 0


def test_void_expense_excluded_from_balances(conn, three_people):
    expense = core.add_expense(
        conn, payer="Alice", amount_cents=6000, description="lunch", split=["Alice", "Bob"]
    )
    core.void_expense(conn, expense["id"], "entered by mistake")
    assert balances_by_name(conn) == {"Alice": 0, "Bob": 0, "Carol": 0}


def test_void_requires_reason(conn, three_people):
    expense = core.add_expense(
        conn, payer="Alice", amount_cents=1000, description="lunch", split=["Alice"]
    )
    with pytest.raises(core.InvalidValue):
        core.void_expense(conn, expense["id"], "")


def test_double_void(conn, three_people):
    expense = core.add_expense(
        conn, payer="Alice", amount_cents=1000, description="lunch", split=["Alice"]
    )
    core.void_expense(conn, expense["id"], "oops")
    with pytest.raises(core.AlreadyVoided):
        core.void_expense(conn, expense["id"], "again")


def test_correct_equal_expense_recomputes_shares(conn, three_people):
    original = core.add_expense(
        conn, payer="Alice", amount_cents=3000, description="lunch",
        split=["Alice", "Bob", "Carol"],
    )
    corrected = core.correct_expense(
        conn, original["id"], reason="amount was wrong", amount_cents=3300
    )
    assert corrected["id"] != original["id"]
    assert corrected["supersedes"] == original["id"]
    assert [s["amount_cents"] for s in corrected["shares"]] == [1100, 1100, 1100]
    refreshed_original = core.get_expense(conn, original["id"])
    assert refreshed_original["voided"] is True
    assert refreshed_original["superseded_by"] == corrected["id"]
    assert balances_by_name(conn) == {"Alice": 2200, "Bob": -1100, "Carol": -1100}


def test_correct_exact_expense_amount_change_requires_shares(conn, three_people):
    original = core.add_expense(
        conn, payer="Alice", amount_cents=3000, description="lunch",
        shares=[("Alice", 1000), ("Bob", 2000)],
    )
    with pytest.raises(core.SumMismatch):
        core.correct_expense(conn, original["id"], reason="x", amount_cents=4000)


def test_correct_exact_expense_keeps_shares_when_amount_same(conn, three_people):
    original = core.add_expense(
        conn, payer="Alice", amount_cents=3000, description="lunch",
        shares=[("Alice", 1000), ("Bob", 2000)],
    )
    corrected = core.correct_expense(
        conn, original["id"], reason="wrong description", description="dinner"
    )
    assert {s["person"]["name"]: s["amount_cents"] for s in corrected["shares"]} == {
        "Alice": 1000, "Bob": 2000,
    }
    assert corrected["description"] == "dinner"


def test_settlement_direction(conn, three_people):
    core.add_expense(
        conn, payer="Alice", amount_cents=3000, description="lunch", split=["Alice", "Bob"]
    )
    core.add_settlement(conn, from_ref="Bob", to_ref="Alice", amount_cents=1500)
    assert balances_by_name(conn) == {"Alice": 0, "Bob": 0, "Carol": 0}


def test_self_settlement_rejected(conn, three_people):
    with pytest.raises(core.SelfSettlement):
        core.add_settlement(conn, from_ref="Alice", to_ref="Alice", amount_cents=100)


def test_settle_up_clears_all_balances(conn, three_people):
    core.add_expense(
        conn, payer="Alice", amount_cents=9000, description="a", split=["Alice", "Bob", "Carol"]
    )
    core.add_expense(conn, payer="Bob", amount_cents=4000, description="b", split=["Bob", "Carol"])
    transfers = core.settle_up(conn)
    assert transfers
    assert len(transfers) <= 2
    for transfer in transfers:
        core.add_settlement(
            conn,
            from_ref=transfer["from"]["name"],
            to_ref=transfer["to"]["name"],
            amount_cents=transfer["amount_cents"],
        )
    assert all(item["balance_cents"] == 0 for item in core.balances(conn))
    assert core.settle_up(conn) == []


def test_resolve_identifiers(conn):
    core.add_person(conn, "Anil", slack_id="U0123ABC", slack_handle="anilkeshwani")
    for ref in ("Anil", "anil", "ANIL", "@anilkeshwani", "anilkeshwani", "U0123ABC"):
        assert core.resolve_person(conn, ref)["name"] == "Anil"


def test_unknown_person_lists_known(conn, three_people):
    with pytest.raises(core.UnknownPerson) as excinfo:
        core.resolve_person(conn, "Dave")
    assert excinfo.value.details["known_people"] == ["Alice", "Bob", "Carol"]


def test_duplicate_name_rejected(conn, three_people):
    with pytest.raises(core.DuplicatePerson):
        core.add_person(conn, "alice")


def test_duplicate_slack_id_rejected(conn, three_people):
    core.link_person(conn, "Alice", slack_id="U999")
    with pytest.raises(core.DuplicatePerson):
        core.link_person(conn, "Bob", slack_id="U999")


def test_link_and_unlink(conn, three_people):
    person = core.link_person(conn, "Alice", slack_id="U111", slack_handle="@alice")
    assert person["slack_id"] == "U111"
    assert person["slack_handle"] == "alice"
    person = core.unlink_person(conn, "Alice")
    assert person["slack_id"] is None
    assert person["slack_handle"] is None


def test_rename(conn, three_people):
    person = core.rename_person(conn, "Alice", "Alicia")
    assert person["name"] == "Alicia"
    assert core.resolve_person(conn, "Alicia")["id"] == person["id"]


def test_archived_person_blocked_from_new_expense(conn, three_people):
    core.set_archived(conn, "Carol", True)
    with pytest.raises(core.ArchivedPerson):
        core.add_expense(
            conn, payer="Alice", amount_cents=1000, description="lunch", split=["Alice", "Carol"]
        )


def test_archived_person_excluded_from_all_split(conn, three_people):
    core.set_archived(conn, "Carol", True)
    expense = core.add_expense(
        conn, payer="Alice", amount_cents=2000, description="lunch", include_all=True
    )
    assert [s["person"]["name"] for s in expense["shares"]] == ["Alice", "Bob"]


def test_archived_person_allowed_in_settlement(conn, three_people):
    core.add_expense(
        conn, payer="Alice", amount_cents=2000, description="lunch", split=["Alice", "Carol"]
    )
    core.set_archived(conn, "Carol", True)
    settlement = core.add_settlement(conn, from_ref="Carol", to_ref="Alice", amount_cents=1000)
    assert settlement["amount_cents"] == 1000


def test_delete_person_blocked_when_transactions_exist(conn, three_people):
    core.add_expense(conn, payer="Alice", amount_cents=1000, description="lunch", split=["Alice"])
    with pytest.raises(core.PersonInUse):
        core.delete_person(conn, "Alice")


def test_delete_person_allowed_when_unused(conn, three_people):
    core.delete_person(conn, "Carol")
    assert [p["name"] for p in core.list_people(conn)] == ["Alice", "Bob"]


def test_list_expenses_filters(conn, three_people):
    a = core.add_expense(
        conn, payer="Alice", amount_cents=1000, description="pizza",
        split=["Alice"], spent_at="2026-08-01", category="lunch",
    )
    b = core.add_expense(
        conn, payer="Bob", amount_cents=2000, description="taxi",
        split=["Bob"], spent_at="2026-08-20", category="taxi",
    )
    ids = [e["id"] for e in core.list_expenses(conn, since="2026-08-10")]
    assert ids == [b["id"]]
    ids = [e["id"] for e in core.list_expenses(conn, category="LUNCH")]
    assert ids == [a["id"]]
    ids = [e["id"] for e in core.list_expenses(conn, person="Alice")]
    assert ids == [a["id"]]


def test_correction_source_inherited_unless_overridden(conn, three_people):
    original = core.add_expense(
        conn, payer="Alice", amount_cents=1000, description="lunch",
        split=["Alice"], source="https://acme.slack.com/archives/C1/p123",
    )
    corrected = core.correct_expense(conn, original["id"], reason="typo", description="lunsh")
    assert corrected["source"] == "https://acme.slack.com/archives/C1/p123"
    corrected2 = core.correct_expense(
        conn, corrected["id"], reason="typo again", description="lunch",
        source="https://acme.slack.com/archives/C1/p456",
    )
    assert corrected2["source"] == "https://acme.slack.com/archives/C1/p456"


def test_correct_settlement(conn, three_people):
    settlement = core.add_settlement(conn, from_ref="Bob", to_ref="Alice", amount_cents=1500)
    corrected = core.correct_settlement(
        conn, settlement["id"], reason="wrong amount", amount_cents=1200
    )
    assert corrected["amount_cents"] == 1200
    assert corrected["supersedes"] == settlement["id"]
    refreshed = core.get_settlement(conn, settlement["id"])
    assert refreshed["voided"] is True
    assert refreshed["superseded_by"] == corrected["id"]


def test_accented_names_are_case_insensitive(conn):
    core.add_person(conn, "Élodie")
    with pytest.raises(core.DuplicatePerson):
        core.add_person(conn, "élodie")
    assert core.resolve_person(conn, "ÉLODIE")["name"] == "Élodie"


def test_handle_casefold_lookup(conn):
    core.add_person(conn, "Anil", slack_handle="anil")
    assert core.resolve_person(conn, "@ANIL")["name"] == "Anil"


def test_conflicting_split_selectors_rejected(conn, three_people):
    with pytest.raises(core.InvalidValue):
        core.add_expense(
            conn, payer="Alice", amount_cents=1000, description="lunch",
            split=["Alice", "Bob"], include_all=True,
        )
    with pytest.raises(core.InvalidValue):
        core.add_expense(
            conn, payer="Alice", amount_cents=1000, description="lunch",
            split=["Alice", "Bob"], shares=[("Alice", 1000)],
        )
