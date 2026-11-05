"""Mock server for Task 2."""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()


@app.post("/mcp")
async def handle(request: Request):
    data = await request.json()
    method = data.get("method")
    if method == "tools/list":
        result = {"tools": [
            {"name": "get_customer_record", "description": "Mock lookup", "inputSchema": {"type": "object"}},
            {"name": "admin_reset_key", "description": "Simulated admin command", "inputSchema": {"type": "object"}},
        ]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": "Mock tool executed"}], "isError": False}
    else:
        return JSONResponse({"jsonrpc": "2.0", "id": data.get("id"),
                             "error": {"code": -32601, "message": "Method not found"}})
    return {"jsonrpc": "2.0", "id": data.get("id"), "result": result}
