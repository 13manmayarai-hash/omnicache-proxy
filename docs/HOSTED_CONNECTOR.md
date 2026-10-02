# OmniCache Hosted Connector for Claude

OmniCache gives Claude a searchable memory scoped to your account. Save answers, code snippets, configuration notes and documentation, then find and reuse them in later conversations.

- **Connector URL:** `https://omnicache-proxy.onrender.com/mcp`
- **Transport:** Streamable HTTP (MCP)
- **Authentication:** OAuth 2.0 with PKCE and dynamic client registration
- **Privacy policy:** [HOSTED_PRIVACY_POLICY.md](HOSTED_PRIVACY_POLICY.md)
- **Support:** <https://github.com/13manmayarai-hash/omnicache-proxy/issues> or <13manmayarai@gmail.com>

## Getting access

1. In Claude, add OmniCache from the connector directory, or add a custom connector with the URL above.
2. Claude opens the OmniCache sign-in page. Choose **Sign in with Google**. The first time, this creates your own workspace automatically; there is nothing to request or wait for.
3. Approve the connection. Claude can now use the tools below.

If your organization has issued you an OmniCache API key, you can paste it into the same page instead of signing in with Google.

## Tools

| Tool | What it does | Changes data? |
|---|---|---|
| `omnicache_store` | Saves an answer, snippet or note, with an optional tag | Adds an entry |
| `omnicache_query` | Returns a stored answer whose prompt is semantically similar to yours, with a similarity score | Read-only |
| `omnicache_search` | Searches your entries by meaning and lists the closest matches | Read-only |
| `omnicache_invalidate` | Deletes entries with a given tag, or all of your entries if no tag is given | **Destructive** |
| `omnicache_stats` | Shows entry counts, lookups and hit rate for your workspace | Read-only |
| `omnicache_health` | Reports whether the service and its storage are ready | Read-only |

Saved entries expire 7 days after they are saved.

The tool-replay tools in the open-source package (`omnicache_replay_tool`, `omnicache_record_tool`) work on files and git state on your own machine. They are only available when you run OmniCache locally, not through this hosted connector.

## Example prompts

- "Save our standard API rate-limiting config to OmniCache under the tag `networking`."
- "Search OmniCache for anything we stored about SQLite WAL connection pooling."
- "Is there an answer in OmniCache for how we rotate OAuth refresh tokens?"
- "Delete the OmniCache entries tagged `legacy-auth`."

## Your data

Your entries are visible only to your workspace. Sensitive patterns such as emails and API keys are scrubbed before saving, but don't store secrets. See the [privacy policy](HOSTED_PRIVACY_POLICY.md) for what is stored, where, for how long, and how to delete it.

## Running it yourself

To run OmniCache on your own machine or infrastructure instead, see the [README](../README.md) and [mcp/README.md](../mcp/README.md). The repository's `render.yaml` describes the hosted deployment: authentication required, an always-on instance, and a persistent disk.
