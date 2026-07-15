import pytest
from pydantic import ValidationError

from data_market_probe.api import PasswordChange, UserInput, UserUpdate
from data_market_probe.auth import hash_password, verify_password


def test_six_character_password_is_accepted_and_verifies():
    password_hash = hash_password("abc123")
    assert verify_password("abc123", password_hash)
    PasswordChange(current_password="old-password", new_password="abc123")
    UserInput(username="reader", password="abc123", role="readonly")
    UserUpdate(password="abc123")


@pytest.mark.parametrize("model, payload", [
    (PasswordChange, {"current_password": "old-password", "new_password": "12345"}),
    (UserInput, {"username": "reader", "password": "12345", "role": "readonly"}),
    (UserUpdate, {"password": "12345"}),
])
def test_five_character_password_is_rejected(model, payload):
    with pytest.raises(ValidationError):
        model(**payload)
