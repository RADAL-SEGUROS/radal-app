"""RUT módulo-11 validation and canonical normalization."""
from __future__ import annotations

import pytest

from app.services.identifiers import (
    InvalidRut,
    compute_dv,
    format_rut,
    is_valid_rut,
    normalize_rut,
    validate_rut,
)

# Check-digit-correct Chilean RUTs. The last three exercise the K digit
# (módulo-11 remainder 10) and the one before them the 0 digit (remainder 11).
VALID_RUTS = [
    "76111111-6",
    "99301000-6",
    "96654180-6",
    "12345678-5",
    "11111111-1",
    "5126663-3",
    "5000006-0",
    "5000001-K",
    "5000015-K",
    "5000029-K",
]


class TestComputeDv:
    @pytest.mark.parametrize("rut", VALID_RUTS)
    def test_computes_the_published_check_digit(self, rut: str) -> None:
        body, dv = rut.split("-")
        assert compute_dv(body) == dv

    def test_dv_11_collapses_to_zero(self) -> None:
        # A módulo-11 result of 11 must yield "0", never "11".
        assert compute_dv("5000006") == "0"

    def test_dv_10_is_k(self) -> None:
        # A módulo-11 result of 10 must yield "K", never "10".
        assert compute_dv("5000001") == "K"
        assert compute_dv("5000015") == "K"

    def test_non_numeric_body_is_rejected(self) -> None:
        with pytest.raises(InvalidRut):
            compute_dv("12A45678")


class TestValidRuts:
    @pytest.mark.parametrize("rut", VALID_RUTS)
    def test_accepts_valid_rut(self, rut: str) -> None:
        assert is_valid_rut(rut) is True
        assert validate_rut(rut) == rut

    def test_accepts_dotted_and_spaced_input(self) -> None:
        assert validate_rut(" 76.111.111-6 ") == "76111111-6"

    def test_accepts_input_without_hyphen(self) -> None:
        assert validate_rut("761111116") == "76111111-6"

    def test_lowercase_k_is_accepted_and_uppercased(self) -> None:
        assert validate_rut("5000001-k") == "5000001-K"
        assert is_valid_rut("5.000.001-k") is True


class TestInvalidRuts:
    @pytest.mark.parametrize(
        "rut",
        [
            "76111111-2",   # wrong check digit
            "12345678-9",   # wrong check digit
            "5000001-0",    # should be K
            "5000006-K",    # should be 0
            "5000015-1",    # should be K
        ],
    )
    def test_rejects_wrong_check_digit(self, rut: str) -> None:
        assert is_valid_rut(rut) is False
        with pytest.raises(InvalidRut):
            validate_rut(rut)

    @pytest.mark.parametrize(
        "rut",
        ["", "   ", "-", "K", "abc-1", "76111111-X", "76.111.111-", "1"],
    )
    def test_rejects_malformed_input(self, rut: str) -> None:
        assert is_valid_rut(rut) is False
        with pytest.raises(InvalidRut):
            validate_rut(rut)

    def test_k_in_the_body_is_rejected(self) -> None:
        # K is only ever a check digit, never part of the numeric body.
        with pytest.raises(InvalidRut):
            validate_rut("1303K849-1")


class TestNormalization:
    def test_strips_dots_spaces_and_uppercases(self) -> None:
        assert normalize_rut(" 5.000.001-k ") == "5000001-K"

    def test_strips_leading_zeros(self) -> None:
        assert normalize_rut("0076111111-6") == "76111111-6"

    def test_every_spelling_of_one_rut_normalizes_identically(self) -> None:
        spellings = ["76111111-6", "76.111.111-6", " 76111111-6 ", "0076.111.111-6", "761111116"]
        assert len({normalize_rut(s) for s in spellings}) == 1

    def test_normalize_does_not_validate_the_check_digit(self) -> None:
        # Normalization is a pure string operation; validation is validate_rut's job.
        assert normalize_rut("76111111-2") == "76111111-2"
        assert is_valid_rut("76111111-2") is False

    def test_format_rut_renders_thousands_separators(self) -> None:
        assert format_rut("76111111-6") == "76.111.111-6"
        assert format_rut("5126663-3") == "5.126.663-3"
