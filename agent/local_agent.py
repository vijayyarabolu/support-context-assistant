"""
Offline support agent used in demo mode.

The AWS version hands the plan to Bedrock AgentCore, which decides which tools
to call. With AWS off, this module runs the same investigation steps itself so
the flow can be tried, tested and evaluated on a laptop:

    1. Refuse requests that name a different tenant than the signed-in one.
    2. Pull the tenant's recent error logs (always filtered to that tenant).
    3. Retrieve the most relevant runbooks.
    4. Look up the tenant's plan and support tier for escalation rules.
    5. Write a cited answer.

Step 5 is a fixed template by default, which keeps tests and evals
deterministic. Set SUPPORT_LLM=anthropic and ANTHROPIC_API_KEY to have Claude
write the final answer from the same gathered context instead.
"""

import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional

from offline.store import LocalStore
from retrieval.local_retriever import LocalRunbookRetriever, extract_error_codes

TENANT_ID_RE = re.compile(r"\bT-\d{3}\b", re.IGNORECASE)

# Below this BM25 score the question is too vague to pick a runbook from words
# alone ("what's wrong with our account?"), so the tenant's most frequent recent
# error code is added to the search.
VAGUE_QUERY_SCORE = 4.0

# A vague question only falls back to the tenant's logs when it is clearly a
# request to investigate. Anything else with a weak match gets "no match"
# instead of a confident answer about an unrelated runbook.
INVESTIGATE_RE = re.compile(
    r"\b(investigate|investigation|error|errors|wrong|problem|problems|issue|issues|broken|"
    r"failing|failure|failures|look into|happening|outage|down)\b", re.IGNORECASE)


class LocalSupportAgent:
    def __init__(self, store: Optional[LocalStore] = None, retriever: Optional[LocalRunbookRetriever] = None):
        self.store = store or LocalStore()
        self.retriever = retriever or LocalRunbookRetriever()

    def invoke(self, prompt: str, tenant_id: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        started = time.perf_counter()
        session_id = session_id or str(uuid.uuid4())
        tool_calls: List[Dict[str, Any]] = []

        other_tenants = {t.upper() for t in TENANT_ID_RE.findall(prompt)} - {tenant_id}
        if other_tenants:
            return self._result(
                text=(f"I can only investigate your own workspace ({tenant_id}). "
                      "Requests about other tenants are blocked."),
                status="refused", session_id=session_id, tool_calls=tool_calls,
                sources=[], started=started,
            )

        explicit_codes = extract_error_codes(prompt)

        logs = self.store.query_logs(tenant_id, error_codes=explicit_codes or None, hours_back=24)
        tool_calls.append({"tool": "query_logs", "function": "query_logs",
                           "parameters": {"error_codes": explicit_codes, "hours_back": 24},
                           "result_count": logs.get("total_errors", 0)})

        hits = self.retriever.search(prompt, top_k=3, error_codes=explicit_codes)
        search_codes = list(explicit_codes)
        weak_match = not hits or hits[0]["score"] < VAGUE_QUERY_SCORE
        if not explicit_codes and weak_match:
            if INVESTIGATE_RE.search(prompt) and logs.get("summary"):
                search_codes = [logs["summary"][0]["error_code"]]
                hits = self.retriever.search(prompt + " " + search_codes[0], top_k=3, error_codes=search_codes)
            else:
                hits = []
        tool_calls.append({"tool": "search_knowledge_base", "function": "search_runbooks",
                           "parameters": {"query": prompt, "error_codes": search_codes},
                           "result_count": len(hits)})

        crm = self.store.lookup_crm(tenant_id)
        tool_calls.append({"tool": "lookup_crm", "function": "lookup_crm",
                           "parameters": {"tenant_id": tenant_id}, "result_count": 1 if crm.get("status") == "found" else 0})

        if not hits:
            text = ("I couldn't find a runbook that matches this. Can you share the error code "
                    "or the exact message users see?")
            return self._result(text=text, status="no_match", session_id=session_id,
                                tool_calls=tool_calls, sources=[], started=started, logs=logs)

        text = self._write_answer(prompt, tenant_id, logs, hits, crm)
        return self._result(text=text, status="success", session_id=session_id, tool_calls=tool_calls,
                            sources=[h["runbook_id"] for h in hits], started=started, logs=logs)

    # ------------------------------------------------------------------ answer

    def _write_answer(self, prompt, tenant_id, logs, hits, crm) -> str:
        if os.getenv("SUPPORT_LLM", "").lower() == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
            try:
                return self._llm_answer(prompt, tenant_id, logs, hits, crm)
            except Exception as exc:  # fall back to the template, never fail the request
                print(f"[local_agent] LLM answer failed, using template: {exc}")
        return self._template_answer(tenant_id, logs, hits, crm)

    def _template_answer(self, tenant_id, logs, hits, crm) -> str:
        top = self.retriever.runbook(hits[0]["runbook_id"])
        lines = [f"**Tenant:** {crm.get('name', tenant_id)} ({tenant_id}), "
                 f"{crm.get('plan', 'Unknown')} plan, {crm.get('support_tier', 'Standard')} support"]

        summary = logs.get("summary", [])
        if summary:
            seen = ", ".join(f"{s['count']}x {s['error_code']} from {s['top_source']}" for s in summary[:3])
            lines.append(f"**Last 24 hours:** {seen}")
        else:
            lines.append("**Last 24 hours:** no matching errors in this tenant's logs")

        lines.append(f"**Best match:** {top.title} [{top.id}]")
        steps = re.search(r"## Resolution steps\n(.*?)(?:\n## |\Z)", top.body, re.S)
        if steps:
            lines.append("**Steps:**\n" + steps.group(1).strip())
        escalation = re.search(r"## Escalation\n(.*?)(?:\n## |\Z)", top.body, re.S)
        if escalation:
            lines.append(f"**Escalation:** {escalation.group(1).strip()}")

        lines.append("**Sources:** " + ", ".join(f"[{h['runbook_id']}] {h['title']}" for h in hits))
        return "\n\n".join(lines)

    def _llm_answer(self, prompt, tenant_id, logs, hits, crm) -> str:
        import anthropic

        context = "\n\n".join(
            f"[{h['runbook_id']}] {self.retriever.runbook(h['runbook_id']).title}\n"
            f"{self.retriever.runbook(h['runbook_id']).body}" for h in hits
        )
        log_lines = "\n".join(f"- {s['count']}x {s['error_code']} from {s['top_source']}: {s['sample_message']}"
                              for s in logs.get("summary", [])) or "- none"
        system = ("You are a support engineer. Answer only from the runbooks and logs provided. "
                  "Cite runbooks as [rb-xxx]. If the context does not answer the question, say so.")
        user = (f"Tenant {tenant_id}: {crm.get('name')}, {crm.get('plan')} plan, "
                f"{crm.get('support_tier')} support.\n\nErrors in the last 24 hours:\n{log_lines}\n\n"
                f"Runbooks:\n{context}\n\nQuestion: {prompt}")
        client = anthropic.Anthropic()
        message = client.messages.create(
            model=os.getenv("SUPPORT_LLM_MODEL", "claude-sonnet-5-5"),
            max_tokens=700,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in message.content if getattr(block, "type", "") == "text")

    # ------------------------------------------------------------------ result

    @staticmethod
    def _result(text, status, session_id, tool_calls, sources, started, logs=None) -> Dict[str, Any]:
        return {
            "text": text,
            "status": status,
            "session_id": session_id,
            "tool_calls": tool_calls,
            "sources": sources,
            "traces": [],
            "log_rows": (logs or {}).get("results", []),
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
