"""Multi-Agent System: Manages multiple specialized agents with connectors.

This module provides a framework for running multiple AI agents, each specialized
for different tasks (coding, social media, research, creative, etc.), and connecting
them to external services (Instagram, Twitter, email, etc.).

Agents are organized by specialty and can be switched dynamically. Each agent
has its own system prompt tailored for its domain.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from .config import settings

log = logging.getLogger(__name__)


class AgentType(str, Enum):
    """Available agent types."""
    GENERAL = "general"           # General-purpose assistant
    CODING = "coding"            # Software development specialist
    SOCIAL = "social"            # Social media & content creator
    RESEARCH = "research"        # Research & analysis
    CREATIVE = "creative"        # Creative writing & brainstorming
    BUSINESS = "business"        # Business & productivity
    SUPPORT = "support"          # Customer support & communication
    DATA = "data"                # Data analysis & visualization


class ConnectorType(str, Enum):
    """Available connector types."""
    INSTAGRAM = "instagram"
    TWITTER = "twitter"
    FACEBOOK = "facebook"
    LINKEDIN = "linkedin"
    YOUTUBE = "youtube"
    EMAIL = "email"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    SLACK = "slack"
    WHATSAPP = "whatsapp"
    GITHUB = "github"
    NOTION = "notion"
    GOOGLE_DRIVE = "google_drive"
    DROPBOX = "dropbox"
    TWITCH = "twitch"
    REDDIT = "reddit"


@dataclass
class AgentConfig:
    """Configuration for an agent."""
    name: str
    description: str
    agent_type: AgentType
    system_prompt: str
    enabled_tools: list[str] | None = None  # None = all tools
    max_steps: int = 30
    temperature: float = 0.7
    icon: str = "🤖"
    color: str = "#6366f1"  # Default indigo


@dataclass
class ConnectorConfig:
    """Configuration for a service connector."""
    name: str
    connector_type: ConnectorType
    description: str
    auth_method: str  # "oauth", "api_key", "token", "username_password"
    icon: str = "🔌"
    color: str = "#10b981"  # Default green
    scopes: list[str] = field(default_factory=list)
    requires_auth: bool = True


@dataclass
class ConnectedAccount:
    """A connected service account."""
    id: str
    connector_type: ConnectorType
    account_name: str
    account_id: str
    connected_at: datetime
    auth_data: dict[str, str] = field(default_factory=dict)
    status: str = "active"
    last_used: datetime | None = None


# Agent definitions with specialized system prompts
AGENT_CONFIGS: dict[AgentType, AgentConfig] = {
    AgentType.GENERAL: AgentConfig(
        name="Nova",
        description="Your versatile general-purpose assistant for everyday tasks",
        agent_type=AgentType.GENERAL,
        system_prompt="""You are Nova, a versatile AI assistant powered by Mistral.
You help users with a wide variety of tasks: answering questions, managing files,
scheduling, research, creative work, and much more.

Your strengths:
- Quick, accurate responses to questions
- File and document management
- Web research and information gathering
- Scheduling and reminders
- General problem-solving

You communicate clearly and concisely. When you don't know something, you say so
rather than making things up. You proactively offer helpful suggestions.""",
        icon="🌟",
        color="#f59e0b"  # Amber
    ),
    
    AgentType.CODING: AgentConfig(
        name="CodeBot",
        description="Software development specialist for coding, debugging, and architecture",
        agent_type=AgentType.CODING,
        system_prompt="""You are CodeBot, a senior software developer specializing in coding.
You help users write, debug, and refactor code across multiple languages and frameworks.

Your strengths:
- Writing clean, efficient code in Python, JavaScript, TypeScript, Go, Rust, etc.
- Debugging complex issues with clear explanations
- System architecture and design patterns
- Code reviews and best practices
- Database design and optimization
- DevOps, CI/CD, and deployment

You write production-ready code with proper error handling, tests, and documentation.
When reviewing code, you focus on maintainability, performance, and security.""",
        icon="💻",
        color="#3b82f6"  # Blue
    ),
    
    AgentType.SOCIAL: AgentConfig(
        name="SocialBee",
        description="Social media expert for content creation, posting, and engagement",
        agent_type=AgentType.SOCIAL,
        system_prompt="""You are SocialBee, a social media and content marketing expert.
You help users create engaging content, manage their social presence, and grow their audience.

Your strengths:
- Creating viral-worthy content (posts, captions, hashtags)
- Scheduling and managing posts across platforms
- Engagement strategy and community management
- Analytics and performance insights
- Influencer outreach and collaboration
- Trend awareness and viral timing

You understand the nuances of different platforms (Instagram, Twitter, LinkedIn, TikTok, etc.)
and tailor content accordingly. You focus on authentic engagement over fake growth.""",
        icon="🐝",
        color="#ec4899"  # Pink
    ),
    
    AgentType.RESEARCH: AgentConfig(
        name="Scholar",
        description="Research assistant for analysis, summaries, and deep investigation",
        agent_type=AgentType.RESEARCH,
        system_prompt="""You are Scholar, an expert research assistant specializing in
analysis, investigation, and knowledge synthesis.

Your strengths:
- Deep research on any topic
- Analyzing and synthesizing information from multiple sources
- Writing comprehensive reports and summaries
- Fact-checking and verification
- Academic writing and citations
- Competitive analysis and market research

You cite your sources and distinguish between facts and opinions. You present
complex information in clear, accessible formats. You think critically about
information quality and bias.""",
        icon="📚",
        color="#8b5cf6"  # Purple
    ),
    
    AgentType.CREATIVE: AgentConfig(
        name="Muse",
        description="Creative partner for writing, brainstorming, and artistic projects",
        agent_type=AgentType.CREATIVE,
        system_prompt="""You are Muse, a creative assistant specializing in artistic
expression, storytelling, and creative brainstorming.

Your strengths:
- Creative writing (stories, scripts, poems, songs)
- Brainstorming and ideation
- Brand voice and messaging
- Visual concepts and descriptions
- Marketing copy and advertising
- Game narratives and character development

You think outside the box and aren't afraid to suggest unconventional ideas.
You help users find their creative voice and bring their vision to life.""",
        icon="🎨",
        color="#f97316"  # Orange
    ),
    
    AgentType.BUSINESS: AgentConfig(
        name="Executive",
        description="Business advisor for productivity, strategy, and professional tasks",
        agent_type=AgentType.BUSINESS,
        system_prompt="""You are Executive, a business and productivity advisor.
You help users with professional tasks, strategic thinking, and workflow optimization.

Your strengths:
- Meeting preparation and follow-ups
- Email drafting and management
- Project planning and tracking
- Strategic analysis and recommendations
- Presentation preparation
- Data organization and reporting

You understand business priorities and focus on high-impact activities.
You help users stay organized and productive without losing sight of goals.""",
        icon="💼",
        color="#0ea5e9"  # Sky blue
    ),
    
    AgentType.SUPPORT: AgentConfig(
        name="Helper",
        description="Customer support specialist for communication and issue resolution",
        agent_type=AgentType.SUPPORT,
        system_prompt="""You are Helper, a customer support and communication specialist.
You help users craft responses, resolve issues, and maintain professional communication.

Your strengths:
- Writing clear, empathetic responses
- Conflict resolution and de-escalation
- FAQ creation and knowledge base articles
- Ticket management and prioritization
- Multilingual support
- Feedback synthesis and improvement

You are patient, empathetic, and focus on resolving issues satisfactorily.
You maintain a professional tone while being warm and approachable.""",
        icon="🎧",
        color="#14b8a6"  # Teal
    ),
    
    AgentType.DATA: AgentConfig(
        name="DataPro",
        description="Data analyst for insights, visualization, and analytics",
        agent_type=AgentType.DATA,
        system_prompt="""You are DataPro, a data analysis and visualization expert.
You help users extract insights from data, create reports, and make data-driven decisions.

Your strengths:
- Statistical analysis and interpretation
- Data cleaning and transformation
- Creating clear visualizations and charts
- SQL queries and database insights
- Excel/Google Sheets formulas and automation
- Business intelligence and metrics

You translate complex data into actionable insights. You suggest appropriate
visualizations and highlight key trends and anomalies.""",
        icon="📊",
        color="#22c55e"  # Green
    ),
}


# Connector definitions
CONNECTOR_CONFIGS: dict[ConnectorType, ConnectorConfig] = {
    ConnectorType.INSTAGRAM: ConnectorConfig(
        name="Instagram",
        connector_type=ConnectorType.INSTAGRAM,
        description="Post photos, stories, reels, and manage your Instagram presence",
        auth_method="oauth",
        icon="📸",
        color="#e4405f",
        scopes=["photos", "stories", "reels", "comments", "messages", "analytics"]
    ),
    ConnectorType.TWITTER: ConnectorConfig(
        name="Twitter/X",
        connector_type=ConnectorType.TWITTER,
        description="Post tweets, manage your timeline, and engage with your audience",
        auth_method="oauth",
        icon="🐦",
        color="#1da1f2",
        scopes=["tweets", "media", "likes", "followers", "messages", "analytics"]
    ),
    ConnectorType.FACEBOOK: ConnectorConfig(
        name="Facebook",
        connector_type=ConnectorType.FACEBOOK,
        description="Post to your timeline, pages, and manage Facebook groups",
        auth_method="oauth",
        icon="👥",
        color="#1877f2",
        scopes=["posts", "pages", "groups", "messenger", "analytics"]
    ),
    ConnectorType.LINKEDIN: ConnectorConfig(
        name="LinkedIn",
        connector_type=ConnectorType.LINKEDIN,
        description="Share professional updates, articles, and network with connections",
        auth_method="oauth",
        icon="💼",
        color="#0a66c2",
        scopes=["profile", "posts", "articles", "messages", "connections"]
    ),
    ConnectorType.YOUTUBE: ConnectorConfig(
        name="YouTube",
        connector_type=ConnectorType.YOUTUBE,
        description="Upload videos, manage channels, and interact with your audience",
        auth_method="oauth",
        icon="📺",
        color="#ff0000",
        scopes=["videos", "comments", "community", "analytics", "live"]
    ),
    ConnectorType.EMAIL: ConnectorConfig(
        name="Email",
        connector_type=ConnectorType.EMAIL,
        description="Send and manage emails through Gmail, Outlook, or other providers",
        auth_method="oauth",
        icon="📧",
        color="#ea4335",
        scopes=["send", "read", "labels", "drafts", "contacts"]
    ),
    ConnectorType.TELEGRAM: ConnectorConfig(
        name="Telegram",
        connector_type=ConnectorType.TELEGRAM,
        description="Send messages, manage channels, and bot interactions",
        auth_method="api_key",
        icon="✈️",
        color="#26a5e4",
        scopes=["messages", "channels", "groups", "bots"]
    ),
    ConnectorType.DISCORD: ConnectorConfig(
        name="Discord",
        connector_type=ConnectorType.DISCORD,
        description="Manage servers, send messages, and moderate Discord communities",
        auth_method="bot_token",
        icon="🎮",
        color="#5865f2",
        scopes=["channels", "messages", "guilds", "webhooks"]
    ),
    ConnectorType.SLACK: ConnectorConfig(
        name="Slack",
        connector_type=ConnectorType.SLACK,
        description="Send messages, manage channels, and integrate with your workspace",
        auth_method="oauth",
        icon="💬",
        color="#4a154b",
        scopes=["channels", "messages", "files", "users"]
    ),
    ConnectorType.WHATSAPP: ConnectorConfig(
        name="WhatsApp",
        connector_type=ConnectorType.WHATSAPP,
        description="Send messages and manage WhatsApp Business interactions",
        auth_method="phone_number",
        icon="💬",
        color="#25d366",
        scopes=["messages", "status", "groups"]
    ),
    ConnectorType.GITHUB: ConnectorConfig(
        name="GitHub",
        connector_type=ConnectorType.GITHUB,
        description="Manage repositories, issues, pull requests, and code workflows",
        auth_method="oauth",
        icon="🐙",
        color="#24292e",
        scopes=["repos", "issues", "pulls", "actions", "packages"]
    ),
    ConnectorType.NOTION: ConnectorConfig(
        name="Notion",
        connector_type=ConnectorType.NOTION,
        description="Manage pages, databases, and your Notion workspace",
        auth_method="oauth",
        icon="📓",
        color="#000000",
        scopes=["pages", "databases", "comments", "users"]
    ),
    ConnectorType.GOOGLE_DRIVE: ConnectorConfig(
        name="Google Drive",
        connector_type=ConnectorType.GOOGLE_DRIVE,
        description="Manage files, folders, and collaborate on Google Docs/Sheets",
        auth_method="oauth",
        icon="📁",
        color="#4285f4",
        scopes=["files", "folders", "documents", "sheets", "slides"]
    ),
    ConnectorType.DROPBOX: ConnectorConfig(
        name="Dropbox",
        connector_type=ConnectorType.DROPBOX,
        description="Upload, download, and manage files in your Dropbox",
        auth_method="oauth",
        icon="📦",
        color="#0061ff",
        scopes=["files", "folders", "sharing", "comments"]
    ),
    ConnectorType.TWITCH: ConnectorConfig(
        name="Twitch",
        connector_type=ConnectorType.TWITCH,
        description="Manage streams, chat, and interact with your Twitch community",
        auth_method="oauth",
        icon="📺",
        color="#9146ff",
        scopes=["channel", "chat", "bits", "subscriptions", "analytics"]
    ),
    ConnectorType.REDDIT: ConnectorConfig(
        name="Reddit",
        connector_type=ConnectorType.REDDIT,
        description="Post, comment, and manage your Reddit presence",
        auth_method="oauth",
        icon="🤖",
        color="#ff4500",
        scopes=["posts", "comments", "messages", "subreddits", "karma"]
    ),
}


class MultiAgentManager:
    """Manages multiple agents and their connectors."""
    
    def __init__(self):
        self.agents: dict[str, Any] = {}  # conversation_id -> agent state
        self.active_agents: dict[str, AgentType] = {}  # conversation_id -> agent type
        self.connectors: dict[str, ConnectedAccount] = {}  # connector instance id -> account
        self.connector_configs: dict[ConnectorType, ConnectorConfig] = CONNECTOR_CONFIGS.copy()
        self.agent_configs: dict[AgentType, AgentConfig] = AGENT_CONFIGS.copy()
    
    def get_agent_config(self, agent_type: AgentType) -> AgentConfig:
        """Get the configuration for an agent type."""
        return self.agent_configs.get(agent_type, AGENT_CONFIGS[AgentType.GENERAL])
    
    def get_connector_config(self, connector_type: ConnectorType) -> ConnectorConfig:
        """Get the configuration for a connector type."""
        return self.connector_configs.get(connector_type)
    
    def list_agents(self) -> list[dict[str, Any]]:
        """List all available agent types."""
        return [
            {
                "type": agent_type.value,
                "name": config.name,
                "description": config.description,
                "icon": config.icon,
                "color": config.color,
            }
            for agent_type, config in self.agent_configs.items()
        ]
    
    def list_connectors(self) -> list[dict[str, Any]]:
        """List all available connector types."""
        return [
            {
                "type": connector_type.value,
                "name": config.name,
                "description": config.description,
                "auth_method": config.auth_method,
                "icon": config.icon,
                "color": config.color,
                "requires_auth": config.requires_auth,
                "scopes": config.scopes,
            }
            for connector_type, config in self.connector_configs.items()
        ]
    
    def list_connected_accounts(self) -> list[dict[str, Any]]:
        """List all connected accounts (without sensitive data)."""
        return [
            {
                "id": conn.id,
                "type": conn.connector_type.value,
                "name": conn.account_name,
                "account_id": conn.account_id,
                "connected_at": conn.connected_at.isoformat(),
                "status": conn.status,
                "last_used": conn.last_used.isoformat() if conn.last_used else None,
            }
            for conn in self.connectors.values()
        ]
    
    def set_active_agent(self, conversation_id: str, agent_type: AgentType) -> dict[str, Any]:
        """Set the active agent type for a conversation."""
        config = self.get_agent_config(agent_type)
        self.active_agents[conversation_id] = agent_type
        
        # Ensure agent state exists
        if conversation_id not in self.agents:
            self.agents[conversation_id] = {
                "type": agent_type,
                "name": config.name,
                "created_at": datetime.now().isoformat(),
                "tools_used": [],
                "tasks_completed": 0,
            }
        
        return {
            "ok": True,
            "agent_type": agent_type.value,
            "agent_name": config.name,
            "agent_icon": config.icon,
            "agent_color": config.color,
            "description": config.description,
        }
    
    def get_active_agent(self, conversation_id: str) -> dict[str, Any]:
        """Get the active agent info for a conversation."""
        agent_type = self.active_agents.get(conversation_id, AgentType.GENERAL)
        config = self.get_agent_config(agent_type)
        
        return {
            "type": agent_type.value,
            "name": config.name,
            "description": config.description,
            "icon": config.icon,
            "color": config.color,
            "system_prompt": config.system_prompt,
        }
    
    def connect_account(self, connector_type: ConnectorType, account_name: str, 
                       account_id: str, auth_data: dict[str, str]) -> dict[str, Any]:
        """Connect a new account to a connector."""
        conn_id = str(uuid4())
        
        account = ConnectedAccount(
            id=conn_id,
            connector_type=connector_type,
            account_name=account_name,
            account_id=account_id,
            connected_at=datetime.now(),
            auth_data=auth_data,
            status="active",
        )
        
        self.connectors[conn_id] = account
        
        return {
            "ok": True,
            "id": conn_id,
            "type": connector_type.value,
            "name": account_name,
            "account_id": account_id,
            "connected_at": account.connected_at.isoformat(),
            "status": "active",
        }
    
    def disconnect_account(self, account_id: str) -> dict[str, Any]:
        """Disconnect an account."""
        if account_id in self.connectors:
            account = self.connectors[account_id]
            account.status = "disconnected"
            return {"ok": True, "id": account_id, "status": "disconnected"}
        return {"ok": False, "error": "Account not found"}
    
    def get_connector_for_type(self, connector_type: ConnectorType) -> ConnectedAccount | None:
        """Get the first connected account for a connector type."""
        for account in self.connectors.values():
            if account.connector_type == connector_type and account.status == "active":
                return account
        return None
    
    def use_connector(self, account_id: str) -> dict[str, Any]:
        """Mark a connector as used."""
        if account_id in self.connectors:
            self.connectors[account_id].last_used = datetime.now()
            return {"ok": True}
        return {"ok": False, "error": "Connector not found"}
    
    def update_connector_auth(self, account_id: str, auth_data: dict[str, str]) -> dict[str, Any]:
        """Update auth data for a connected account."""
        if account_id in self.connectors:
            self.connectors[account_id].auth_data.update(auth_data)
            return {"ok": True}
        return {"ok": False, "error": "Connector not found"}
    
    def get_agent_system_prompt(self, conversation_id: str) -> str:
        """Get the system prompt for the active agent in a conversation."""
        agent_info = self.get_active_agent(conversation_id)
        base_prompt = agent_info.get("system_prompt", "")
        
        # Add connector context if any are connected
        connected = self.list_connected_accounts()
        if connected:
            connector_names = [c["name"] for c in connected]
            connector_context = f"\n\nConnected services: {', '.join(connector_names)}"
            base_prompt += connector_context
        
        return base_prompt


# Singleton instance
MANAGER = MultiAgentManager()