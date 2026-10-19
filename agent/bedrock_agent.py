"""
AWS Bedrock AgentCore Orchestration for Support Context Assistant.

Invokes the Bedrock Agent to orchestrate the support context workflow.
The agent acts as the primary router, deciding whether to:
- Search OpenSearch RAG knowledge base for runbooks
- Query CloudWatch logs for recent errors
- Look up CRM data for tenant context
- Fetch DynamoDB records for tenant state

Uses the MCP (Model Context Protocol) gateway for tool routing with
built-in guardrails for response safety and consistency.
"""

import json
import uuid
from typing import Dict, Any, Optional, Generator

import boto3
from botocore.config import Config

import config


class SupportAgent:
    """
    Manages interactions with the AWS Bedrock AgentCore Support Agent.

    AgentCore was chosen over the initial LangGraph prototype because:
    1. Native execution isolation per session (no state corruption)
    2. MCP gateway handles tool routing without custom orchestration
    3. Built-in guardrails for response safety in customer-facing scenarios
    4. Managed infrastructure reduces operational overhead

    See prototype/langgraph_agent.py for the original implementation
    and documentation of the state errors that prompted this migration.
    """

    def __init__(self):
        """
        Initialize the Bedrock AgentCore runtime client.

        Uses bedrock-agent-runtime for invoking the agent at runtime.
        Configured with adaptive retry strategy and extended read timeout
        to accommodate multi-tool agent executions.
        """
        aws_config = config.get_aws_session_config()

        # Configure retry strategy for production reliability
        retry_config = Config(
            retries={"max_attempts": 3, "mode": "adaptive"},
            read_timeout=120,  # Agent responses can take time with tool calls
        )

        self.agent_id = config.BEDROCK_AGENT_ID
        self.agent_alias_id = config.BEDROCK_AGENT_ALIAS_ID
        self.client = None

        if config.DEMO_MODE:
            return

        # bedrock-agent-runtime client for agent invocation
        self.client = boto3.client(
            "bedrock-agent-runtime",
            **aws_config,
            config=retry_config,
        )

    @staticmethod
    def _build_contextualized_prompt(prompt: str, tenant_id: Optional[str] = None) -> str:
        """
        Inject tenant context consistently for standard and streaming invocations.
        """
        if not tenant_id:
            return prompt

        return f"[Tenant Context: {tenant_id}] {prompt}"

    @staticmethod
    def _parse_agent_response(response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse AgentCore's event stream into text, traces, and tool calls.
        """
        output_text = ""
        traces = []
        tool_calls = []

        for event in response.get("completion", []):
            if "chunk" in event:
                chunk = event["chunk"]
                if "bytes" in chunk:
                    output_text += chunk["bytes"].decode("utf-8")

            if "trace" in event:
                trace = event["trace"]
                traces.append(trace)

                trace_detail = trace.get("trace", {})
                if "orchestrationTrace" in trace_detail:
                    orch = trace_detail["orchestrationTrace"]
                    if "invocationInput" in orch:
                        invocation = orch["invocationInput"]
                        if "actionGroupInvocationInput" in invocation:
                            action = invocation["actionGroupInvocationInput"]
                            tool_calls.append({
                                "tool": action.get("actionGroupName", "unknown"),
                                "function": action.get("function", "unknown"),
                                "parameters": action.get("parameters", []),
                            })

        return {
            "text": output_text,
            "traces": traces,
            "tool_calls": tool_calls,
        }

    @staticmethod
    def demo_response(prompt: str, session_id: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Local demo response that preserves tenant context without AWS calls.
        """
        tenant_label = tenant_id or "unknown tenant"
        return {
            "text": (
                f"Demo analysis for {tenant_label}: I would search tenant-scoped logs, "
                "retrieve matching runbooks, check CRM tier, and return a cited support plan."
            ),
            "session_id": session_id,
            "traces": [],
            "tool_calls": [
                {
                    "tool": "support_context",
                    "function": "demo_triage_plan",
                    "parameters": [{"name": "prompt", "value": prompt}],
                }
            ],
            "status": "demo",
        }

    def invoke(
        self,
        prompt: str,
        session_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Invoke the Bedrock AgentCore agent with a support query.

        The agent processes the query through these steps:
        1. Parses the user's support intent
        2. Determines which MCP tools to invoke
        3. Executes tool calls (OpenSearch, CloudWatch, DynamoDB)
        4. Synthesizes a resolution response with citations

        Args:
            prompt: Natural language support query
            session_id: Existing session ID for multi-turn conversations
            tenant_id: Authenticated tenant ID for context injection

        Returns:
            Dict with response text, traces, session_id, and tool calls
        """
        if not session_id:
            session_id = str(uuid.uuid4())

        contextualized_prompt = self._build_contextualized_prompt(prompt, tenant_id)

        if not self.client:
            if config.DEMO_MODE:
                return self.demo_response(prompt, session_id, tenant_id)
            raise RuntimeError("Bedrock AgentCore client unavailable and DEMO_MODE is disabled")

        # Invoke the agent via bedrock-agent-runtime API
        response = self.client.invoke_agent(
            agentId=self.agent_id,
            agentAliasId=self.agent_alias_id,
            sessionId=session_id,
            inputText=contextualized_prompt,
            enableTrace=True,
        )

        parsed_response = self._parse_agent_response(response)

        return {
            "text": parsed_response["text"],
            "session_id": session_id,
            "traces": parsed_response["traces"],
            "tool_calls": parsed_response["tool_calls"],
            "status": "success",
        }

    def invoke_streaming(
        self,
        prompt: str,
        session_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> Generator[str, None, None]:
        """
        Stream the agent's response token by token.

        Used by the Streamlit UI for real-time response display.

        Args:
            prompt: Support query
            session_id: Session ID for multi-turn conversations
            tenant_id: Authenticated tenant ID for context injection

        Yields:
            Text chunks as they arrive
        """
        if not session_id:
            session_id = str(uuid.uuid4())

        contextualized_prompt = self._build_contextualized_prompt(prompt, tenant_id)

        if not self.client:
            if config.DEMO_MODE:
                yield self.demo_response(prompt, session_id, tenant_id)["text"]
                return
            raise RuntimeError("Bedrock AgentCore client unavailable and DEMO_MODE is disabled")

        response = self.client.invoke_agent(
            agentId=self.agent_id,
            agentAliasId=self.agent_alias_id,
            sessionId=session_id,
            inputText=contextualized_prompt,
            enableTrace=False,
        )

        for event in response.get("completion", []):
            if "chunk" in event:
                chunk = event["chunk"]
                if "bytes" in chunk:
                    yield chunk["bytes"].decode("utf-8")
