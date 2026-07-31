"""舆情/事件监控员测试：关键词扫描与重大事件标注。"""
from __future__ import annotations

from agents.event_monitor import run as event_run


def test_no_material_event_when_clean(ctx):
    # MockAshareProvider 默认返回「无重大负面」
    upd = event_run({}, ctx)
    assert upd["market_events"] == []


def test_material_event_detected(ctx, monkeypatch):
    def fake_news(self, ticker):
        return [f"{ticker}：发布半年报，业绩超预期，拟并购重组"]
    monkeypatch.setattr(type(ctx.data), "get_news", fake_news)
    upd = event_run({}, ctx)
    evs = upd["market_events"]
    assert len(evs) >= 1
    assert all(e.material for e in evs)
    assert any("并购" in e.headline or "超预期" in e.headline for e in evs)


def test_negative_keyword_material(ctx, monkeypatch):
    def fake_news(self, ticker):
        return [f"{ticker}：遭立案调查，存退市风险"]
    monkeypatch.setattr(type(ctx.data), "get_news", fake_news)
    upd = event_run({}, ctx)
    evs = upd["market_events"]
    assert evs and evs[0].material
    assert "负面" in evs[0].detail


def test_event_monitor_disabled(ctx):
    ctx.settings.event_monitor.enabled = False
    upd = event_run({}, ctx)
    assert upd["market_events"] == []
