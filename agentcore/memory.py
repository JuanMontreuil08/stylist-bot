"""
Persistent memory for the stylist agent via Amazon Bedrock AgentCore Memory.

Each user (identified by phone number) gets their own session manager that
writes every conversation turn to Bedrock and re-hydrates it on server restart.

Setup (one-time):
  1. Create a Memory resource on AWS:
       aws bedrock-agentcore create-memory \
         --name "stylist-bot-memory" \
         --memory-configuration '{"chatHistory": {"timeToLiveInDays": 90}}' \
         --region us-east-1
  2. Copy the returned memoryId into .env as BEDROCK_MEMORY_ID=<id>
  3. Ensure the IAM role has:
       bedrock-agentcore:CreateEvent
       bedrock-agentcore:ListEvents
       bedrock-agentcore:GetEvent
       bedrock-agentcore-control:GetMemory
     on arn:aws:bedrock-agentcore:<region>:<account>:memory/*

If BEDROCK_MEMORY_ID is not set, make_session_manager() returns None and the
agent falls back to in-process memory (no persistence across restarts).
"""
import os
from dotenv import load_dotenv
from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig

load_dotenv()

_MEMORY_ID = os.getenv("BEDROCK_MEMORY_ID", "").strip()
_REGION    = os.getenv("AWS_DEFAULT_REGION", "us-east-1")


def make_session_manager(phone_number: str) -> AgentCoreMemorySessionManager | None:
    """
    Return a session manager that persists conversation history to Bedrock
    AgentCore Memory for this user, or None if memory is not configured.

    Args:
        phone_number: User's phone in E.164 format (e.g. +51995132783).
                      Used as both actor_id and session_id so each WhatsApp
                      user has one continuous conversation thread.
    """
    if not _MEMORY_ID:
        return None

    # Bedrock only allows [a-zA-Z0-9-_] — strip the leading + from E.164
    safe_id = phone_number.lstrip("+")
    config = AgentCoreMemoryConfig(
        memory_id=_MEMORY_ID,
        actor_id=safe_id,
        session_id=safe_id,
    )
    return AgentCoreMemorySessionManager(
        agentcore_memory_config=config,
        region_name=_REGION,
    )
