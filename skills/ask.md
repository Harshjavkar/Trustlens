# Skill: ask
**When to use:** the user asks what trusted creators said about a topic.

**Steps**
1. Embed the question; take the top 20 chunks from trusted creators by cosine similarity.
2. LLM-rerank to the top 5.
3. Run the Legitimacy Report on each in parallel (see `verify_claim`).
4. Score, sort (contradicted last), save the question to memory.

**Inputs:** `q`. **Outputs:** ranked results: title, creator, timestamp link, quote (<=15 words), score breakdown, report.
