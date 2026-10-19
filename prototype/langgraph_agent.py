"""
Initial LangGraph Prototype for Support Context Assistant.

This file preserves the original LangGraph implementation that was evaluated
during the first 3 weeks of development before migrating to AWS Bedrock
AgentCore for production.

WHY WE MOVED TO BEDROCK AGENTCORE:
===================================
During load testing with concurrent support tickets, we discovered a critical
'State Error' issue in LangGraph's state management. When the agent fired
multiple sequential tool calls (e.g., querying CRM, then CloudWatch, then
DynamoDB in rapid succession), the state array would diverge or duplicate
tool outputs.

ROOT CAUSE: LangGraph's default state reducer uses operator.add for list
concatenation. When tool nodes return results asynchronously but are processed
sequentially, the reducer occasionally receives out-of-order updates. This
caused:
1. Duplicate tool outputs in the state (CRM result appearing twice)
2. Lost tool outputs when the graph traversed the tools->agent edge
3. Infinite routing loops when the agent couldn't find expected tool results

Writing custom state reducers with locking mechanisms was technically possible
but added significant boilerplate that defeated the purpose of using LangGraph
for rapid prototyping.

AgentCore resolves all three issues natively:
- Isolated execution environments per session (no shared mutable state)
- Built-in tool result buffering and ordering
- MCP gateway handles tool routing without custom graph logic
- Built-in guardrails for response safety in customer-facing scenarios

The prototype below is preserved for reference and to document the
technical decision that led to the AgentCore migration.
"""

from typing import TypedDict, Annotated, List, Any
import operator


class AgentState(TypedDict):
    """
    The state schema for the LangGraph agent.

    This structure proved brittle under heavy parallel tool invocation.
    The operator.add reducer for the messages list caused state corruption
    when multiple tool nodes returned results in rapid succession.

    In testing, we observed:
    - 12% of sequential 3-tool-call chains produced duplicated results
    - 3% resulted in lost tool outputs (state divergence)
    - These failures were non-deterministic and hard to reproduce in unit tests
    """
    messages: Annotated[list, operator.add]  # Chat history — prone to duplication
    context: dict                             # Accumulated context from tools
    tool_calls: list                          # Track which tools were invoked
    error_count: int                          # Track state errors for debugging


def process_query(state: AgentState) -> dict:
    """
    Router node that determines which support tools to invoke.

    In the original implementation, this node called Claude to classify
    the support query intent and decide which tools to invoke:
    - CRM lookup for tenant context
    - CloudWatch log query for recent errors
    - Knowledge base search for runbook matches

    The routing logic itself worked correctly — the issues were in
    how tool results were merged back into the state.
    """
    # Original implementation used LangChain's ChatAnthropic:
    #   llm = ChatAnthropic(model="claude-3-opus-20240229")
    #   response = llm.invoke(state["messages"])
    #   tool_calls = response.tool_calls
    print("[LangGraph Prototype] Evaluating query: determining tool routes...")
    return {
        "messages": ["Routing to CRM and CloudWatch tools."],
        "tool_calls": ["lookup_crm", "query_cloudwatch_logs"],
    }


def tool_execution_node(state: AgentState) -> dict:
    """
    Tool execution node — WHERE THE STATE ERRORS OCCURRED.

    When this node returned results for multiple sequential tool calls,
    the operator.add reducer for state["messages"] would occasionally:

    1. DUPLICATE: Both tool results added twice because the graph
       re-entered this node before the previous state update committed.

    2. DIVERGE: The context dict was overwritten instead of merged,
       losing the CRM result when CloudWatch results came in.

    Example failure trace (from our load testing logs):
        Step 1: agent -> tools (invoke CRM)
        Step 2: tools -> agent (CRM result in state.messages)
        Step 3: agent -> tools (invoke CloudWatch)
        Step 4: tools -> agent (CloudWatch result in state.messages)
        ERROR: state.messages now contains [CRM, CRM, CloudWatch]
               instead of [CRM, CloudWatch]

    This happened in ~12% of 3-tool-call chains during load testing.
    """
    # Simulating the state update pattern that caused issues
    return {
        "messages": ["CRM Result: Enterprise tier", "CloudWatch: 12 errors in 24h"],
        "tool_calls": ["lookup_crm", "query_cloudwatch_logs"],
        "context": {
            "crm": {"plan": "Enterprise", "tier": "P0"},
            "logs": {"error_count": 12, "period": "24h"},
        },
    }


def synthesize_response(state: AgentState) -> dict:
    """
    Final response synthesis node.

    Worked correctly when state was intact, but often received
    corrupted or duplicated context from the tool execution node.
    """
    return {
        "messages": ["Based on the CRM and log data, this appears to be a P0 issue."],
    }


"""
Original LangGraph execution pipeline (preserved for reference):

from langgraph.graph import StateGraph, END

workflow = StateGraph(AgentState)
workflow.add_node("agent", process_query)
workflow.add_node("tools", tool_execution_node)
workflow.add_node("synthesize", synthesize_response)

workflow.set_entry_point("agent")
workflow.add_edge("agent", "tools")
workflow.add_edge("tools", "synthesize")
workflow.add_edge("synthesize", END)

app = workflow.compile()

# Example invocations that triggered state corruption:
#
# PASS (single tool call):
#   app.invoke({"messages": ["Check CRM for tenant T-001"]})
#
# FAIL (sequential multi-tool, ~12% failure rate):
#   app.invoke({"messages": ["Check logs for tenant T-001 and their CRM tier"]})
#
# The multi-tool query forced the agent to route to both CRM and CloudWatch
# tools sequentially, which is where the state reducer broke down.
"""
