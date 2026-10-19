"""
Streamlit UI for the Support Context Assistant.

Multi-tenant support dashboard powered by AWS Bedrock AgentCore.
Features:
- Mock Cognito JWT authentication for tenant isolation validation
- Full conversational interface with the Bedrock Agent
- Real-time streaming response display
- End-to-end tracing observability view (CloudWatch structured logs)
- Tool call inspection
"""

import streamlit as st
import json
import uuid

import config
from auth.cognito_auth import CognitoAuthMiddleware
from agent.bedrock_agent import SupportAgent


# =============================================================================
# Page Configuration & Styling
# =============================================================================
st.set_page_config(
    page_title=config.APP_TITLE,
    page_icon=config.APP_PAGE_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .stApp {
        background: #0f172a;
    }
    
    .tenant-badge {
        background: #3b82f6;
        color: white;
        padding: 4px 12px;
        border-radius: 12px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    
    .trace-card {
        background: rgba(255, 255, 255, 0.05);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 6px;
        padding: 12px;
        margin-top: 8px;
        font-family: monospace;
        font-size: 0.8rem;
    }
    
    .tool-badge {
        background: #10b981;
        color: white;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 0.75rem;
        margin-right: 8px;
    }
</style>
""", unsafe_allow_html=True)


# =============================================================================
# Session State Initialization
# =============================================================================
if "messages" not in st.session_state:
    st.session_state.messages = []
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "tenant_id" not in st.session_state:
    st.session_state.tenant_id = None
if "traces" not in st.session_state:
    st.session_state.traces = []
if "tool_calls" not in st.session_state:
    st.session_state.tool_calls = []


# =============================================================================
# Auth Middleware (Cognito Integration)
# =============================================================================
st.sidebar.markdown("## 🔐 Authentication (Cognito)")
st.sidebar.caption("Provide a JWT token to establish tenant context.")

auth_middleware = CognitoAuthMiddleware()

# Mock JWT token for UI demonstration (simulates a decoded Cognito token)
default_token = "eyJhbGciOiJIUzI1NiJ9.eyJjdXN0b206dGVuYW50X2lkIjoiVC00MDIiLCJ1c2VybmFtZSI6InRlc3Rfb3BzX3VzZXIifQ.mock_signature"

jwt_token = st.sidebar.text_input("Cognito JWT Token", value=default_token, type="password")

if jwt_token:
    validation = auth_middleware.validate_token(jwt_token)
    
    if validation.get("is_valid"):
        tenant_id = validation.get("tenant_id")
        st.session_state.tenant_id = tenant_id
        
        st.sidebar.success(f"Authenticated")
        st.sidebar.markdown(
            f'<div class="tenant-badge">Tenant Context: {tenant_id}</div>',
            unsafe_allow_html=True
        )
        st.sidebar.caption(f"User: {validation.get('username')}")
        if validation.get("auth_mode") == "demo_unverified":
            st.sidebar.warning("Demo auth mode: JWT signature verification is disabled.")
    else:
        st.sidebar.error(f"Invalid Token: {validation.get('error', 'Signature verification failed')}")
        st.session_state.tenant_id = None

st.sidebar.divider()

# =============================================================================
# Observability Panel (CloudWatch Integration)
# =============================================================================
st.sidebar.markdown("## 📊 Agent Observability")
st.sidebar.caption("CloudWatch Tracing & Tool Execution")

if st.session_state.tool_calls:
    st.sidebar.markdown("### Recent Tool Calls")
    for call in reversed(st.session_state.tool_calls[-5:]):  # Show last 5
        st.sidebar.markdown(
            f'<div class="trace-card">'
            f'<span class="tool-badge">{call.get("tool")}</span><br/>'
            f'<span style="color: #94a3b8;">{call.get("function")}</span>'
            f'</div>',
            unsafe_allow_html=True
        )

if st.button("Clear Session"):
    st.session_state.messages = []
    st.session_state.session_id = str(uuid.uuid4())
    st.session_state.traces = []
    st.session_state.tool_calls = []
    st.rerun()


# =============================================================================
# Main Application Header
# =============================================================================
st.title(f"{config.APP_PAGE_ICON} {config.APP_TITLE}")
st.caption(
    "Orchestrated via **AWS Bedrock AgentCore** • RAG via **OpenSearch Serverless** • "
    "Logs via **CloudWatch** • Tenant isolation via **Cognito**"
)

# Configuration validation check
config_check = config.validate_config()
if config.DEMO_MODE:
    st.info("Demo mode is enabled. AWS calls and unverified demo JWTs are allowed for local presentation only.")
elif not config_check["valid"]:
    st.warning(f"⚠️ Configure .env for full AWS functionality. Missing: {', '.join(config_check['missing'])}")

st.divider()


# =============================================================================
# Conversation Interface
# =============================================================================

if not st.session_state.tenant_id:
    st.info("👈 Please authenticate with a valid Cognito JWT in the sidebar to begin.")
    st.stop()

# Render chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Handle new user input
if prompt := st.chat_input("E.g., Investigate Database Timeout exceptions for this tenant in the last 24h..."):
    # Render user query
    st.chat_message("user").markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})
    
    with st.chat_message("assistant"):
        agent = SupportAgent()
        
        # Bedrock Agent invocation
        with st.spinner(f"AgentCore processing query (Context: {st.session_state.tenant_id})..."):
            
            # Streaming response placeholder
            response_placeholder = st.empty()
            full_response = ""
            
            try:
                # We use the standard invoke which returns the accumulated text + structured traces
                # AgentCore handles the multi-step tool routing synchronously
                response = agent.invoke(
                    prompt=prompt,
                    session_id=st.session_state.session_id,
                    tenant_id=st.session_state.tenant_id
                )
                
                full_response = response.get("text", "")
                
                # If AgentCore successfully ran but returned nothing, it might be waiting on a tool loop
                if not full_response and response.get("status") == "success":
                    full_response = "I searched the knowledge base, but couldn't find a definitive answer. Can you provide more details about the error code?"
                
                # Check for UI Demo fallback payload
                if response.get("status") == "demo":
                    full_response = response.get("text")
                    
                response_placeholder.markdown(full_response)
                
                # Store traces for sidebar rendering
                if response.get("tool_calls"):
                    st.session_state.tool_calls.extend(response.get("tool_calls"))
                    
                # Ship trace to CloudWatch (via our logger module)
                if response.get("traces"):
                    try:
                        from observability.cloudwatch_logger import AgentTraceLogger
                        logger = AgentTraceLogger()
                        
                        logger.log_request(
                            session_id=st.session_state.session_id,
                            user_query=prompt,
                            agent_response=full_response,
                            total_latency_ms=1250.0, # approximation
                            tenant_id=st.session_state.tenant_id
                        )
                        
                        logger.log_trace(
                            session_id=st.session_state.session_id,
                            trace_data=response.get("traces"),
                            metadata={"tenant_id": st.session_state.tenant_id}
                        )
                    except Exception as e:
                        print(f"[CloudWatch Logging] {e}")
                        
            except Exception as e:
                # Fallback for unconnected AWS environment
                full_response = (
                    f"**Agent System Error**\n\n"
                    f"Failed to communicate with AWS Bedrock AgentCore.\n\n"
                    f"`{str(e)[:150]}`"
                )
                response_placeholder.markdown(full_response)
                
    st.session_state.messages.append({"role": "assistant", "content": full_response})
    
    # Rerun to update the sidebar observability panel
    st.rerun()
