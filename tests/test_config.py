"""配置加载健壮性：未知键不炸、保存回读一致、授权码打码。"""

import json
from pathlib import Path

from magpie.core.config import AppConfig, AlertConfig


def test_unknown_key_does_not_nuke_config(tmp_path: Path):
    """旧版行为：一个未知键让整份配置静默回落默认值。"""
    p = tmp_path / "config.json"
    p.write_text(
        json.dumps(
            {
                "wechat": {"window_width": 1200, "typo_key": 1},
                "monitor": {"enabled": True},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cfg = AppConfig.load(p)
    assert cfg.wechat.window_width == 1200  # 已知键仍生效
    assert cfg.monitor.enabled is True
    assert cfg.web.port == 8080  # 其余段落不受影响


def test_save_load_roundtrip(tmp_path: Path):
    p = tmp_path / "config.json"
    cfg = AppConfig.load(p)
    cfg.wechat.window_height = 777
    cfg.alert.enabled = True
    cfg.save(p)
    cfg2 = AppConfig.load(p)
    assert cfg2.wechat.window_height == 777
    assert cfg2.alert.enabled is True


def test_auth_code_masking():
    a = AlertConfig(auth_code="abcdefgh")
    assert a.masked_auth_code() == "****efgh"
    assert "abcdefgh" not in a.masked_auth_code()
    assert AlertConfig(auth_code="").masked_auth_code() == ""
