# Troubleshooting & FAQ

Common questions and resolution steps for OmniCache.

---

### 1. "My request resulted in a Cache MISS when I expected a Cache HIT"
* **Check Temperature:** If `temperature > 0.0`, creative sampling may bypass semantic caching to preserve output diversity. Set `temperature: 0.0` for deterministic caching.
* **Code Prompt Strictness:** Prompts containing code syntax (`def `, `class `, `SELECT`) use a strict similarity threshold (0.98) to prevent subtle code inaccuracies.
* **Tenant Isolation:** Verify that both requests share the same `X-Org-Id` header (defaults to `"default"`).

---

### 2. "How do I verify the server is running?"
Run the built-in doctor command:
```bash
omnicache doctor
```
Or query the health endpoint:
```bash
curl http://localhost:8000/healthz
```

---

### 3. "Port 8000 is already in use"
Start OmniCache on an alternate port:
```bash
omnicache --port 8080
```
Then configure your client to connect to `http://localhost:8080`.

---

### 4. "How do I clear or invalidate the cache?"
* **Purge all entries:**
  ```bash
  curl -X POST http://localhost:8000/v1/cache/purge
  ```
* **Invalidate by tag:**
  ```bash
  curl -X POST http://localhost:8000/v1/cache/invalidate-tag \
    -H "Content-Type: application/json" \
    -d '{"tag": "my_tag"}'
  ```

---

### 5. "Where is cache data stored?"
By default, entries are persisted to SQLite at `~/.omnicache/omnicache.db`. You can override this location using the `OMNICACHE_DB_PATH` environment variable.
