"""Sub2API 兼容性测试：/v1/responses 必须返回干净的 404（探测落标的关键）。

依据 Sub2API 源码（openai_apikey_responses_probe.go）：
- 404/405 → 落标"不支持 Responses"→ 走 chat/completions 直转
- 404 但错误体带 model_not_found 类错误码/文案 → 判为"不下结论"（保持默认走 Responses）
所以 Adapter 的 404 绝不能包含模型相关错误码。
"""
from conftest import AUTH


async def test_responses_endpoint_clean_404(client):
    resp = await client.post("/v1/responses", headers=AUTH, json={"model": "m365-copilot-chat", "input": []})
    assert resp.status_code in (404, 405)
    body = resp.text.lower()
    for bad in ("model_not_found", "model_not_available", "unsupported_model", "invalid_model"):
        assert bad not in body
