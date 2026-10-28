# Skill: verify_claim
**When to use:** automatically on each top-5 result, or when the user asks to check one chunk.

**Steps**
1. `extract_claim` -> claim + type. Non-fact: verdict "Opinion/Unverifiable", skip search.
2. `gather_evidence`: top-5 chunks from other trusted creators + 3 web queries (top 5 results).
3. `judge` -> verdict (supported/mixed/contradicted/insufficient), confidence, reason, evidence. Only retrieved sources may be cited.
4. `red_flags`: sponsorship language, no cited source, absolute claims. Cache by claim hash.

**Inputs:** `chunk_id`. **Outputs:** Legitimacy Report. Wording: "cross-checked", never "guaranteed true".
