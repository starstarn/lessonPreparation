"""FastAPI 基础接口测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from lesson_prep.api import app

    with TestClient(app) as c:
        yield c


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    assert "mock_llm" in data


def test_agent_plugins(client):
    resp = client.get("/api/agent-plugins")
    assert resp.status_code == 200
    plugins = resp.json()["plugins"]
    ids = {p["id"] for p in plugins}
    assert "curriculum" in ids
    assert "consistency" in ids
    by_id = {p["id"]: p for p in plugins}
    assert "shared.read_anchors" in by_id["exercises"].get("skills", [])
    assert "consistency.anchors" in by_id["consistency"].get("skills", [])


def test_agent_skills(client):
    resp = client.get("/api/agent-skills")
    assert resp.status_code == 200
    skills = resp.json()["skills"]
    ids = {s["id"] for s in skills}
    assert "shared.read_anchors" in ids
    assert "consistency.anchors" in ids


def test_agent_profiles(client):
    resp = client.get("/api/agent-profiles")
    assert resp.status_code == 200
    profiles = resp.json()["profiles"]
    ids = {p["id"] for p in profiles}
    assert "full" in ids
    assert "plan_only" in ids


def test_catalog(client):
    resp = client.get("/api/catalog")
    assert resp.status_code == 200
    data = resp.json()
    assert "stages" in data
