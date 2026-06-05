# MOVE 4 — Intent Router for the Main Input (committed design)

> Goal (Joel): the main input box should DETECT intent — if the user wants a report, run the full
> pipeline; if they want a quick answer, give the fast chat. EITHER way, always offer "Generate Report".
> Backend owns ALL intent/AI logic (frontend stays dumb — calls one endpoint, branches on `mode`).
> Scope: MAIN INPUT QUERY only. NOT touching add-KPI / add-chart (modify) paths.

---

## Current system (verified from code)
- Main input → ALWAYS `/chat/stream` (fast 2-agent ~30s) → shows "Generate Report" offer IF backend
  flags `report_eligible`. Full `/report` (6-agent ~140s) only fires when user clicks / says "yes".
- NO intent detection up front. A clear "create a full dashboard" still gets a chat answer first.
- `classify_intent()` (report_generator.py:125) exists but is KEYWORD-based (crude) and only used to set
  the report_eligible hint — NOT to route.

## Target (Joel's goal)
```
Main input → POST /ask {question}
                  │  backend: classify intent (cheap LLM classifier)
        ┌─────────┴──────────┐
   intent=report        intent=chat
        │                    │
   full /report path    fast chat path
   (6-agent dashboard)   (2-agent answer)
        │                    │
   mode:"report"        mode:"chat" + ALWAYS include report-offer hint
```
Frontend calls ONE endpoint, reads `mode`, renders report or chat. All AI logic stays backend.

## Design decisions (committed)
1. **Intent detection = cheap LLM classifier** (Haiku, ~1-2s, tiny cost) — not keywords. "Understand intent"
   needs understanding. Returns {mode: "report"|"chat", confidence, reason}.
   - Reuse: on the report path, the Context agent ALSO classifies — but the router runs FIRST and decides
     which pipeline to enter, so the router classifier is a separate tiny call (cheap, worth it for routing).
2. **Backend owns it** — new function `classify_query_intent(question)` in a backend module (NOT frontend JS).
3. **One new endpoint `/ask`** that classifies then dispatches:
   - report → run the existing EnhancedReportPipeline (same as /report today), return JSON with mode:"report".
   - chat → run the existing answer_chat_question stream, mode:"chat", ALWAYS set report_eligible=true so the
     offer always shows (Joel: always give a report option even on fast chat).
4. **Streaming:** /ask streams (SSE) in BOTH cases so the UI shows progress. Chat streams its stages; report
   streams a "generating report…" heartbeat then the final payload. (Keeps one response contract.)
5. **Keep /report and /chat/stream** as-is — the explicit "Generate Report" button still calls /report directly;
   /ask is the new smart entry for the main box.
6. **Safety net:** misrouted "should've been a report" → user still gets fast answer + the always-present
   "Generate Report" button → one click escalates. Misrouting is low-stakes by design.

## Honest risks
- Intent misclassification (mitigated by always-on report offer).
- +1-2s on every query for the classifier call (acceptable; it gates a 30s-vs-140s decision).
- Streaming-both-modes plumbing is the fiddly part; the report path "stream" is really just progress + final.
- Frontend change: the main box must call /ask instead of /chat/stream, and branch on `mode`. Small, contained.

## Build order
1. Backend: `classify_query_intent(question)` — cheap Haiku classifier (report|chat + reason).
2. Backend: `/ask` endpoint — classify → dispatch to report or chat → unified streamed response w/ `mode`.
3. Frontend: main input calls `/ask`; on `mode:"report"` render report (open report view), on `mode:"chat"`
   render chat answer + always show "Generate Report" offer.
4. Test: run report-intent phrasings ("create a dashboard of X") → should auto-run full report; chat-intent
   ("what is total revenue") → fast answer + offer. Verify classifier accuracy on ~8-10 phrasings.

## What stays out of scope (Joel said)
- add-KPI / add-chart (modify) flow — untouched.
