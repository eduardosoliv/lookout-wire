---
title: Verify an external service with a real call before its facts go into a plan
date: 2026-10-16
category: workflow-issues
module: lookout_wire.adapters
problem_type: workflow_issue
component: development_workflow
severity: medium
applies_when:
  - Planning a capability or adapter that calls an external service the project has not called before
  - A plan or an agent states a vendor's free tier, quota, model id, timeout behavior, or response shape
  - Changing a vendor model id, SDK major version, account tier, or timeout default
symptoms:
  - Every Gemini request failed with HTTP 429 RESOURCE_EXHAUSTED on a tier the plan said was free
  - The default model id gemini-3.5-flash-lite was not offered on the owner's account
  - Search-grounded answers ended with HTTP 504 DEADLINE_EXCEEDED at the planned 20 s timeout
  - The first failure log said only ClientError
root_cause: missing_workflow_step
related_components:
  - observability
  - tooling
tags: [gemini, google-genai, search-grounding, live-check, external-service, vendor-quota, plan-assumptions]
---

# Verify an external service with a real call before its facts go into a plan

## Context

The watchlist plan (`docs/plans/2026-10-15-1635-feat-whatsapp-watchlist-alerts-plan.md`) based its
Gemini design on Google's documentation and its own defaults. Research subagents read the SDK docs
and model lists, and nobody called the API with the owner's key before the adapter was built
(session history). Three of those assumptions were wrong, and each showed up only when real scans
ran (session history):

- **Free tier.** The plan chose Gemini partly because "its free search quota covers the expected
  volume" (plan, Key Decisions), and the agent told the owner the default model was covered by the
  free tier, so the owner stayed on it (session history). The free tier gives zero quota for
  Google Search grounding: every request, from the first, returned
  `HTTP 429 RESOURCE_EXHAUSTED: You exceeded your current quota`. Switching to a model on the free
  list, `gemini-3.1-flash-lite`, still returned 429. That isolated grounding as the cause, because
  a wrong model name returns 404 and a 429 on the first request means zero quota, not a busy
  minute (session history). AI Studio's rate-limit page shows the model with free quota, which
  misleads. The fix was billing with a project spend cap.
- **Model id.** The default `gemini-3.5-flash-lite` was not offered on the owner's account. The
  default is now `gemini-3.8-flash` (`src/lookout_wire/core/settings.py:35`). Grounding is billed
  per search whichever model asks, so a Lite model saves almost nothing.
- **Timeout.** The plan's own choice of 20 s for the explanation was too short for some
  grounded answers, which ended with `HTTP 504 DEADLINE_EXCEEDED`. The adapter hands the timeout to
  the SDK (`src/lookout_wire/adapters/gemini.py:26-27`). google-genai 2.31.0 sends it to Google as
  an `X-Server-Timeout` header (`google/genai/_api_client.py`, lines 211-212, in the installed
  package) and also uses it for the httpx request (lines 1553 and 1606). In the observed failures
  Google's deadline fired first, so the error was a 504 response, not a client-side timeout. The
  default is now 30 s (`src/lookout_wire/core/settings.py:43`, PR #1). 30 s was the owner's choice;
  no live run after the change confirms it is enough (session history).

The link shape went the way all three should have. The plan's first version listed whether
grounding links would be redirect or publisher URLs as an open question for the first live call,
and accepted either answer. The call showed long Google redirect URLs
(`vertexaisearch.cloud.google.com/grounding-api-redirect/...`), and the owner chose source names
instead (`src/lookout_wire/adapters/gemini.py:64-80`). Because the unknown was marked, the answer
changed a message format, not a plan decision.

Diagnosis was slow because the first adapter logged only the exception class, so the 429 appeared
as `Gemini explanation failed: ClientError`. The first guesses were a bad model name, grounding
blocked on the free tier, or a bad key, and only a one-off call outside the agent showed the 429
(session history). The log now carries the model, the HTTP status, and Google's first sentence
(`src/lookout_wire/adapters/gemini.py:34-36` and `83-88`), which made the later 504 clear in one
step.

Alpaca and WhatsApp went differently. The plan's first unit, "Project scaffold, settings, and key
verification", required checking the owner's keys against the IEX snapshot and the market clock,
and a proactive WhatsApp send before the last unit. Those checks are now `just check-alpaca`,
`just whatsapp-id`, and `just whatsapp-send` (`justfile:51`, `justfile:55`, `justfile:59`;
`src/lookout_wire/checks.py:45`, `src/lookout_wire/checks.py:130`,
`src/lookout_wire/checks.py:147`). Gemini's only planned verification was a manual call after its
adapter existed (the plan's Gemini explainer unit). It still has no live check: only `just scan`
(`justfile:17`) and the full agent (`just run`) exercise it, and both send real WhatsApp alerts.

## Guidance

1. **Call the service before writing its facts into a plan.** Make one real request with the
   owner's key for every vendor fact the plan depends on: which tier or quota covers the feature,
   which model ids or endpoints the account can use, how long a typical answer takes and how a
   timeout fails, and what the response looks like. Record what came back in the plan and in the
   README's External services section. Mark a fact you cannot check yet as unverified or as an open
   question, as the plan did for the link shape and the README does for WhatsApp's 24-hour
   delivery.
2. **Say where a vendor fact came from.** The owner chose a tier based on the agent's reading of the
   pricing page (session history). Tell the owner which claims come from docs and which from a
   call.
3. **Give every adapter a live check in the first unit.** Add a function to
   `src/lookout_wire/checks.py` and a `just check-<service>` recipe that run the adapter's real code
   with the settings from `.env`, print OK or NOT OK with the vendor's reason, and send no WhatsApp
   message. `check_alpaca` is the shape to copy. A Gemini check costs one billed grounded search
   (about $14 per 1,000 searches, per the README), so its recipe comment should say so.
   `GeminiExplainer.explain` returns `None` on any failure by design
   (`src/lookout_wire/adapters/gemini.py:30-37`), so a Gemini check needs a path that surfaces the
   error, with `describe()` for the message, rather than reporting only that it got nothing.
4. **Log the vendor's status and message from the first version.** HTTP status, status word, and
   the first sentence of the message; never the key, headers, or URLs with query strings
   (AGENTS.md, Secrets). `describe()` in the Gemini adapter is the pattern.
5. **Read a failure by its status before guessing.** On the Gemini API, a 404 points to the model
   name, a 429 on the first request points to zero quota on the tier, and a 504
   `DEADLINE_EXCEEDED` points to the timeout the SDK forwarded to Google.

## Why This Matters

The explainer fails quietly on purpose: an alert goes out without its explanation rather than not
at all. That is right for the product, but it hides wrong vendor assumptions. Alerts kept arriving,
and only the log showed that every explanation was failing (session history). Each wrong
assumption cost a fix, a plan edit, and a README edit after the feature was built, and the owner
had picked the free tier on the agent's reading of the docs (session history). One real call during
planning would have caught the quota and the model id in minutes. The timeout problem appeared only
after a day of use, because grounded answers only sometimes take longer than 20 s. The timeout was
already a setting; its fix added `LOOKOUT_EXPLAIN_TIMEOUT_SECONDS` to `.env.example` and the README
so the owner can change it without code, and the descriptive log is what showed which limit was
hit.

## When to Apply

- Adding a capability that needs a service the project has not called yet, such as a news feed, a
  second LLM provider behind the `Explainer` port, or a broker.
- Changing a vendor model id, SDK major version, account tier, or timeout default.
- Reviewing a plan: any sentence that says what a vendor allows, costs, or returns needs a call
  behind it, or a mark that it is still open.

## Examples

The plan's sentence, written from the docs and never tested:

> Its search uses the Google index and its free search quota covers the expected volume.

The README's sentence, written after a real call:

> The free tier does not work, even though the model looks free. Every request fails with
> `HTTP 429 RESOURCE_EXHAUSTED: You exceeded your current quota` from the very first one, because
> the free tier gives zero quota for search grounding.

The failure log, before and after:

```python
# First version: the 429 showed up as "ClientError"
logger.warning("Gemini explanation failed: %s", type(error).__name__)

# Now (src/lookout_wire/adapters/gemini.py:34-36 and 83-88)
logger.warning("Gemini explanation failed with model %s: %s", self._model, describe(error))
```

A line the new log produced during a scan (session history):

```text
Gemini explanation failed with model gemini-3.8-flash: HTTP 504 DEADLINE_EXCEEDED: Deadline expired before operation could complete.
```

## Related

- README, "External services": the current Gemini, Alpaca, and WhatsApp quirks. The facts live
  there; this doc is about how to find them before building.
- `docs/plans/2026-10-15-1635-feat-whatsapp-watchlist-alerts-plan.md`: its timeout and Gemini
  design notes carry the corrected timeout and model. The Key Decisions entry for Gemini still
  says the free search quota covers the expected volume.
