# Skill: weekly_digest
**When to use:** the user wants to catch up on what trusted creators published recently.

**Steps**
1. `GET /digest`: select chunks ingested in the last 7 days.
2. LLM groups them by topic.
3. Return topics with title, creator, quote and timestamp link.

**Inputs:** none. **Outputs:** `{topics: [{topic, items[]}]}`.
