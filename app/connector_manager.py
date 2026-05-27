"""Connector Manager: Bridges agents with external services.

Manages connections to external platforms (Instagram, Twitter, etc.)
and provides tools for agents to interact with connected accounts.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from .agents import (
    AgentType,
    ConnectedAccount,
    MANAGER as AGENT_MANAGER,
)
from .connectors import (
    BaseConnector,
    ContentPost,
    CONNECTOR_CLASSES,
    ConnectorPlatform,
    get_connector,
    list_platforms,
)

log = logging.getLogger(__name__)


class ConnectorManager:
    """Manages all connector instances and provides agent tools."""
    
    def __init__(self):
        self._instances: dict[str, BaseConnector] = {}  # account_id -> connector instance
        self._accounts: dict[str, ConnectedAccount] = {}  # account_id -> account info
        self._platforms = list_platforms()
    
    def list_available_platforms(self) -> list[dict[str, Any]]:
        """List all available platforms."""
        return self._platforms
    
    def list_connected_accounts(self) -> list[dict[str, Any]]:
        """List all connected accounts."""
        return AGENT_MANAGER.list_connected_accounts()
    
    def get_account_by_id(self, account_id: str) -> ConnectedAccount | None:
        """Get account info by ID."""
        return self._accounts.get(account_id)
    
    def get_account_by_platform(self, platform: ConnectorPlatform) -> ConnectedAccount | None:
        """Get the first connected account for a platform."""
        for account in self._accounts.values():
            if account.connector_type.value == platform.value:
                return account
        return None
    
    def connect_account(self, platform: str, account_name: str, 
                       account_id: str, auth_data: dict[str, str]) -> dict[str, Any]:
        """Connect a new account to a platform."""
        try:
            platform_enum = ConnectorPlatform(platform.lower())
        except ValueError:
            return {"ok": False, "error": f"Unknown platform: {platform}"}
        
        # Create connector instance
        connector_class = CONNECTOR_CLASSES.get(platform_enum)
        if not connector_class:
            return {"ok": False, "error": f"Platform {platform} not supported"}
        
        connector = connector_class(auth_data)
        
        # Test connection
        result = AGENT_MANAGER.connect_account(
            connector_type=AgentType.SOCIAL,  # Use generic type
            account_name=account_name,
            account_id=account_id,
            auth_data=auth_data,
        )
        
        if result.get("ok"):
            account = ConnectedAccount(
                id=result["id"],
                connector_type=platform_enum,
                account_name=account_name,
                account_id=account_id,
                connected_at=datetime.now(),
                auth_data=auth_data,
            )
            self._accounts[result["id"]] = account
            self._instances[result["id"]] = connector
            
            return {
                "ok": True,
                "id": result["id"],
                "platform": platform,
                "account_name": account_name,
                "account_id": account_id,
            }
        
        return result
    
    def disconnect_account(self, account_id: str) -> dict[str, Any]:
        """Disconnect an account."""
        if account_id in self._instances:
            connector = self._instances[account_id]
            # Run disconnect asynchronously
            try:
                import asyncio
                asyncio.get_event_loop().run_until_complete(connector.disconnect())
            except Exception:
                pass
            del self._instances[account_id]
        
        if account_id in self._accounts:
            del self._accounts[account_id]
        
        return AGENT_MANAGER.disconnect_account(account_id)
    
    async def post_to_platform(self, platform: str, text: str,
                              image_urls: list[str] = None,
                              tags: list[str] = None,
                              mentions: list[str] = None,
                              link: str = "") -> dict[str, Any]:
        """Post content to a connected platform."""
        try:
            platform_enum = ConnectorPlatform(platform.lower())
        except ValueError:
            return {"ok": False, "error": f"Unknown platform: {platform}"}
        
        # Find connected account for this platform
        account = self.get_account_by_platform(platform_enum)
        if not account:
            return {"ok": False, "error": f"No connected account for {platform}"}
        
        # Get or create connector instance
        connector = self._instances.get(account.id)
        if not connector:
            connector = get_connector(platform_enum, account.auth_data)
            self._instances[account.id] = connector
        
        # Create content post
        content = ContentPost(
            text=text,
            image_urls=image_urls or [],
            tags=tags or [],
            mentions=mentions or [],
            link=link,
        )
        
        # Post content
        try:
            result = await connector.post_content(content)
            
            # Mark connector as used
            AGENT_MANAGER.use_connector(account.id)
            
            return result
        except Exception as exc:
            log.error(f"Post to {platform} failed: {exc}")
            return {"ok": False, "error": str(exc)}
    
    async def get_account_info(self, platform: str) -> dict[str, Any]:
        """Get account info from a connected platform."""
        try:
            platform_enum = ConnectorPlatform(platform.lower())
        except ValueError:
            return {"ok": False, "error": f"Unknown platform: {platform}"}
        
        account = self.get_account_by_platform(platform_enum)
        if not account:
            return {"ok": False, "error": f"No connected account for {platform}"}
        
        connector = self._instances.get(account.id)
        if not connector:
            connector = get_connector(platform_enum, account.auth_data)
            self._instances[account.id] = connector
        
        try:
            return await connector.get_account_info()
        except Exception as exc:
            log.error(f"Get account info from {platform} failed: {exc}")
            return {"ok": False, "error": str(exc)}
    
    def get_connected_platforms(self) -> list[str]:
        """Get list of platforms with connected accounts."""
        platforms = set()
        for account in self._accounts.values():
            platforms.add(account.connector_type.value)
        return list(platforms)


# Agent tools for connector operations
def connect_service(platform: str, account_name: str = "", 
                   account_id: str = "", **auth_kwargs) -> dict[str, Any]:
    """Connect a new service account.
    
    Args:
        platform: Platform name (instagram, twitter, github, etc.)
        account_name: Display name for the account
        account_id: Username or ID on the platform
        **auth_kwargs: Platform-specific auth data (access_token, api_key, etc.)
    """
    manager = ConnectorManager()
    return manager.connect_account(platform, account_name, account_id, auth_kwargs)


def disconnect_service(account_id: str) -> dict[str, Any]:
    """Disconnect a service account.
    
    Args:
        account_id: The ID of the connected account to disconnect
    """
    manager = ConnectorManager()
    return manager.disconnect_account(account_id)


def list_connected_services() -> dict[str, Any]:
    """List all connected service accounts."""
    manager = ConnectorManager()
    accounts = manager.list_connected_accounts()
    platforms = manager.list_available_platforms()
    
    return {
        "ok": True,
        "connected_accounts": accounts,
        "available_platforms": platforms,
        "total_connected": len(accounts),
    }


def post_content(platform: str, text: str, image_urls: str = "", 
               tags: str = "", mentions: str = "", link: str = "") -> dict[str, Any]:
    """Post content to a connected service.
    
    Args:
        platform: Platform to post to (instagram, twitter, etc.)
        text: Content text to post
        image_urls: Comma-separated list of image URLs (optional)
        tags: Comma-separated hashtags (optional)
        mentions: Comma-separated @mentions (optional)
        link: URL to include (optional)
    """
    import asyncio
    
    manager = ConnectorManager()
    
    # Parse comma-separated values
    image_list = [u.strip() for u in image_urls.split(",") if u.strip()] if image_urls else []
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    mention_list = [m.strip() for m in mentions.split(",") if m.strip()] if mentions else []
    
    # Run async post
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    return loop.run_until_complete(
        manager.post_to_platform(platform, text, image_list, tag_list, mention_list, link)
    )


def get_service_info(platform: str) -> dict[str, Any]:
    """Get information about a connected service account.
    
    Args:
        platform: Platform to get info from (instagram, twitter, etc.)
    """
    import asyncio
    
    manager = ConnectorManager()
    
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    return loop.run_until_complete(manager.get_account_info(platform))


# Global manager instance
CONNECTOR_MANAGER = ConnectorManager()


# Registry of connector tools for the agent
CONNECTOR_TOOLS = {
    fn.__name__: fn
    for fn in [
        connect_service,
        disconnect_service,
        list_connected_services,
        post_content,
        get_service_info,
    ]
}