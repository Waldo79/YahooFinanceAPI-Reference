# Parser Design

Yahoo Finance responses are dynamic.

## Required Parser Behaviors

- Parse fields by key name.
- Ignore field order.
- Treat missing fields as normal unless explicitly required.
- Log unknown fields.
- Store raw JSON captures.
- Validate derived fields when possible.

## Suggested Flow

```text
Receive JSON
→ Identify endpoint
→ Identify root object
→ Identify symbol and quoteType
→ Identify marketState
→ Traverse objects by key
→ Normalize values
→ Write CSV
→ Validate output
→ Log unknown or missing fields
```
