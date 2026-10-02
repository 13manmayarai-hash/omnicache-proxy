# OmniCache Hosted Connector — Privacy Policy

**Applies to:** the hosted OmniCache MCP connector at `https://omnicache.rawwgrid.com/mcp`, used from Claude or other MCP clients.
**Does not apply to:** the open-source package you install and run yourself (`pip install omnicache-proxy`). That runs on your own machine and is covered by [PRIVACY.md](../PRIVACY.md).
**Operator:** Rajiv Prasad (<13manmayarai@gmail.com>)
**Effective date:** October 1, 2026

## 1. What we collect

| Data | When | Why |
|---|---|---|
| Entries you save: the prompt or title, the answer or snippet, an optional tag and model label, and a numeric embedding of the text | When you (or Claude on your behalf) call `omnicache_store` | To return them to you from `omnicache_query` and `omnicache_search` |
| Your Google account email and a generated workspace name and ID | When you sign in with Google | To create your account and keep your entries separate from everyone else's |
| OAuth client registration details and access tokens | When an MCP client such as Claude connects | To authenticate requests from that client |
| An audit record per tool call: tool name, your workspace ID, duration and success or failure. It does not include the content of your prompts or entries | Every tool call | Security and abuse investigation |
| Usage counters: number of entries, lookups and hit rate | Every lookup | Shown to you by `omnicache_stats` |

Text you look up with `omnicache_query` or `omnicache_search` is compared against your saved entries and is not stored.

Before an entry is saved, OmniCache automatically scrubs common sensitive patterns such as email addresses, API keys and tokens, replacing them with placeholders. This is a safety net, not a guarantee: don't save secrets.

We don't sell your data, use it for advertising, or use it to train models. The service has no third-party analytics or tracking.

## 2. Who can see it

- Your entries are tied to your workspace. Other users of the connector can't read, search or delete them.
- The operator can access the server and its database for maintenance, security and support.
- Data is not shared with third parties, except the hosting provider (Render) that runs the server and stores the database, and Google when you choose Google sign-in.

## 3. Where it is stored

Data is stored in a SQLite database on a persistent disk attached to the service on Render, in the Oregon (US West) region. Connections to the service use HTTPS.

## 4. How long we keep it

- **Saved entries** expire automatically **7 days** after they are saved. You can delete them sooner at any time.
- **Account records** (your Google email, workspace ID, access tokens) are kept until you ask us to delete your account.
- **Audit records** are kept for 90 days and then deleted.

## 5. Deleting your data

- **Delete entries yourself:** ask Claude to use `omnicache_invalidate`. With a tag it deletes the entries carrying that tag; without one it deletes all of your entries. Deletion takes effect immediately.
- **Delete your account:** email <13manmayarai@gmail.com> from the address you signed in with. We will delete your account, entries and tokens within 30 days and confirm by email.
- **Disconnect:** removing the connector in Claude stops further access but does not delete stored data. Use the steps above to delete it.

## 6. Children

The service is not directed at children under 13 (or the minimum age in your country), and we don't knowingly collect their data.

## 7. Changes

We will update the effective date above when this policy changes. Material changes will be announced in the project's GitHub repository.

## 8. Contact

Questions or requests: <13manmayarai@gmail.com>, or open an issue at <https://github.com/13manmayarai-hash/omnicache-proxy/issues>. Don't post personal data in a public issue.
