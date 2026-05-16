from __future__ import annotations

import logging
from unittest.mock import Mock

import requests

from scripts.pushers.dingtalk_pusher import DingTalkPusher


def test_sign_against_official_spec():
    timestamp = "1577000000000"
    secret = "MOCKSECRET"
    expected = "WNpMNBy0F4TNVqjpK5AtIry5M26yHjM4uaOJ0rXUvcw%3D"

    assert DingTalkPusher._sign(timestamp, secret) == expected


def test_send_markdown_builds_correct_payload(monkeypatch):
    mock_response = Mock(status_code=200, json=lambda: {"errcode": 0})
    mock_post = Mock(return_value=mock_response)
    monkeypatch.setattr(requests, "post", mock_post)
    monkeypatch.setenv("DINGTALK_WEBHOOK_TOKEN", "test_token")
    monkeypatch.setenv("DINGTALK_WEBHOOK_SECRET", "MOCKSECRET")
    pusher = DingTalkPusher()

    pusher.initialize()
    ok = pusher.send_markdown(title="测试", content="正文")

    assert ok is True
    mock_post.assert_called_once()
    url = mock_post.call_args.args[0]
    assert "timestamp=" in url
    assert "sign=" in url
    assert mock_post.call_args.kwargs["json"] == {
        "msgtype": "markdown",
        "markdown": {"title": "测试", "text": "正文"},
    }


def test_handles_connection_error_and_dingtalk_errcode(monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger="scripts.pushers.dingtalk_pusher")
    monkeypatch.setenv("DINGTALK_WEBHOOK_TOKEN", "test_token")
    monkeypatch.setenv("DINGTALK_WEBHOOK_SECRET", "MOCKSECRET")
    pusher = DingTalkPusher()
    pusher.initialize()

    mock_post = Mock(side_effect=requests.ConnectionError("network down"))
    monkeypatch.setattr(requests, "post", mock_post)
    assert pusher.send_markdown(title="测试", content="正文") is False
    assert any("DingTalk 发送异常" in record.message for record in caplog.records)

    caplog.clear()
    mock_response = Mock(
        status_code=200,
        json=lambda: {"errcode": 300001, "errmsg": "token invalid"},
    )
    monkeypatch.setattr(requests, "post", Mock(return_value=mock_response))
    assert pusher.send_markdown(title="测试", content="正文") is False
    assert any("DingTalk 发送失败" in record.message for record in caplog.records)


def test_exception_log_redacts_credentials(monkeypatch, caplog):
    """Codex review 严重 1：异常日志不应泄漏 access_token / sign。
    requests 异常 message 通常含完整 URL，必须脱敏。
    """
    caplog.set_level(logging.ERROR, logger="scripts.pushers.dingtalk_pusher")
    monkeypatch.setenv("DINGTALK_WEBHOOK_TOKEN", "REAL_SECRET_TOKEN_abc123")
    monkeypatch.setenv("DINGTALK_WEBHOOK_SECRET", "MOCKSECRET")
    pusher = DingTalkPusher()
    pusher.initialize()

    # 模拟 requests 抛出含完整 URL 的异常（真实 ConnectionError 常如此）
    full_url_with_creds = (
        "HTTPSConnectionPool(host='oapi.dingtalk.com', port=443): Max retries exceeded "
        "with url: /robot/send?access_token=REAL_SECRET_TOKEN_abc123"
        "&timestamp=1577000000000&sign=somesecret%3D"
    )
    monkeypatch.setattr(
        requests, "post",
        Mock(side_effect=requests.ConnectionError(full_url_with_creds)),
    )

    assert pusher.send_markdown(title="t", content="c") is False

    # 关键断言：日志中不得包含真实 token 值与 sign 值（脱敏后可以保留 `access_token=<redacted>`）
    all_log_text = "\n".join(r.message for r in caplog.records)
    assert "REAL_SECRET_TOKEN_abc123" not in all_log_text, \
        f"token 值泄漏到日志：{all_log_text}"
    assert "somesecret%3D" not in all_log_text, \
        f"sign 值泄漏到日志：{all_log_text}"
    # 但应该仍有一条 error 日志记录失败类型（便于排障）
    assert any("ConnectionError" in r.message or "DingTalk" in r.message
               for r in caplog.records)
