import pytest
pytest.importorskip("mcp", reason="Official MCP SDK not installed in this offline test environment")
from mcp import MCPError
from task1.server import CustomerInput, RefundInput, validate


def test_strict_validators():
    assert validate(CustomerInput, {"customer_id": "CUST-12345"}).customer_id == "CUST-12345"
    for args in ({"customer_id": "CUST-1234"}, {"customer_id": "cust-12345"},
                 {"customer_id": "CUST-12345", "unknown": True}):
        with pytest.raises(MCPError) as error:
            validate(CustomerInput, args)
        assert error.value.code == -32602
    with pytest.raises(MCPError):
        validate(RefundInput, {"customer_id": "CUST-12345", "amount": -5.5, "reason": "too short"})
