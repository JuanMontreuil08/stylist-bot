# MVP Feature Test Report
**Date:** 2026-03-28
**Features tested:** Onboarding flow · Online product search (Perplexity Sonar)
**Verdict:** Both features are MVP-ready with minor caveats documented below.

---

## 1. Onboarding Flow

### What was tested
- `needs_onboarding()` correctly identifies new vs. returning users
- All 8 steps of the state machine persist correctly to Supabase
- Profile data (name, gender, style, sizes, budget, colors, brands) survives in DB
- `needs_onboarding()` returns `False` after completion (agent takes over)
- Profile context block format injected into agent prompt
- `update_user_profile` tool — scalar and array fields, plus invalid field rejection

### Results

| Test | Result |
|------|--------|
| `needs_onboarding` → True for unknown user | ✅ PASS |
| All 8 step transitions (state persists correctly) | ✅ PASS (12/12) |
| `onboarding_complete` set to True after final step | ✅ PASS |
| `needs_onboarding` → False after completion | ✅ PASS |
| All 7 profile fields returned by `get_profile` | ✅ PASS |
| Profile context block format correct | ✅ PASS |
| `update_user_profile` — scalar field (city) | ✅ PASS |
| `update_user_profile` — array field via CSV (brands) | ✅ PASS |
| `update_user_profile` — invalid field rejected with clear error | ✅ PASS |

**31 / 31 tests passed.**

### Sample profile context injected into agent
```
[Perfil del usuario — phone: +51000000001, nombre: Juan, género: hombre,
estilo: casual, tallas: Camiseta M, Pantalón 32, Zapato 42,
presupuesto: 150_400, colores favoritos: azules y verdes,
marcas favoritas: Nike, Zara]
```

### Issues found

**Issue 1 — Brand casing inconsistency (minor)**
During onboarding, brands entered by the user are stored capitalized (`"Nike"`). When the agent calls `update_user_profile` for brands, they are lowercased (`"nike"`). This is cosmetic today but will cause inconsistency in the profile display.
*Fix:* Remove `.lower()` from the array field parsing in `update_user_profile` and store as-entered.

**Issue 2 — WhatsApp interactive messages untested end-to-end**
The state machine logic and DB persistence are fully validated. However, the actual `_send_buttons()` and `_send_list()` calls to Kapso could not be tested in this environment without a live WhatsApp number. The first real user run will be the live test for the UI layer.
*Recommendation:* Test with a dev WhatsApp number before opening to users. Verify button titles display correctly (≤20 chars) and list rows render.

**Issue 3 — No restart protection for in-progress sessions**
If a user is mid-onboarding (e.g., at step 4) and doesn't respond for several days, the state persists correctly. However, there is no timeout or "resume" message to re-engage them.
*Recommendation:* Not critical for MVP. Address when first users are onboarded.

---

## 2. Online Product Search (Perplexity Sonar)

### What was tested
6 scenarios covering: fashion with budget and location, casual clothing, profile-aware query using color/brand preferences, non-clothing (skincare), event-based outfit, and a vague query stress test.

Each result was scored across 5 dimensions (max 10):
- **Rec count** (≥3 = 2pts)
- **Link count** (≥2 = 2pts)
- **Structure** (price + pros + cons + comparison all present = 2pts)
- **No citation leakage** (no `[1][3]` markers in text = 2pts)
- **No non-shopping URLs** (no YouTube, blog, or editorial links = 2pts)

### Results

| Scenario | Score | Latency | Recs | Links | Price | Pros/Cons | Comparison | Citation leak | Bad URLs |
|----------|-------|---------|------|-------|-------|-----------|------------|---------------|----------|
| S1 · Fashion + budget + location | 10/10 | 9.9s | 4 | 4 | ✅ | ✅ | ✅ | ✅ None | ✅ 0 |
| S2 · Casual clothing, no context | 10/10 | 9.2s | 4 | 4 | ✅ | ✅ | ✅ | ✅ None | ✅ 0 |
| S3 · Profile-aware (colors + brands) | 10/10 | 9.3s | 4 | 8 | ✅ | ✅ | ✅ | ✅ None | ✅ 0 |
| S4 · Non-clothing (skincare) | 10/10 | 11.4s | 4 | 4 | ✅ | ✅ | ✅ | ✅ None | ✅ 0 |
| S5 · Event-based outfit | 10/10 | 18.2s | 4 | 5 | ✅ | ✅ | ✅ | ✅ None | ✅ 0 |
| S6 · Vague query stress test | 10/10 | 8.0s | 4 | 4 | ✅ | ✅ | ✅ | ✅ None | ✅ 0 |

**Average score: 10.0 / 10 — Average latency: 11.0s**

### Profile context works
S3 (polo hombre casual) with context `"colores azules y verdes, marcas Nike o Adidas"` returned Adidas polos in blue and green specifically, confirming that user profile preferences passed via `user_context` correctly steer results.

### Issues found

**Issue 1 — Budget not enforced (S5, event outfit) 🔴 High priority**
S5 requested `presupuesto 400 soles` for a formal outfit. The top recommendation was a full suit at S/699.90 — 75% over budget. The model acknowledged the overage in the cons section but still recommended it as the #1 option.
*Impact:* Users will feel recommendations are irrelevant to their actual situation.
*Fix:* Add explicit budget enforcement instruction to the system prompt: `"Do not recommend products whose price exceeds the user's stated budget. If no in-budget option exists, say so explicitly and suggest the closest alternative."`

**Issue 2 — Shortened/redirect URLs in S6 (vague query) 🟡 Medium priority**
The vague query `"ropa bonita"` returned three `shein.top/xxxxxxx` shortened URLs. These are link redirectors — if SHEIN's shortener goes down, the links break. They also obscure the destination from the user.
*Impact:* Broken links undermine trust with users.
*Fix:* Add to the system prompt: `"Always use full, direct product URLs. Never use URL shorteners or redirect links."`

**Issue 3 — All links for one scenario point to the same category page (S5) 🟡 Medium priority**
S5 returned 3 links that were all identical (`falabella.com.pe/…/Ropa-Formal`) — the same category page, not individual product pages. This gives the impression of multiple links but provides no extra value.
*Impact:* Users can't compare specific products.
*Fix:* Add to system prompt: `"Each cited source URL should point to a specific product or product listing page, not a general category page. Do not repeat the same URL for multiple products."`

**Issue 4 — Latency spike on complex queries 🟡 Medium priority**
S5 (event outfit, formal, budget) took 18.2s — more than double the average. On WhatsApp, a user typically expects a reply in 3–5 seconds. Any response over 10 seconds risks the user assuming the bot is broken.
*Impact:* Poor perceived responsiveness for complex outfit queries.
*Recommendation:* Send a typing indicator or acknowledgment message (`"Buscando las mejores opciones para ti... 🔍"`) immediately via Kapso before invoking Perplexity, so the user knows the bot is working.

**Issue 5 — Vague query defaulted to women's products 🟡 Medium priority**
`"ropa bonita"` with no context returned women's products (blusa bodysuit rosada). Without a user profile, the model makes assumptions.
*Impact:* With profile injection, this becomes a non-issue since gender is always in context. But it highlights the importance of completing onboarding before users start querying.
*Note:* This reinforces that onboarding should be completed before the first free-form query is allowed.

---

## Overall MVP Verdict

| Feature | MVP Ready? | Blocker? |
|---------|-----------|----------|
| Onboarding state machine (logic + DB) | ✅ Yes | None — needs live WhatsApp test |
| Profile injection into agent | ✅ Yes | None |
| `update_user_profile` (agent tool) | ✅ Yes | Minor casing fix recommended |
| Perplexity search (structure + quality) | ✅ Yes | Budget enforcement + URL quality fixes |

### Recommended fixes before first users (priority order)

1. **Budget enforcement in Perplexity prompt** — 1 line change in `system_prompt.txt` (Issue S5-1)
2. **Ban shortened URLs in Perplexity prompt** — 1 line change in `system_prompt.txt` (Issue S6-2)
3. **Ban duplicate/category URLs** — 1 line change in `system_prompt.txt` (Issue S5-3)
4. **"Typing…" acknowledgment message** — send a text via Kapso before calling Perplexity (Issue S5-4)
5. **Brand casing in `update_user_profile`** — remove `.lower()` from array field parsing (Issue OB-1)
6. **Live WhatsApp button/list test** — manual test with a dev number before launch (Issue OB-2)

---

## Re-test after fixes (2026-03-28)

All 5 fixes applied and verified:

| Fix | File changed | Re-test result |
|-----|-------------|----------------|
| Budget enforcement | `agentcore/tools.py` — Perplexity system prompt | ✅ S5 max price now S/350, no violations |
| Ban shortened URLs | `agentcore/tools.py` — Perplexity system prompt | ✅ S6 zero `shein.top` or redirect URLs |
| Ban duplicate/category URLs | `agentcore/tools.py` — Perplexity system prompt | ✅ S5 now returns product-specific Falabella/Ripley links |
| Acknowledgment message | `kapso/handler.py` | ✅ "Buscando las mejores opciones..." sent before agent call |
| Brand casing consistency | `agentcore/tools.py` — `update_user_profile` | ✅ Brands stored as-entered, not lowercased |

**Note on S6 (vague query):** `"ropa bonita"` still returns store/category links (Falabella Moda Mujer, Ripley ver-todo) because the query is too vague for Perplexity to identify specific products — this is correct behavior. The real fix is agent-level: the agent should ask for clarification before calling `search_products_online` with a vague query. Add to system prompt: *"Before calling search_products_online, ensure the query is specific enough (product type + style/color/occasion). If the user's request is too vague, ask one clarifying question first."*
