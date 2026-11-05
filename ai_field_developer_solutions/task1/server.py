"""Task 1"""
import asyncio
import json
import logging
import sys
from typing import Annotated

from mcp import MCPError
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    INVALID_PARAMS,
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger("customer-mcp")

CustomerId = Annotated[str, StringConstraints(pattern=r"^CUST-[0-9]{5}$", strict=True)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, strict=True)]


class CustomerInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    customer_id: CustomerId


class RefundInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    customer_id: CustomerId
    amount: float = Field(gt=0, allow_inf_nan=False)
    reason: Reason


# Mock values. Never execute payments from a sample test server.
CUSTOMERS = {
    "CUST-00001": {"customer_id": "CUST-00001", "name": "Example Customer", "status": "active"},
    "CUST-12345": {"customer_id": "CUST-12345", "name": "Sample Buyer", "status": "active"},
}


def validate(model_type: type[BaseModel], arguments: dict | None) -> BaseModel:
    try:
        return model_type.model_validate(arguments or {})
    except ValidationError as exc:
        # The assignment explicitly requests protocol-level invalid-params errors.
        # Only expose field names and error types, not raw values or sensitive data.
        fields = [".".join(str(x) for x in e["loc"]) + ": " + e["type"] for e in exc.errors()]
        raise MCPError(INVALID_PARAMS, "Invalid tool arguments: " + "; ".join(fields)) from None


GET_CUSTOMER = Tool(
    name="get_customer_record",
    description="Look up one customer in the mock customer directory.",
    input_schema=CustomerInput.model_json_schema(),
)
REFUND = Tool(
    name="trigger_refund",
    description="Simulate a refund; no real payment is created.",
    input_schema=RefundInput.model_json_schema(),
)


async def list_tools(ctx: ServerRequestContext, params: PaginatedRequestParams | None) -> ListToolsResult:
    return ListToolsResult(tools=[GET_CUSTOMER, REFUND])


async def call_tool(ctx: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
    if params.name == "get_customer_record":
        args = validate(CustomerInput, params.arguments)
        record = CUSTOMERS.get(args.customer_id)
        if record is None:
            # A valid lookup that finds no record is a tool-level outcome.
            return CallToolResult(
                content=[TextContent(type="text", text="Customer not found")],
                is_error=True,
            )
        result = record
    elif params.name == "trigger_refund":
        args = validate(RefundInput, params.arguments)
        if args.customer_id not in CUSTOMERS:
            return CallToolResult(
                content=[TextContent(type="text", text="Customer not found")],
                is_error=True,
            )
        result = {
            "customer_id": args.customer_id,
            "amount": args.amount,
            "status": "simulated",
            "message": "Refund was NOT sent to a payment processor.",
        }
    else:
        raise MCPError(INVALID_PARAMS, "Unknown tool")
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(result))])


server = Server("customer-refund-demo", on_list_tools=list_tools, on_call_tool=call_tool)


async def main() -> None:
    logger.info("Starting mock MCP server on stdio")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
