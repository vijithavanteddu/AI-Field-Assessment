"""Mint a local TEST token. Never put signing secrets in source control."""
import os
import sys
import time
import jwt

role = sys.argv[1] if len(sys.argv) > 1 else "viewer"
if role not in ("viewer", "admin"):
    raise SystemExit("Role must be viewer or admin")
secret = os.environ["GATEWAY_JWT_SECRET"]
print(jwt.encode({"role": role, "exp": int(time.time()) + 3600,
                  "aud": "mcp-gateway", "iss": "assessment-gateway"},
                 secret, algorithm="HS256"))
