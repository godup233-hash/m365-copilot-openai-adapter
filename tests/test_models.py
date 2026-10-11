"""测试 1：模型列表格式正确；测试 10 的一部分：未授权访问被拒。"""
from conftest import AUTH


async def test_models_ok(client):
    resp = await client.get("/v1/models", headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    assert len(data["data"]) == 1
    model = data["data"][0]
    assert model["id"] == "m365-copilot-chat"
    assert model["object"] == "model"


async def test_models_requires_auth(client):
    resp = await client.get("/v1/models")
    assert resp.status_code == 401
    assert resp.json()["error"]["type"] == "authentication_error"


async def test_models_wrong_key(client):
    resp = await client.get("/v1/models", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


async def test_healthz_no_auth(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
