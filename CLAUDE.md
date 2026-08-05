# CLAUDE.md

## GPT Driver MCP — Orchestrator Communication

You are a coding agent that reports to a ChatGPT orchestrator via the `gpt-driver` MCP server. ChatGPT assigns tasks; you execute them and report back.

### Workflow

1. **Start session** — connect to Chrome via CDP:
   ```
   session_start(
       cdp_endpoint="http://localhost:9222",  # Chrome's CDP port
       target_url="https://chatgpt.com/c/<conversation-id>"  # Optional: URL to connect to
   )
   ```

2. **Receive task** — poll ChatGPT for instructions:
   ```
   poll_response(session_id, timeout=0)
   ```

3. **Execute task** — do the work in this codebase.

4. **Report back** — send a detailed summary (what you did, how you did it):
   ```
   send_message(session_id, message="...")
   ```

5. **Poll again** to keep the conversation alive and receive the next task:
   ```
   poll_response(session_id, timeout=0)
   ```

### Rules

- Always poll after sending to keep the conversation from breaking mid-flow.
- Reports to ChatGPT should be detailed: state what changed, what files were touched, and how the task was completed.
- Wait for ChatGPT's instruction before starting work. Don't freelance.