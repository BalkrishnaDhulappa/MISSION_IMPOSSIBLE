"""Tests for EMI verification helpers."""

from emi_verify import (
    EmiStatus,
    estimate_funded_from_mtf_block,
    verify_emi_repaid,
)


def test_estimate_funded_from_mtf_block():
    assert estimate_funded_from_mtf_block(15000, 4500) == 10500.0


def test_verify_emi_repaid_success():
    r = verify_emi_repaid(562.5, 9000, 8437.5, tolerance=10)
    assert r.verified
    assert r.funded_drop == 562.5


def test_verify_emi_repaid_failure():
    r = verify_emi_repaid(562.5, 9000, 9000, tolerance=10)
    assert not r.verified


def test_emi_status_values():
    assert EmiStatus.VERIFIED.value == "verified"
