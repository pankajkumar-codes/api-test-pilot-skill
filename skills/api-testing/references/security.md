# Security testing (stub — build in Phase 2)

Map to OWASP API Security Top 10. TODO checks:
- Broken object-level authorization (BOLA/IDOR): user A accessing user B's objects
- Broken authentication: missing/expired/tampered tokens
- Mass assignment, excessive data exposure
- Injection (SQL/NoSQL/command) in params and bodies
- Rate limiting and resource consumption
- Security headers, verbose errors, CORS

Only test APIs the user owns or is authorized to test.
