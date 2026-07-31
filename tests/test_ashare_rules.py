from data.ashare_rules import board_of, limit_pct, is_limit_up, is_limit_down, can_buy, can_sell


def test_board_of():
    assert board_of("688001") == "STAR"
    assert board_of("300750") == "CHINEXT"
    assert board_of("600519") == "MAIN"
    assert board_of("000001") == "MAIN"


def test_limit_pct():
    assert limit_pct("600519") == 0.10
    assert limit_pct("300750") == 0.20
    assert limit_pct("688001") == 0.20


def test_limit_up_blocks_buy():
    # 涨停：price=prev*1.1
    ok, reason = can_buy("600519", "茅台", 110.0, 100.0)
    assert not ok and "涨停" in reason


def test_limit_down_blocks_sell():
    ok, reason = can_sell("600519", "茅台", 90.0, 100.0, bought_today=False)
    assert not ok and "跌停" in reason


def test_t1_blocks_sell_today_buy():
    ok, reason = can_sell("600519", "茅台", 100.0, 100.0, bought_today=True)
    assert not ok and "T+1" in reason


def test_st_forbidden():
    ok, _ = can_buy("600519", "*ST 某某", 100.0, 100.0)
    assert not ok
