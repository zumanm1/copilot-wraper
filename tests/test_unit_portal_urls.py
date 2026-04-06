"""Tests for portal_urls.normalize_copilot_portal_url (shared C1/C3)."""
from __future__ import annotations

from portal_urls import m365_hub_default_landing, normalize_copilot_portal_url


def test_normalize_m365_cloud_microsoft_com_to_canonical():
    assert "m365.cloud.microsoft.com" not in normalize_copilot_portal_url(
        "https://m365.cloud.microsoft.com/chat/?auth=1"
    )
    assert normalize_copilot_portal_url("https://m365.cloud.microsoft.com/chat").startswith(
        "https://m365.cloud.microsoft/"
    )


def test_normalize_www_m365():
    u = normalize_copilot_portal_url("https://www.m365.cloud.microsoft/foo")
    assert u.startswith("https://m365.cloud.microsoft/")


def test_normalize_copilot_www():
    u = normalize_copilot_portal_url("https://www.copilot.microsoft.com/")
    assert u.startswith("https://copilot.microsoft.com/")


def test_m365_default_landing_is_chat_path():
    assert "/chat/" in m365_hub_default_landing()
    assert m365_hub_default_landing().startswith("https://m365.cloud.microsoft")


def test_empty_unchanged():
    assert normalize_copilot_portal_url("") == ""
