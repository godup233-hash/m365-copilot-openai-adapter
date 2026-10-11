"""测试 7：缺少权限/登录失败正确报告（不伪造正常回复）。"""
import httpx
from conftest import AUTH, GraphScript, build_test_app, chat_body, failing_token


async def test_auth_required_when_token_unavailable(settings, graph):
    app = build_test_app(settings, graph, token_provider=failing_token)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/v1/chat/completions", headers=AUTH, json=chat_body())
    assert resp.status_code == 401
    err = resp.json()["error"]
    assert err["type"] == "authentication_error"
    assert err["code"] == "auth_required"
    assert "login" in err["message"]


async def test_upstream_403_passthrough(client, graph):
    """无 Copilot 许可证等 403：状态码与真实 message 透传（Sub2API 只透传 400 系）。"""
    graph.chat_responses.append(
        httpx.Response(403, json={"error": {"code": "Forbidden", "message": "user is not licensed for Microsoft 365 Copilot"}})
    )
    resp = await client.post("/v1/chat/completions", headers=AUTH, json=chat_body())
    assert resp.status_code == 403
    assert "licensed" in resp.json()["error"]["message"]


async def test_session_expired_reports_error(client, graph):
    """上游 404（会话不存在）+ recovery=off → 明确报 session_expired。"""
    graph.chat_responses.append(
        httpx.Response(404, json={"error": {"code": "NotFound", "message": "conversation not found"}})
    )
    resp = await client.post("/v1/chat/completions", headers=AUTH, json=chat_body())
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "session_expired"
