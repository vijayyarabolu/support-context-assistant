"""
Structured Logging to AWS CloudWatch for Agent Observability.

Ships agent traces, tool invocations, and end-to-end timing data
to CloudWatch Log Groups for debugging, analytics, and compliance.

Enables end-to-end tracing by correlating:
- Session ID (conversation identifier)
- Request timestamp
- Agent reasoning traces
- Tool call inputs/outputs
- Response latency
"""

import json
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

import boto3
from botocore.exceptions import ClientError

import config


class AgentTraceLogger:
    """
    Ships structured JSON logs of agent interactions to AWS CloudWatch.

    Each log entry contains the full agent reasoning trace including
    which tools were invoked, what inputs were provided, and the
    resulting outputs. This enables:
    - Performance monitoring (latency per tool call)
    - Debugging (trace why the agent chose specific tools)
    - Compliance auditing (full record of agent decisions)
    """

    def __init__(self):
        """
        Initialize the CloudWatch Logs client and create a log stream.

        Each instance creates a new log stream named with the current
        timestamp to avoid collisions when running multiple sessions.
        """
        aws_config = config.get_aws_session_config()

        self.log_group = config.CLOUDWATCH_LOG_GROUP
        self.logs_client = None
        self.log_stream_name = f"agent-trace-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

        if config.DEMO_MODE:
            return

        # Create CloudWatch Logs client with configured credentials
        self.logs_client = boto3.client(
            "logs",
            **aws_config,
        )

        # Initialize the log stream
        self._create_log_stream()

    def _create_log_stream(self):
        """
        Create a new log stream in the configured log group.

        If the log group doesn't exist, attempts to create it first.
        This ensures the logger works even in freshly provisioned environments.
        """
        try:
            # First, ensure the log group exists
            self.logs_client.create_log_group(logGroupName=self.log_group)
            print(f"[CloudWatch] Created log group: {self.log_group}")
        except ClientError as e:
            # ResourceAlreadyExistsException is expected if group exists
            if e.response["Error"]["Code"] != "ResourceAlreadyExistsException":
                print(f"[CloudWatch] Log group creation error: {e}")

        try:
            # Create the log stream within the group
            self.logs_client.create_log_stream(
                logGroupName=self.log_group,
                logStreamName=self.log_stream_name,
            )
            print(f"[CloudWatch] Created log stream: {self.log_stream_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceAlreadyExistsException":
                print(f"[CloudWatch] Log stream creation error: {e}")

    def log_trace(
        self,
        session_id: str,
        trace_data: List[Dict],
        metadata: Optional[Dict] = None,
    ):
        """
        Ship a structured agent trace to CloudWatch.

        Args:
            session_id: Conversation session identifier
            trace_data: List of trace events from the agent response
            metadata: Optional additional context (tenant_id, user, etc.)
        """
        log_event = {
            "event_type": "agent_trace",
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "traces": trace_data,
            "trace_count": len(trace_data),
        }

        if metadata:
            log_event["metadata"] = metadata

        self._put_log_event(log_event)

    def log_tool_call(
        self,
        session_id: str,
        tool_name: str,
        tool_input: Dict,
        tool_output: Dict,
        latency_ms: float,
    ):
        """
        Log a specific tool invocation with timing data.

        Args:
            session_id: Conversation session identifier
            tool_name: Name of the MCP tool that was invoked
            tool_input: Input parameters passed to the tool
            tool_output: Output returned by the tool
            latency_ms: Time taken for the tool call in milliseconds
        """
        log_event = {
            "event_type": "tool_call",
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_output": tool_output,
            "latency_ms": latency_ms,
        }

        self._put_log_event(log_event)

    def log_request(
        self,
        session_id: str,
        user_query: str,
        agent_response: str,
        total_latency_ms: float,
        tenant_id: str = "",
    ):
        """
        Log a complete request-response cycle for end-to-end tracing.

        Args:
            session_id: Conversation session identifier
            user_query: The user's input query
            agent_response: The agent's final response
            total_latency_ms: Total time from query to response
            tenant_id: Authenticated tenant identifier
        """
        log_event = {
            "event_type": "request_response",
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tenant_id": tenant_id,
            "user_query": user_query,
            "response_length": len(agent_response),
            "total_latency_ms": total_latency_ms,
        }

        self._put_log_event(log_event)

    def _put_log_event(self, log_event: Dict[str, Any]):
        """
        Write a single log event to CloudWatch Logs.

        CloudWatch Logs no longer requires sequence tokens for put_log_events.

        Args:
            log_event: Structured log event dictionary
        """
        if not self.logs_client:
            return

        timestamp_ms = int(time.time() * 1000)

        put_kwargs = {
            "logGroupName": self.log_group,
            "logStreamName": self.log_stream_name,
            "logEvents": [
                {
                    "timestamp": timestamp_ms,
                    "message": json.dumps(log_event, default=str),
                }
            ],
        }

        try:
            self.logs_client.put_log_events(**put_kwargs)

            print(f"[CloudWatch] Logged {log_event.get('event_type', 'event')} "
                  f"for session {log_event.get('session_id', 'unknown')}")

        except ClientError as e:
            error_code = e.response["Error"]["Code"]

            print(f"[CloudWatch] Failed to log event ({error_code}): {e}")
