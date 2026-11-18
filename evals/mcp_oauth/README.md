# Profile-scoped MCP OAuth integration probe

Run from the repository root with the Python MCP dependencies installed.  
The probe uses disposable HOME/HERMES_HOME directories, a local OAuth/MCP provider, and synthetic credentials.  

```bash
python3 evals/mcp_oauth/backend_http_fixture.py --repo . --output "$PWD/.build/mcp-oauth-receipt.json"
```

The harness exercises production session functions, discovery, dynamic registration, PKCE code exchange, token persistence, and fresh-process authenticated MCP access.  
Wrong state, callback replay, wrong server, wrong profile, and cancellation are negative controls.  
It removes temporary token stores and reports only booleans/paths, never token values.  

Cross-profile callbacks must be rejected even when the session ID and valid state are supplied.  
The original owner must still be able to finish afterward.  
This probe starts local fixture servers and subprocesses, so it requires integration-test authorization and is not a static check.  
