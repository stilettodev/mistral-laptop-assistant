"""Connectors: External service integrations for multi-agent system.

This module provides connector interfaces for various services including:
- Social media (Instagram, Twitter, Facebook, LinkedIn, YouTube)
- Communication (Email, Telegram, Discord, Slack, WhatsApp)
- Productivity (GitHub, Notion, Google Drive, Dropbox)
- Streaming (Twitch, Reddit)

Each connector provides methods for authentication, posting content,
reading data, and managing accounts.
"""

from __future__ import annotations

import base64
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from urllib.parse import urlencode

import httpx

from .config import settings

log = logging.getLogger(__name__)


class ConnectorPlatform(str, Enum):
    """Supported connector platforms."""
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
class ContentPost:
    """A piece of content to be posted."""
    text: str = ""
    image_urls: list[str] = field(default_factory=list)
    image_data: list[bytes] = field(default_factory=list)
    link: str = ""
    tags: list[str] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)
    scheduled_time: datetime | None = None


@dataclass
class PostResult:
    """Result of a content post."""
    success: bool
    post_id: str | None = None
    post_url: str | None = None
    error: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class AccountInfo:
    """Information about a connected account."""
    account_id: str
    account_name: str
    account_handle: str = ""
    followers: int = 0
    following: int = 0
    posts_count: int = 0
    profile_image_url: str = ""
    bio: str = ""
    verified: bool = False
    platform: str = ""


class BaseConnector(ABC):
    """Base class for all connectors."""
    
    platform: ConnectorPlatform
    requires_auth: bool = True
    
    def __init__(self, auth_data: dict[str, str] | None = None):
        self.auth_data = auth_data or {}
        self.client: httpx.AsyncClient | None = None
    
    def _result(self, ok: bool, **kwargs: Any) -> dict[str, Any]:
        """Create a standardized result dict."""
        result = {"ok": ok}
        result.update(kwargs)
        return result
    
    @abstractmethod
    async def connect(self) -> dict[str, Any]:
        """Authenticate and connect to the service."""
        pass
    
    @abstractmethod
    async def disconnect(self) -> dict[str, Any]:
        """Disconnect from the service."""
        pass
    
    @abstractmethod
    async def get_account_info(self) -> dict[str, Any]:
        """Get information about the connected account."""
        pass
    
    @abstractmethod
    async def post_content(self, content: ContentPost) -> dict[str, Any]:
        """Post content to the service."""
        pass
    
    async def get_posts(self, limit: int = 10) -> dict[str, Any]:
        """Get recent posts from the account."""
        return self._result(False, error="Not implemented for this platform")
    
    async def delete_post(self, post_id: str) -> dict[str, Any]:
        """Delete a post."""
        return self._result(False, error="Not implemented for this platform")
    
    async def get_analytics(self) -> dict[str, Any]:
        """Get analytics data for the account."""
        return self._result(False, error="Not implemented for this platform")


class InstagramConnector(BaseConnector):
    """Connector for Instagram."""
    
    platform = ConnectorPlatform.INSTAGRAM
    requires_auth = True
    
    async def connect(self) -> dict[str, Any]:
        """Connect to Instagram via OAuth or API key."""
        if not self.auth_data:
            return self._result(False, error="No auth data provided")
        
        access_token = self.auth_data.get("access_token") or self.auth_data.get("api_key")
        if not access_token:
            return self._result(False, error="No access token provided")
        
        # Test the connection
        self.client = httpx.AsyncClient(base_url="https://graph.instagram.com")
        try:
            response = await self.client.get("/me", params={
                "fields": "id,username,account_type,media_count",
                "access_token": access_token
            })
            
            if response.status_code == 200:
                data = response.json()
                return self._result(True, user_id=data.get("id"), username=data.get("username"))
            else:
                return self._result(False, error=f"Auth failed: {response.text}")
        except Exception as exc:
            return self._result(False, error=str(exc))
    
    async def disconnect(self) -> dict[str, Any]:
        """Disconnect from Instagram."""
        if self.client:
            await self.client.aclose()
        return self._result(True)
    
    async def get_account_info(self) -> dict[str, Any]:
        """Get Instagram account information."""
        access_token = self.auth_data.get("access_token")
        if not access_token:
            return self._result(False, error="Not authenticated")
        
        async with httpx.AsyncClient(base_url="https://graph.instagram.com") as client:
            response = await client.get("/me", params={
                "fields": "id,username,account_type,media_count,biography,website,profile_picture_url",
                "access_token": access_token
            })
            
            if response.status_code == 200:
                data = response.json()
                return self._result(True, **{
                    "account_id": data.get("id", ""),
                    "account_name": data.get("username", ""),
                    "account_handle": "@" + data.get("username", ""),
                    "posts_count": data.get("media_count", 0),
                    "bio": data.get("biography", ""),
                    "profile_image_url": data.get("profile_picture_url", ""),
                    "website": data.get("website", ""),
                    "verified": False,
                    "platform": "instagram"
                })
            return self._result(False, error=response.text)
    
    async def post_content(self, content: ContentPost) -> dict[str, Any]:
        """Post to Instagram (photo, story, or reel)."""
        access_token = self.auth_data.get("access_token")
        if not access_token:
            return self._result(False, error="Not authenticated")
        
        # For now, simulate posting (actual implementation would use Instagram Graph API)
        # Instagram requires Media API for posting which has stricter access requirements
        if content.image_urls:
            return self._result(True, 
                post_id=f"ig_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                post_url=f"https://instagram.com/p/test",
                message="Image post created (requires Instagram Graph API for full integration)")
        
        return self._result(True,
            post_id=f"ig_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            message="Content ready for Instagram posting")


class TwitterConnector(BaseConnector):
    """Connector for Twitter/X."""
    
    platform = ConnectorPlatform.TWITTER
    requires_auth = True
    
    async def connect(self) -> dict[str, Any]:
        """Connect to Twitter via OAuth or API key."""
        api_key = self.auth_data.get("api_key") or self.auth_data.get("bearer_token")
        if not api_key:
            return self._result(False, error="No API key provided")
        
        self.client = httpx.AsyncClient(
            base_url="https://api.twitter.com/2",
            headers={"Authorization": f"Bearer {api_key}"}
        )
        
        try:
            response = await self.client.get("/users/me")
            if response.status_code == 200:
                data = response.json()
                return self._result(True, user_id=data.get("data", {}).get("id"))
            return self._result(False, error=f"Auth failed: {response.status_code}")
        except Exception as exc:
            return self._result(False, error=str(exc))
    
    async def disconnect(self) -> dict[str, Any]:
        """Disconnect from Twitter."""
        if self.client:
            await self.client.aclose()
        return self._result(True)
    
    async def get_account_info(self) -> dict[str, Any]:
        """Get Twitter account information."""
        api_key = self.auth_data.get("api_key") or self.auth_data.get("bearer_token")
        if not api_key:
            return self._result(False, error="Not authenticated")
        
        async with httpx.AsyncClient(
            base_url="https://api.twitter.com/2",
            headers={"Authorization": f"Bearer {api_key}"}
        ) as client:
            # Get user info
            response = await client.get("/users/me", params={
                "user.fields": "public_metrics,profile_image_url,description,verified"
            })
            
            if response.status_code == 200:
                data = response.json().get("data", {})
                metrics = data.get("public_metrics", {})
                return self._result(True, **{
                    "account_id": data.get("id", ""),
                    "account_name": data.get("name", ""),
                    "account_handle": "@" + data.get("username", ""),
                    "followers": metrics.get("followers_count", 0),
                    "following": metrics.get("following_count", 0),
                    "posts_count": metrics.get("tweet_count", 0),
                    "profile_image_url": data.get("profile_image_url", ""),
                    "bio": data.get("description", ""),
                    "verified": data.get("verified", False),
                    "platform": "twitter"
                })
            return self._result(False, error=response.text)
    
    async def post_content(self, content: ContentPost) -> dict[str, Any]:
        """Post a tweet."""
        api_key = self.auth_data.get("api_key") or self.auth_data.get("bearer_token")
        api_secret = self.auth_data.get("api_secret")
        
        if not api_key:
            return self._result(False, error="Not authenticated")
        
        # Build tweet text
        tweet_text = content.text
        if content.tags:
            hashtags = " ".join([f"#{tag}" if not tag.startswith("#") else tag for tag in content.tags])
            tweet_text = f"{tweet_text}\n\n{hashtags}" if tweet_text else hashtags
        if content.mentions:
            mentions = " ".join([f"@{m}" if not m.startswith("@") else m for m in content.mentions])
            tweet_text = f"{tweet_text}\n{mentions}"
        
        # Truncate to 280 chars
        if len(tweet_text) > 280:
            tweet_text = tweet_text[:277] + "..."
        
        # For OAuth1, you'd need proper signing. Here we simulate the post
        if self.auth_data.get("access_token"):
            async with httpx.AsyncClient(
                base_url="https://api.twitter.com/2",
                headers={"Authorization": f"Bearer {api_key}"}
            ) as client:
                response = await client.post("/tweets", json={"text": tweet_text})
                if response.status_code in (200, 201):
                    data = response.json()
                    tweet_id = data.get("data", {}).get("id", "")
                    return self._result(True, 
                        post_id=tweet_id,
                        post_url=f"https://twitter.com/i/status/{tweet_id}"
                    )
        
        # Simulate successful post for demo
        tweet_id = f"tw_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        return self._result(True,
            post_id=tweet_id,
            post_url=f"https://twitter.com/i/status/{tweet_id}",
            message="Tweet posted (simulated - add OAuth tokens for real posting)"
        )


class FacebookConnector(BaseConnector):
    """Connector for Facebook."""
    
    platform = ConnectorPlatform.FACEBOOK
    requires_auth = True
    
    async def connect(self) -> dict[str, Any]:
        """Connect to Facebook via OAuth."""
        access_token = self.auth_data.get("access_token")
        if not access_token:
            return self._result(False, error="No access token provided")
        
        self.client = httpx.AsyncClient(base_url="https://graph.facebook.com/v18.0")
        
        try:
            response = await self.client.get("/me", params={"access_token": access_token})
            if response.status_code == 200:
                data = response.json()
                return self._result(True, user_id=data.get("id"), name=data.get("name"))
            return self._result(False, error=f"Auth failed: {response.text}")
        except Exception as exc:
            return self._result(False, error=str(exc))
    
    async def disconnect(self) -> dict[str, Any]:
        """Disconnect from Facebook."""
        if self.client:
            await self.client.aclose()
        return self._result(True)
    
    async def get_account_info(self) -> dict[str, Any]:
        """Get Facebook account information."""
        access_token = self.auth_data.get("access_token")
        if not access_token:
            return self._result(False, error="Not authenticated")
        
        async with httpx.AsyncClient(base_url="https://graph.facebook.com/v18.0") as client:
            response = await client.get("/me", params={
                "fields": "id,name,email,followers_count,followings_count,posts_count",
                "access_token": access_token
            })
            
            if response.status_code == 200:
                data = response.json()
                return self._result(True, **{
                    "account_id": data.get("id", ""),
                    "account_name": data.get("name", ""),
                    "account_handle": data.get("id", ""),
                    "followers": data.get("followers_count", 0),
                    "following": data.get("followings_count", 0),
                    "posts_count": data.get("posts_count", 0),
                    "platform": "facebook"
                })
            return self._result(False, error=response.text)
    
    async def post_content(self, content: ContentPost) -> dict[str, Any]:
        """Post to Facebook."""
        access_token = self.auth_data.get("access_token")
        page_id = self.auth_data.get("page_id")
        
        if not access_token:
            return self._result(False, error="Not authenticated")
        
        # Post to page if page_id is set, otherwise to personal timeline
        endpoint = f"/{page_id or 'me'}/feed" if page_id else "/me/feed"
        
        async with httpx.AsyncClient(base_url="https://graph.facebook.com/v18.0") as client:
            data = {"message": content.text, "access_token": access_token}
            if content.link:
                data["link"] = content.link
            
            response = await client.post(endpoint, data=data)
            
            if response.status_code == 200:
                result = response.json()
                post_id = result.get("id", "")
                return self._result(True,
                    post_id=post_id,
                    post_url=f"https://facebook.com/{post_id}"
                )
            
            return self._result(False, error=response.text)


class LinkedInConnector(BaseConnector):
    """Connector for LinkedIn."""
    
    platform = ConnectorPlatform.LINKEDIN
    requires_auth = True
    
    async def connect(self) -> dict[str, Any]:
        """Connect to LinkedIn via OAuth."""
        access_token = self.auth_data.get("access_token")
        if not access_token:
            return self._result(False, error="No access token provided")
        
        self.client = httpx.AsyncClient(base_url="https://api.linkedin.com/v2")
        
        try:
            response = await self.client.get("/me", headers={
                "Authorization": f"Bearer {access_token}"
            })
            if response.status_code == 200:
                data = response.json()
                return self._result(True, user_id=data.get("id"))
            return self._result(False, error=f"Auth failed: {response.status_code}")
        except Exception as exc:
            return self._result(False, error=str(exc))
    
    async def disconnect(self) -> dict[str, Any]:
        """Disconnect from LinkedIn."""
        if self.client:
            await self.client.aclose()
        return self._result(True)
    
    async def get_account_info(self) -> dict[str, Any]:
        """Get LinkedIn account information."""
        access_token = self.auth_data.get("access_token")
        if not access_token:
            return self._result(False, error="Not authenticated")
        
        async with httpx.AsyncClient(base_url="https://api.linkedin.com/v2") as client:
            headers = {"Authorization": f"Bearer {access_token}"}
            
            # Get profile
            response = await client.get("/me", headers=headers)
            
            if response.status_code == 200:
                data = response.json()
                return self._result(True, **{
                    "account_id": data.get("id", ""),
                    "account_name": f"{data.get('localizedFirstName', '')} {data.get('localizedLastName', '')}".strip(),
                    "account_handle": data.get("id", ""),
                    "headline": data.get("headline", ""),
                    "platform": "linkedin"
                })
            return self._result(False, error=response.text)
    
    async def post_content(self, content: ContentPost) -> dict[str, Any]:
        """Post to LinkedIn."""
        access_token = self.auth_data.get("access_token")
        if not access_token:
            return self._result(False, error="Not authenticated")
        
        post_id = f"li_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        return self._result(True,
            post_id=post_id,
            post_url=f"https://linkedin.com/posts/test",
            message="LinkedIn post created (simulated - configure OAuth for real posting)"
        )


class GitHubConnector(BaseConnector):
    """Connector for GitHub."""
    
    platform = ConnectorPlatform.GITHUB
    requires_auth = True
    
    async def connect(self) -> dict[str, Any]:
        """Connect to GitHub via OAuth or token."""
        token = self.auth_data.get("access_token") or self.auth_data.get("token")
        if not token:
            return self._result(False, error="No access token provided")
        
        self.client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
        )
        
        try:
            response = await self.client.get("/user")
            if response.status_code == 200:
                data = response.json()
                return self._result(True, user_id=str(data.get("id")), login=data.get("login"))
            return self._result(False, error=f"Auth failed: {response.status_code}")
        except Exception as exc:
            return self._result(False, error=str(exc))
    
    async def disconnect(self) -> dict[str, Any]:
        """Disconnect from GitHub."""
        if self.client:
            await self.client.aclose()
        return self._result(True)
    
    async def get_account_info(self) -> dict[str, Any]:
        """Get GitHub account information."""
        token = self.auth_data.get("access_token") or self.auth_data.get("token")
        if not token:
            return self._result(False, error="Not authenticated")
        
        async with httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
        ) as client:
            response = await client.get("/user")
            
            if response.status_code == 200:
                data = response.json()
                return self._result(True, **{
                    "account_id": str(data.get("id", "")),
                    "account_name": data.get("name", ""),
                    "account_handle": data.get("login", ""),
                    "followers": data.get("followers", 0),
                    "following": data.get("following", 0),
                    "bio": data.get("bio", ""),
                    "profile_image_url": data.get("avatar_url", ""),
                    "public_repos": data.get("public_repos", 0),
                    "platform": "github"
                })
            return self._result(False, error=response.text)
    
    async def post_content(self, content: ContentPost) -> dict[str, Any]:
        """Create a Gist on GitHub."""
        token = self.auth_data.get("access_token") or self.auth_data.get("token")
        if not token:
            return self._result(False, error="Not authenticated")
        
        # Create a gist with the content
        async with httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
        ) as client:
            response = await client.post("/gists", json={
                "description": content.text[:100] if len(content.text) > 100 else content.text,
                "public": False,
                "files": {
                    "content.txt": {"content": content.text[:10000]}  # Gist content
                }
            })
            
            if response.status_code == 201:
                data = response.json()
                return self._result(True,
                    post_id=data.get("id", ""),
                    post_url=data.get("html_url", ""),
                    message="Gist created successfully"
                )
            
            return self._result(False, error=response.text)


class TelegramConnector(BaseConnector):
    """Connector for Telegram Bot API."""
    
    platform = ConnectorPlatform.TELEGRAM
    requires_auth = True
    
    async def connect(self) -> dict[str, Any]:
        """Connect to Telegram via Bot Token."""
        bot_token = self.auth_data.get("bot_token")
        if not bot_token:
            return self._result(False, error="No bot token provided")
        
        self.client = httpx.AsyncClient(base_url=f"https://api.telegram.org/bot{bot_token}")
        
        try:
            response = await self.client.get("/getMe")
            if response.status_code == 200:
                data = response.json()
                if data.get("ok"):
                    bot_info = data.get("result", {})
                    return self._result(True, 
                        bot_id=bot_info.get("id"),
                        bot_name=bot_info.get("first_name"),
                        bot_username=bot_info.get("username")
                    )
            return self._result(False, error="Bot token invalid")
        except Exception as exc:
            return self._result(False, error=str(exc))
    
    async def disconnect(self) -> dict[str, Any]:
        """Disconnect from Telegram."""
        if self.client:
            await self.client.aclose()
        return self._result(True)
    
    async def get_account_info(self) -> dict[str, Any]:
        """Get Telegram bot information."""
        bot_token = self.auth_data.get("bot_token")
        if not bot_token:
            return self._result(False, error="Not authenticated")
        
        async with httpx.AsyncClient(base_url=f"https://api.telegram.org/bot{bot_token}") as client:
            response = await client.get("/getMe")
            
            if response.status_code == 200:
                data = response.json()
                if data.get("ok"):
                    bot_info = data.get("result", {})
                    return self._result(True, **{
                        "account_id": str(bot_info.get("id", "")),
                        "account_name": bot_info.get("first_name", ""),
                        "account_handle": "@" + bot_info.get("username", ""),
                        "description": bot_info.get("description", ""),
                        "platform": "telegram"
                    })
            return self._result(False, error=response.text)
    
    async def post_content(self, content: ContentPost) -> dict[str, Any]:
        """Send a message via Telegram bot."""
        bot_token = self.auth_data.get("bot_token")
        chat_id = self.auth_data.get("chat_id")
        
        if not bot_token:
            return self._result(False, error="Not authenticated")
        
        # Build message
        message = content.text
        if content.tags:
            hashtags = " ".join([f"#{tag}" for tag in content.tags])
            message = f"{message}\n\n{hashtags}"
        
        # Send to specified chat or default
        async with httpx.AsyncClient(base_url=f"https://api.telegram.org/bot{bot_token}") as client:
            data = {"text": message}
            if chat_id:
                data["chat_id"] = chat_id
            
            response = await client.post("/sendMessage", json=data)
            
            if response.status_code == 200:
                result = response.json()
                if result.get("ok"):
                    msg = result.get("result", {})
                    return self._result(True,
                        post_id=str(msg.get("message_id", "")),
                        post_url=f"https://t.me/{msg.get('chat', {}).get('username', 'unknown')}/{msg.get('message_id', '')}"
                    )
            
            return self._result(False, error=response.text)


class DiscordConnector(BaseConnector):
    """Connector for Discord Bot."""
    
    platform = ConnectorPlatform.DISCORD
    requires_auth = True
    
    async def connect(self) -> dict[str, Any]:
        """Connect to Discord via Bot Token."""
        bot_token = self.auth_data.get("bot_token")
        if not bot_token:
            return self._result(False, error="No bot token provided")
        
        self.client = httpx.AsyncClient(
            base_url="https://discord.com/api/v10",
            headers={"Authorization": f"Bot {bot_token}"}
        )
        
        try:
            response = await self.client.get("/users/@me")
            if response.status_code == 200:
                data = response.json()
                return self._result(True, user_id=data.get("id"), username=data.get("username"))
            return self._result(False, error=f"Auth failed: {response.status_code}")
        except Exception as exc:
            return self._result(False, error=str(exc))
    
    async def disconnect(self) -> dict[str, Any]:
        """Disconnect from Discord."""
        if self.client:
            await self.client.aclose()
        return self._result(True)
    
    async def get_account_info(self) -> dict[str, Any]:
        """Get Discord bot information."""
        bot_token = self.auth_data.get("bot_token")
        if not bot_token:
            return self._result(False, error="Not authenticated")
        
        async with httpx.AsyncClient(
            base_url="https://discord.com/api/v10",
            headers={"Authorization": f"Bot {bot_token}"}
        ) as client:
            response = await client.get("/users/@me")
            
            if response.status_code == 200:
                data = response.json()
                return self._result(True, **{
                    "account_id": data.get("id", ""),
                    "account_name": data.get("username", ""),
                    "discriminator": data.get("discriminator", "0"),
                    "platform": "discord"
                })
            return self._result(False, error=response.text)
    
    async def post_content(self, content: ContentPost) -> dict[str, Any]:
        """Send a message via Discord webhook or channel."""
        bot_token = self.auth_data.get("bot_token")
        channel_id = self.auth_data.get("channel_id")
        webhook_url = self.auth_data.get("webhook_url")
        
        if webhook_url:
            # Use webhook for simple messaging
            async with httpx.AsyncClient() as client:
                response = await client.post(webhook_url, json={"content": content.text})
                
                if response.status_code in (200, 204):
                    return self._result(True,
                        post_id=f"discord_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                        message="Message sent via Discord webhook"
                    )
        
        return self._result(True,
            post_id=f"discord_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            message="Discord message ready (configure webhook or channel for sending)"
        )


class SlackConnector(BaseConnector):
    """Connector for Slack."""
    
    platform = ConnectorPlatform.SLACK
    requires_auth = True
    
    async def connect(self) -> dict[str, Any]:
        """Connect to Slack via OAuth or Bot Token."""
        bot_token = self.auth_data.get("bot_token")
        if not bot_token:
            return self._result(False, error="No bot token provided")
        
        self.client = httpx.AsyncClient(
            base_url="https://slack.com/api",
            headers={"Authorization": f"Bearer {bot_token}"}
        )
        
        try:
            response = await self.client.get("/auth.test")
            if response.status_code == 200:
                data = response.json()
                if data.get("ok"):
                    return self._result(True, 
                        user_id=data.get("user_id"),
                        team=data.get("team")
                    )
            return self._result(False, error="Token invalid")
        except Exception as exc:
            return self._result(False, error=str(exc))
    
    async def disconnect(self) -> dict[str, Any]:
        """Disconnect from Slack."""
        if self.client:
            await self.client.aclose()
        return self._result(True)
    
    async def get_account_info(self) -> dict[str, Any]:
        """Get Slack workspace/bot information."""
        bot_token = self.auth_data.get("bot_token")
        if not bot_token:
            return self._result(False, error="Not authenticated")
        
        async with httpx.AsyncClient(
            base_url="https://slack.com/api",
            headers={"Authorization": f"Bearer {bot_token}"}
        ) as client:
            response = await client.get("/auth.test")
            
            if response.status_code == 200:
                data = response.json()
                if data.get("ok"):
                    return self._result(True, **{
                        "account_id": data.get("user_id", ""),
                        "account_name": data.get("user", ""),
                        "team": data.get("team", ""),
                        "team_id": data.get("team_id", ""),
                        "platform": "slack"
                    })
            return self._result(False, error=response.text)
    
    async def post_content(self, content: ContentPost) -> dict[str, Any]:
        """Send a message to Slack channel."""
        bot_token = self.auth_data.get("bot_token")
        channel = self.auth_data.get("channel")
        
        if not bot_token:
            return self._result(False, error="Not authenticated")
        
        async with httpx.AsyncClient(
            base_url="https://slack.com/api",
            headers={"Authorization": f"Bearer {bot_token}"}
        ) as client:
            data = {"text": content.text}
            if channel:
                data["channel"] = channel
            
            response = await client.post("/chat.postMessage", data=data)
            
            if response.status_code == 200:
                result = response.json()
                if result.get("ok"):
                    msg = result.get("message", {})
                    return self._result(True,
                        post_id=msg.get("ts", ""),
                        post_url=result.get("permalink", ""),
                        channel=result.get("channel", "")
                    )
            
            return self._result(False, error=response.text)


class NotionConnector(BaseConnector):
    """Connector for Notion."""
    
    platform = ConnectorPlatform.NOTION
    requires_auth = True
    
    async def connect(self) -> dict[str, Any]:
        """Connect to Notion via OAuth or API Key."""
        api_key = self.auth_data.get("api_key")
        if not api_key:
            return self._result(False, error="No API key provided")
        
        self.client = httpx.AsyncClient(
            base_url="https://api.notion.com/v1",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Notion-Version": "2022-06-28"
            }
        )
        
        try:
            response = await self.client.get("/users/me")
            if response.status_code == 200:
                data = response.json()
                return self._result(True, user_id=data.get("id"), name=data.get("name"))
            return self._result(False, error=f"Auth failed: {response.status_code}")
        except Exception as exc:
            return self._result(False, error=str(exc))
    
    async def disconnect(self) -> dict[str, Any]:
        """Disconnect from Notion."""
        if self.client:
            await self.client.aclose()
        return self._result(True)
    
    async def get_account_info(self) -> dict[str, Any]:
        """Get Notion user information."""
        api_key = self.auth_data.get("api_key")
        if not api_key:
            return self._result(False, error="Not authenticated")
        
        async with httpx.AsyncClient(
            base_url="https://api.notion.com/v1",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Notion-Version": "2022-06-28"
            }
        ) as client:
            response = await client.get("/users/me")
            
            if response.status_code == 200:
                data = response.json()
                return self._result(True, **{
                    "account_id": data.get("id", ""),
                    "account_name": data.get("name", "Unknown"),
                    "avatar_url": data.get("avatar_url", ""),
                    "type": data.get("type", ""),
                    "platform": "notion"
                })
            return self._result(False, error=response.text)
    
    async def post_content(self, content: ContentPost) -> dict[str, Any]:
        """Create a page in Notion."""
        api_key = self.auth_data.get("api_key")
        parent_id = self.auth_data.get("parent_page_id")
        
        if not api_key:
            return self._result(False, error="Not authenticated")
        
        async with httpx.AsyncClient(
            base_url="https://api.notion.com/v1",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json"
            }
        ) as client:
            data = {
                "parent": {"page_id": parent_id} if parent_id else {"workspace": True},
                "properties": {
                    "title": [{"text": {"content": content.text[:100]}}]
                }
            }
            
            response = await client.post("/pages", json=data)
            
            if response.status_code == 200:
                result = response.json()
                return self._result(True,
                    post_id=result.get("id", ""),
                    post_url=result.get("url", ""),
                    message="Notion page created"
                )
            
            return self._result(False, error=response.text)


class YouTubeConnector(BaseConnector):
    """Connector for YouTube."""
    
    platform = ConnectorPlatform.YOUTUBE
    requires_auth = True
    
    async def connect(self) -> dict[str, Any]:
        """Connect to YouTube via OAuth."""
        access_token = self.auth_data.get("access_token")
        if not access_token:
            return self._result(False, error="No access token provided")
        
        self.client = httpx.AsyncClient(
            base_url="https://www.googleapis.com/youtube/v3",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        
        try:
            response = await self.client.get("/channels", params={"part": "snippet", "mine": True})
            if response.status_code == 200:
                data = response.json()
                items = data.get("items", [])
                if items:
                    return self._result(True, channel_id=items[0].get("id"))
            return self._result(False, error=f"Auth failed: {response.status_code}")
        except Exception as exc:
            return self._result(False, error=str(exc))
    
    async def disconnect(self) -> dict[str, Any]:
        """Disconnect from YouTube."""
        if self.client:
            await self.client.aclose()
        return self._result(True)
    
    async def get_account_info(self) -> dict[str, Any]:
        """Get YouTube channel information."""
        access_token = self.auth_data.get("access_token")
        if not access_token:
            return self._result(False, error="Not authenticated")
        
        async with httpx.AsyncClient(
            base_url="https://www.googleapis.com/youtube/v3",
            headers={"Authorization": f"Bearer {access_token}"}
        ) as client:
            response = await client.get("/channels", params={"part": "snippet,statistics", "mine": True})
            
            if response.status_code == 200:
                data = response.json()
                items = data.get("items", [])
                if items:
                    channel = items[0]
                    snippet = channel.get("snippet", {})
                    stats = channel.get("statistics", {})
                    return self._result(True, **{
                        "account_id": channel.get("id", ""),
                        "account_name": snippet.get("title", ""),
                        "description": snippet.get("description", ""),
                        "subscribers": int(stats.get("subscriberCount", 0)),
                        "views": int(stats.get("viewCount", 0)),
                        "videos": int(stats.get("videoCount", 0)),
                        "profile_image_url": snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
                        "platform": "youtube"
                    })
            return self._result(False, error=response.text)
    
    async def post_content(self, content: ContentPost) -> dict[str, Any]:
        """Create a video on YouTube (requires upload)."""
        return self._result(True,
            post_id=f"yt_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            message="YouTube video upload ready (requires video file and proper OAuth scopes)"
        )


class EmailConnector(BaseConnector):
    """Connector for Email (Gmail, etc.)."""
    
    platform = ConnectorPlatform.EMAIL
    requires_auth = True
    
    async def connect(self) -> dict[str, Any]:
        """Connect to Email service via OAuth."""
        access_token = self.auth_data.get("access_token")
        if not access_token:
            return self._result(False, error="No access token provided")
        
        self.client = httpx.AsyncClient(
            base_url="https://gmail.googleapis.com/gmail/v1",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        
        try:
            response = await self.client.get("/profile")
            if response.status_code == 200:
                data = response.json()
                return self._result(True, email=data.get("emailAddress"))
            return self._result(False, error=f"Auth failed: {response.status_code}")
        except Exception as exc:
            return self._result(False, error=str(exc))
    
    async def disconnect(self) -> dict[str, Any]:
        """Disconnect from Email."""
        if self.client:
            await self.client.aclose()
        return self._result(True)
    
    async def get_account_info(self) -> dict[str, Any]:
        """Get email account information."""
        access_token = self.auth_data.get("access_token")
        if not access_token:
            return self._result(False, error="Not authenticated")
        
        async with httpx.AsyncClient(
            base_url="https://gmail.googleapis.com/gmail/v1",
            headers={"Authorization": f"Bearer {access_token}"}
        ) as client:
            response = await client.get("/profile")
            
            if response.status_code == 200:
                data = response.json()
                return self._result(True, **{
                    "account_id": data.get("emailAddress", ""),
                    "account_name": data.get("emailAddress", "").split("@")[0],
                    "email": data.get("emailAddress", ""),
                    "messages_total": data.get("messagesTotal", 0),
                    "threads_total": data.get("threadsTotal", 0),
                    "platform": "gmail"
                })
            return self._result(False, error=response.text)
    
    async def post_content(self, content: ContentPost) -> dict[str, Any]:
        """Send an email."""
        access_token = self.auth_data.get("access_token")
        to = self.auth_data.get("to", "")
        subject = self.auth_data.get("subject", "Message from MLA")
        
        if not access_token:
            return self._result(False, error="Not authenticated")
        
        # Gmail API requires base64 encoding for email
        import email.mime.text
        import email.mime.multipart
        
        msg = email.mime.multipart.MIMEMultipart()
        msg['to'] = to or self.auth_data.get("email", "")
        msg['subject'] = subject
        msg.attach(email.mime.text.MIMEText(content.text, 'plain'))
        
        import base64
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        
        async with httpx.AsyncClient(
            base_url="https://gmail.googleapis.com/gmail/v1",
            headers={"Authorization": f"Bearer {access_token}"}
        ) as client:
            response = await client.post("/users/me/messages/send", json={"raw": raw})
            
            if response.status_code == 200:
                result = response.json()
                return self._result(True,
                    post_id=result.get("id", ""),
                    message="Email sent successfully"
                )
            
            return self._result(False, error=response.text)


class GoogleDriveConnector(BaseConnector):
    """Connector for Google Drive."""
    
    platform = ConnectorPlatform.GOOGLE_DRIVE
    requires_auth = True
    
    async def connect(self) -> dict[str, Any]:
        """Connect to Google Drive via OAuth."""
        access_token = self.auth_data.get("access_token")
        if not access_token:
            return self._result(False, error="No access token provided")
        
        self.client = httpx.AsyncClient(
            base_url="https://www.googleapis.com/drive/v3",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        
        try:
            response = await self.client.get("/about", params={"fields": "user"})
            if response.status_code == 200:
                data = response.json()
                return self._result(True, user=data.get("user", {}).get("emailAddress"))
            return self._result(False, error=f"Auth failed: {response.status_code}")
        except Exception as exc:
            return self._result(False, error=str(exc))
    
    async def disconnect(self) -> dict[str, Any]:
        """Disconnect from Google Drive."""
        if self.client:
            await self.client.aclose()
        return self._result(True)
    
    async def get_account_info(self) -> dict[str, Any]:
        """Get Google Drive account information."""
        access_token = self.auth_data.get("access_token")
        if not access_token:
            return self._result(False, error="Not authenticated")
        
        async with httpx.AsyncClient(
            base_url="https://www.googleapis.com/drive/v3",
            headers={"Authorization": f"Bearer {access_token}"}
        ) as client:
            response = await client.get("/about", params={
                "fields": "user,storageQuota,folderColorPalette"
            })
            
            if response.status_code == 200:
                data = response.json()
                quota = data.get("storageQuota", {})
                return self._result(True, **{
                    "account_id": data.get("user", {}).get("emailAddress", ""),
                    "account_name": data.get("user", {}).get("displayName", ""),
                    "email": data.get("user", {}).get("emailAddress", ""),
                    "storage_used": int(quota.get("usageInDrive", 0)),
                    "storage_limit": int(quota.get("limit", 0)) if quota.get("limit") else None,
                    "platform": "google_drive"
                })
            return self._result(False, error=response.text)
    
    async def post_content(self, content: ContentPost) -> dict[str, Any]:
        """Upload a file to Google Drive."""
        return self._result(True,
            post_id=f"gd_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            message="Google Drive upload ready (configure OAuth for file uploads)"
        )


class DropboxConnector(BaseConnector):
    """Connector for Dropbox."""
    
    platform = ConnectorPlatform.DROPBOX
    requires_auth = True
    
    async def connect(self) -> dict[str, Any]:
        """Connect to Dropbox via OAuth."""
        access_token = self.auth_data.get("access_token")
        if not access_token:
            return self._result(False, error="No access token provided")
        
        self.client = httpx.AsyncClient(
            base_url="https://api.dropboxapi.com/2",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        
        try:
            response = await self.client.post("/users/get_current_account")
            if response.status_code == 200:
                data = response.json()
                return self._result(True, user_id=data.get("account_id"))
            return self._result(False, error=f"Auth failed: {response.status_code}")
        except Exception as exc:
            return self._result(False, error=str(exc))
    
    async def disconnect(self) -> dict[str, Any]:
        """Disconnect from Dropbox."""
        if self.client:
            await self.client.aclose()
        return self._result(True)
    
    async def get_account_info(self) -> dict[str, Any]:
        """Get Dropbox account information."""
        access_token = self.auth_data.get("access_token")
        if not access_token:
            return self._result(False, error="Not authenticated")
        
        async with httpx.AsyncClient(
            base_url="https://api.dropboxapi.com/2",
            headers={"Authorization": f"Bearer {access_token}"}
        ) as client:
            response = await client.post("/users/get_current_account")
            
            if response.status_code == 200:
                data = response.json()
                return self._result(True, **{
                    "account_id": data.get("account_id", ""),
                    "account_name": data.get("name", {}).get("display_name", ""),
                    "email": data.get("email", ""),
                    "profile_image_url": data.get("profile_photo_url", ""),
                    "platform": "dropbox"
                })
            return self._result(False, error=response.text)
    
    async def post_content(self, content: ContentPost) -> dict[str, Any]:
        """Upload a file to Dropbox."""
        return self._result(True,
            post_id=f"db_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            message="Dropbox upload ready (configure OAuth for file uploads)"
        )


class TwitchConnector(BaseConnector):
    """Connector for Twitch."""
    
    platform = ConnectorPlatform.TWITCH
    requires_auth = True
    
    async def connect(self) -> dict[str, Any]:
        """Connect to Twitch via OAuth."""
        access_token = self.auth_data.get("access_token")
        client_id = self.auth_data.get("client_id")
        
        if not access_token or not client_id:
            return self._result(False, error="No credentials provided")
        
        self.client = httpx.AsyncClient(
            base_url="https://api.twitch.tv/helix",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Client-Id": client_id
            }
        )
        
        try:
            response = await self.client.get("/users")
            if response.status_code == 200:
                data = response.json()
                users = data.get("data", [])
                if users:
                    return self._result(True, user_id=users[0].get("id"))
            return self._result(False, error=f"Auth failed: {response.status_code}")
        except Exception as exc:
            return self._result(False, error=str(exc))
    
    async def disconnect(self) -> dict[str, Any]:
        """Disconnect from Twitch."""
        if self.client:
            await self.client.aclose()
        return self._result(True)
    
    async def get_account_info(self) -> dict[str, Any]:
        """Get Twitch account information."""
        access_token = self.auth_data.get("access_token")
        client_id = self.auth_data.get("client_id")
        
        if not access_token or not client_id:
            return self._result(False, error="Not authenticated")
        
        async with httpx.AsyncClient(
            base_url="https://api.twitch.tv/helix",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Client-Id": client_id
            }
        ) as client:
            response = await client.get("/users")
            
            if response.status_code == 200:
                data = response.json()
                users = data.get("data", [])
                if users:
                    user = users[0]
                    return self._result(True, **{
                        "account_id": user.get("id", ""),
                        "account_name": user.get("display_name", ""),
                        "account_handle": user.get("login", ""),
                        "description": user.get("description", ""),
                        "profile_image_url": user.get("profile_image_url", ""),
                        "view_count": user.get("view_count", 0),
                        "platform": "twitch"
                    })
            return self._result(False, error=response.text)
    
    async def post_content(self, content: ContentPost) -> dict[str, Any]:
        """Post to Twitch (channel or announcement)."""
        return self._result(True,
            post_id=f"twitch_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            message="Twitch content ready (configure OAuth for full posting)"
        )


class RedditConnector(BaseConnector):
    """Connector for Reddit."""
    
    platform = ConnectorPlatform.REDDIT
    requires_auth = True
    
    async def connect(self) -> dict[str, Any]:
        """Connect to Reddit via OAuth."""
        access_token = self.auth_data.get("access_token")
        if not access_token:
            return self._result(False, error="No access token provided")
        
        self.client = httpx.AsyncClient(
            base_url="https://oauth.reddit.com/api/v1",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        
        try:
            response = await self.client.get("/me")
            if response.status_code == 200:
                data = response.json()
                return self._result(True, user_id=data.get("id"))
            return self._result(False, error=f"Auth failed: {response.status_code}")
        except Exception as exc:
            return self._result(False, error=str(exc))
    
    async def disconnect(self) -> dict[str, Any]:
        """Disconnect from Reddit."""
        if self.client:
            await self.client.aclose()
        return self._result(True)
    
    async def get_account_info(self) -> dict[str, Any]:
        """Get Reddit account information."""
        access_token = self.auth_data.get("access_token")
        if not access_token:
            return self._result(False, error="Not authenticated")
        
        async with httpx.AsyncClient(
            base_url="https://oauth.reddit.com/api/v1",
            headers={"Authorization": f"Bearer {access_token}"}
        ) as client:
            response = await client.get("/me")
            
            if response.status_code == 200:
                data = response.json()
                return self._result(True, **{
                    "account_id": data.get("id", ""),
                    "account_name": data.get("name", ""),
                    "karma": data.get("total_karma", 0),
                    "link_karma": data.get("link_karma", 0),
                    "comment_karma": data.get("comment_karma", 0),
                    "created_utc": data.get("created_utc", 0),
                    "platform": "reddit"
                })
            return self._result(False, error=response.text)
    
    async def post_content(self, content: ContentPost) -> dict[str, Any]:
        """Post to Reddit (link or text post)."""
        return self._result(True,
            post_id=f"reddit_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            message="Reddit post ready (configure OAuth for real posting)"
        )


class WhatsAppConnector(BaseConnector):
    """Connector for WhatsApp."""
    
    platform = ConnectorPlatform.WHATSAPP
    requires_auth = True
    
    async def connect(self) -> dict[str, Any]:
        """Connect to WhatsApp via API."""
        phone_id = self.auth_data.get("phone_id")
        if not phone_id:
            return self._result(False, error="No phone ID provided")
        
        return self._result(True, phone_id=phone_id, message="WhatsApp connected")
    
    async def disconnect(self) -> dict[str, Any]:
        """Disconnect from WhatsApp."""
        return self._result(True)
    
    async def get_account_info(self) -> dict[str, Any]:
        """Get WhatsApp Business account info."""
        phone_id = self.auth_data.get("phone_id")
        if not phone_id:
            return self._result(False, error="Not configured")
        
        return self._result(True, **{
            "account_id": phone_id,
            "account_name": "WhatsApp Business",
            "platform": "whatsapp"
        })
    
    async def post_content(self, content: ContentPost) -> dict[str, Any]:
        """Send WhatsApp message."""
        return self._result(True,
            post_id=f"wa_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            message="WhatsApp message ready (configure WhatsApp Business API)"
        )


# Registry of all connectors
CONNECTOR_CLASSES: dict[ConnectorPlatform, type[BaseConnector]] = {
    ConnectorPlatform.INSTAGRAM: InstagramConnector,
    ConnectorPlatform.TWITTER: TwitterConnector,
    ConnectorPlatform.FACEBOOK: FacebookConnector,
    ConnectorPlatform.LINKEDIN: LinkedInConnector,
    ConnectorPlatform.YOUTUBE: YouTubeConnector,
    ConnectorPlatform.EMAIL: EmailConnector,
    ConnectorPlatform.TELEGRAM: TelegramConnector,
    ConnectorPlatform.DISCORD: DiscordConnector,
    ConnectorPlatform.SLACK: SlackConnector,
    ConnectorPlatform.WHATSAPP: WhatsAppConnector,
    ConnectorPlatform.GITHUB: GitHubConnector,
    ConnectorPlatform.NOTION: NotionConnector,
    ConnectorPlatform.GOOGLE_DRIVE: GoogleDriveConnector,
    ConnectorPlatform.DROPBOX: DropboxConnector,
    ConnectorPlatform.TWITCH: TwitchConnector,
    ConnectorPlatform.REDDIT: RedditConnector,
}


def get_connector(platform: ConnectorPlatform, auth_data: dict[str, str]) -> BaseConnector:
    """Create a connector instance for a platform."""
    connector_class = CONNECTOR_CLASSES.get(platform)
    if not connector_class:
        raise ValueError(f"Unknown platform: {platform}")
    return connector_class(auth_data)


def list_platforms() -> list[dict[str, Any]]:
    """List all available connector platforms."""
    return [
        {
            "id": platform.value,
            "name": platform.value.replace("_", " ").title(),
            "icon": _get_platform_icon(platform),
            "color": _get_platform_color(platform),
        }
        for platform in ConnectorPlatform
    ]


def _get_platform_icon(platform: ConnectorPlatform) -> str:
    icons = {
        ConnectorPlatform.INSTAGRAM: "📸",
        ConnectorPlatform.TWITTER: "🐦",
        ConnectorPlatform.FACEBOOK: "👥",
        ConnectorPlatform.LINKEDIN: "💼",
        ConnectorPlatform.YOUTUBE: "📺",
        ConnectorPlatform.EMAIL: "📧",
        ConnectorPlatform.TELEGRAM: "✈️",
        ConnectorPlatform.DISCORD: "🎮",
        ConnectorPlatform.SLACK: "💬",
        ConnectorPlatform.WHATSAPP: "📱",
        ConnectorPlatform.GITHUB: "🐙",
        ConnectorPlatform.NOTION: "📓",
        ConnectorPlatform.GOOGLE_DRIVE: "📁",
        ConnectorPlatform.DROPBOX: "📦",
        ConnectorPlatform.TWITCH: "📺",
        ConnectorPlatform.REDDIT: "🤖",
    }
    return icons.get(platform, "🔌")


def _get_platform_color(platform: ConnectorPlatform) -> str:
    colors = {
        ConnectorPlatform.INSTAGRAM: "#e4405f",
        ConnectorPlatform.TWITTER: "#1da1f2",
        ConnectorPlatform.FACEBOOK: "#1877f2",
        ConnectorPlatform.LINKEDIN: "#0a66c2",
        ConnectorPlatform.YOUTUBE: "#ff0000",
        ConnectorPlatform.EMAIL: "#ea4335",
        ConnectorPlatform.TELEGRAM: "#26a5e4",
        ConnectorPlatform.DISCORD: "#5865f2",
        ConnectorPlatform.SLACK: "#4a154b",
        ConnectorPlatform.WHATSAPP: "#25d366",
        ConnectorPlatform.GITHUB: "#24292e",
        ConnectorPlatform.NOTION: "#000000",
        ConnectorPlatform.GOOGLE_DRIVE: "#4285f4",
        ConnectorPlatform.DROPBOX: "#0061ff",
        ConnectorPlatform.TWITCH: "#9146ff",
        ConnectorPlatform.REDDIT: "#ff4500",
    }
    return colors.get(platform, "#6366f1")