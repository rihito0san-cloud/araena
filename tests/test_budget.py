"""
Budget ledger: cap enforcement, atomic reserve/release, restart safety.

All amounts are wei integers. A cap of 0 means "cannot spend", and the ledger
must refuse rather than fall back to some default.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "controller"))

from budget import BudgetError, Ledger  # noqa: E402

ETH = 10 ** 18


@pytest.fixture()
def led(tmp_path):
    l = Ledger(str(tmp_path / "test.sqlite"))
    yield l
    l.close()


def reserve_args(**kw):
    base = dict(wallet="0x" + "aa" * 20, value_wei=8 * 10 ** 15,
                gas_limit=300_000, max_fee_wei=10 ** 9)
    base.update(kw)
    return base


def test_new_ledger_starts_with_zero_cap_and_cannot_spend(led):
    assert led.cap == 0
    assert led.snapshot()["cap_set"] is False
    with pytest.raises(BudgetError, match="no spending cap"):
        led.reserve(**reserve_args())


def test_cap_set_by_operator_allows_within_limit(led):
    led.set_cap(ETH // 10)
    row = led.reserve(**reserve_args())
    assert row > 0
    assert led.reserved > 0
    assert led.spent == 0


def test_liability_includes_worst_case_gas(led):
    led.set_cap(ETH)
    value, gas, fee = 8 * 10 ** 15, 300_000, 2 * 10 ** 9
    led.reserve(value_wei=value, gas_limit=gas, max_fee_wei=fee,
                wallet="0x" + "aa" * 20)
    assert led.reserved == value + gas * fee


def test_cap_refuses_to_be_exceeded(led):
    led.set_cap(ETH // 100)  # 0.01 ETH
    # 0.008 ETH value plus gas at 1 gwei for 300k gas = 0.0083 ETH, fits once.
    led.reserve(**reserve_args())
    with pytest.raises(BudgetError, match="cap exceeded"):
        led.reserve(**reserve_args())


def test_lowering_cap_below_obligations_is_refused(led):
    led.set_cap(ETH)
    led.reserve(**reserve_args())
    obligations = led.spent + led.reserved
    with pytest.raises(BudgetError, match="below existing obligations"):
        led.set_cap(obligations - 1)
    # The cap is unchanged, and obligations are intact.
    assert led.cap == ETH
    assert led.reserved == obligations


def test_changing_cap_does_not_reset_spent(led):
    led.set_cap(ETH)
    row = led.reserve(**reserve_args(value_wei=10 ** 15))
    led.settle_success(row, gas_used_wei=10 ** 14, value_wei=10 ** 15,
                       token_id=42)
    spent_before = led.spent
    led.set_cap(2 * ETH)
    assert led.spent == spent_before > 0


def test_settle_success_books_gas_plus_value_once(led):
    led.set_cap(ETH)
    row = led.reserve(**reserve_args(value_wei=10 ** 15))
    assert led.settle_success(row, gas_used_wei=10 ** 14, value_wei=10 ** 15,
                              token_id=7) is True
    assert led.spent == 10 ** 15 + 10 ** 14
    assert led.reserved == 0
    # A replayed receipt must not double-book.
    assert led.settle_success(row, gas_used_wei=10 ** 14, value_wei=10 ** 15) is False
    assert led.spent == 10 ** 15 + 10 ** 14


def test_settle_failure_books_gas_only(led):
    """A reverted mint still burned gas but returned the price."""
    led.set_cap(ETH)
    row = led.reserve(**reserve_args(value_wei=8 * 10 ** 15))
    assert led.settle_failure(row, gas_used_wei=2 * 10 ** 14) is True
    assert led.spent == 2 * 10 ** 14
    assert led.reserved == 0


def test_release_before_broadcast_frees_reservation(led):
    led.set_cap(ETH // 100)
    row = led.reserve(**reserve_args())
    led.release(row, "never broadcast")
    assert led.reserved == 0
    # The freed allowance can be used again.
    assert led.reserve(**reserve_args()) > 0


def test_open_txs_visible_for_restart_reconciliation(led):
    led.set_cap(ETH)
    r1 = led.reserve(**reserve_args())
    r2 = led.reserve(**reserve_args())
    led.mark_broadcast(r1, "0x" + "ab" * 32, wallet_nonce=5)
    led.release(r2, "dropped before broadcast")
    open_rows = led.open_txs()
    assert [r["id"] for r in open_rows] == [r1]
    assert open_rows[0]["tx_hash"] == "0x" + "ab" * 32


def test_own_pending_blocks_a_second_reservation(led):
    led.set_cap(ETH)
    w = "0x" + "cc" * 20
    led.reserve(**reserve_args(wallet=w))
    assert len(led.pending_by_wallet(w)) == 1


def test_wallet_and_signer_selection(led):
    addr = "0x" + "dE" * 20
    led.add_wallet(addr, "main")
    led.set_active_signer(addr)
    assert led.active_signer == addr.lower()
    led.clear_active_signer()
    assert led.active_signer in (None, "")


def test_survives_reopen_without_losing_state(tmp_path):
    path = str(tmp_path / "persist.sqlite")
    l = Ledger(path)
    l.set_cap(ETH)
    row = l.reserve(**reserve_args())
    l.close()

    l2 = Ledger(path)
    assert l2.cap == ETH
    assert [r["id"] for r in l2.open_txs()] == [row]
    assert l2.reserved > 0
    l2.close()
