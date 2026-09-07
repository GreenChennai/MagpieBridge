"""OneBot 分发器：HTTP/WS 共用语义；失败绝不谎报成功。"""

import pytest

from magpie.onebot.dispatcher import ActionError, OneBotDispatcher


class FakeEngine:
    def __init__(self, ok: bool = True):
        self.ok = ok
        self.calls: list[tuple] = []

    async def send_text(self, contact: str, text: str) -> bool:
        self.calls.append((contact, text))
        return self.ok

    def get_status(self) -> dict:
        return {"initialized": True}


class FakePM:
    def __init__(self):
        self.actions = {"hello": True}
        self.ran = []

    def has_action(self, action: str) -> bool:
        return action in self.actions

    async def run_action(self, action: str, **params):
        self.ran.append((action, params))
        return {"pong": action}


@pytest.mark.asyncio
async def test_send_group_msg_success():
    d = OneBotDispatcher(FakeEngine(ok=True))
    result = await d.dispatch("send_group_msg", {"group_id": 123, "message": "嗨"})
    assert "message_id" in result


@pytest.mark.asyncio
async def test_send_failure_raises_not_lies():
    d = OneBotDispatcher(FakeEngine(ok=False))
    with pytest.raises(ActionError):
        await d.dispatch("send_group_msg", {"group_id": 123, "message": "嗨"})


@pytest.mark.asyncio
async def test_message_segments_flattened():
    eng = FakeEngine(ok=True)
    d = OneBotDispatcher(eng)
    await d.dispatch(
        "send_private_msg",
        {"user_id": 1, "message": [{"type": "text", "data": {"text": "a"}}, {"type": "at", "data": {"qq": "2"}}]},
    )
    assert eng.calls[0][1] == "a"


@pytest.mark.asyncio
async def test_unknown_action_code_100():
    d = OneBotDispatcher(FakeEngine())
    with pytest.raises(ActionError) as ei:
        await d.dispatch("no_such_action", {})
    assert ei.value.code == 100


@pytest.mark.asyncio
async def test_plugin_action_routing():
    d = OneBotDispatcher(FakeEngine())
    pm = FakePM()
    d.set_plugin_manager(pm)
    result = await d.dispatch("hello", {"x": 1})
    assert result == {"pong": "hello"}
    assert pm.ran[0][1] == {"x": 1}
