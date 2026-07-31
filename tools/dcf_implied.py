#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
穿透叙事 DCF 隐含天花板反算工具

替代原 pe_lookup.py 的查表法。新方法更贴近现实：
  - 前3年（t=1,2,3）：直接使用分析师一致预期业绩 E1, E2, E3
  - 第4-8年（t=4..8）：从 E3 起匀速（等比）增长5年到达终局利润 L
  - 第9年起（t=9..∞）：L 永续稳定，不给予永续增长率（g=0）
  - 折现率三档：r = 8%、10%、12%
  - 根据当前市值，反算股价隐含的终局利润 L

DCF 公式（净利润 ≈ 权益现金流，忽略营运资本变动）：
    市值 = Σ_{t=1}^{3} E_t / (1+r)^t
         + Σ_{t=4}^{8} E_3·(1+g)^(t-3) / (1+r)^t        其中 g = (L/E3)^(1/5) - 1
         + (L / r) / (1+r)^8                              永续期现值

当 L < E3 时（隐含业绩下滑），g < 0，公式仍成立（匀速衰减至 L）。

用法:
    # 核心：反算隐含终局利润（三档折现率同时输出）
    python dcf_implied.py implied --cap 1000 --e1 10 --e2 11 --e3 12

    # 正算：给定终局利润，算合理市值与动态PE
    python dcf_implied.py calc --l 50 --e1 10 --e2 11 --e3 12 --r 10

    # 交互式：仅给市值和三年业绩，输出完整分析表
    python dcf_implied.py analyze --cap 1000 --e1 10 --e2 11 --e3 12

    # 查看不同终局利润对应的市值（敏感性分析）
    python dcf_implied.py sensitivity --e1 10 --e2 11 --e3 12 --r 10
"""
import argparse

# 三档折现率
R_GRID = [8, 10, 12]
GROWTH_YEARS = 5  # 第4-8年匀速增长年数


def fair_value(L: float, e1: float, e2: float, e3: float, r_pct: float) -> float:
    """正算：给定终局利润 L、前3年业绩、折现率 r(%)，返回合理市值。

    假设净利润 ≈ 权益现金流。第4-8年从 E3 等比增长至 L，之后永续稳定。
    """
    if r_pct <= 0:
        raise ValueError("折现率必须为正")
    r = r_pct / 100.0
    pv = 0.0
    # 前3年
    pv += e1 / (1 + r) ** 1
    pv += e2 / (1 + r) ** 2
    pv += e3 / (1 + r) ** 3
    # 第4-8年：从E3等比增长至L
    if e3 > 0 and L > 0 and abs(L - e3) > 1e-9:
        g = (L / e3) ** (1.0 / GROWTH_YEARS) - 1.0
        for t in range(4, 4 + GROWTH_YEARS):
            profit_t = e3 * (1 + g) ** (t - 3)
            pv += profit_t / (1 + r) ** t
    elif e3 > 0 and L > 0:  # L == E3，g=0
        for t in range(4, 4 + GROWTH_YEARS):
            pv += e3 / (1 + r) ** t
    # 永续期（第9年起，L稳定不变）
    pv += (L / r) / (1 + r) ** (3 + GROWTH_YEARS)
    return pv


def implied_ceiling(cap: float, e1: float, e2: float, e3: float, r_pct: float) -> float:
    """反算：给定市值、前3年业绩、折现率 r(%)，返回隐含终局利润 L。

    用二分法求解。L 的范围 [0, 上界]，上界取一个足够大的值。
    """
    r = r_pct / 100.0
    # 上界估计：仅永续期就支撑市值时 L = cap * r * (1+r)^8，放大10倍确保覆盖
    hi = max(cap * r * (1 + r) ** (3 + GROWTH_YEARS) * 10, e3 * 100, 1000.0)
    lo = 0.0
    for _ in range(300):
        mid = (lo + hi) / 2.0
        if fair_value(mid, e1, e2, e3, r_pct) < cap:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def implied_growth_rate(L: float, e3: float) -> float:
    """计算第4-8年的隐含复合增速 g(%)。"""
    if e3 <= 0 or L <= 0:
        return float('nan')
    return ((L / e3) ** (1.0 / GROWTH_YEARS) - 1.0) * 100.0


def fmt(x, suffix="", width=10, prec=1):
    if x is None or (isinstance(x, float) and x != x):  # NaN
        return f"{'N/A':>{width}}"
    return f"{x:> {width}.{prec}f}{suffix}"


def cmd_implied(args):
    """反算隐含终局利润（核心功能，三档折现率同时输出）。"""
    print("=" * 80)
    print("DCF 反算：股价隐含的终局利润预期")
    print("=" * 80)
    print(f"当前市值: {args.cap:.1f} 亿")
    print(f"未来3年一致预期: E1={args.e1}, E2={args.e2}, E3={args.e3} 亿")
    print(f"假设: 第4-8年从E3匀速增长至终局L，之后永续稳定(g=0)")
    print("-" * 80)
    print(f"{'折现率r':>10} | {'隐含终局L':>12} | {'L/E3倍数':>10} | {'隐含增速g':>10} | {'动态PE(E1)':>12}")
    print("-" * 80)
    for r in R_GRID:
        L = implied_ceiling(args.cap, args.e1, args.e2, args.e3, r)
        ratio = L / args.e3 if args.e3 > 0 else float('nan')
        g = implied_growth_rate(L, args.e3)
        pe1 = args.cap / args.e1 if args.e1 > 0 else float('nan')
        print(f"{r:>8}%  | {L:>10.1f} 亿 | {ratio:>8.2f}x | {g:>8.1f}% | {pe1:>10.1f}x")
    print("-" * 80)
    print("解读要点:")
    print("  1. 隐含终局L vs 产业空间测算的终局利润 → 判断高估/低估")
    print("  2. L/E3倍数 > 1 表示市场预期增长，< 1 表示预期下滑")
    print("  3. 三档折现率给出区间，r=8%偏乐观(低协方差资产)，r=12%偏谨慎(高风险)")
    print("  4. 与公司历史业绩增速、行业增速对照，判断隐含预期是否合理")


def cmd_calc(args):
    """正算：给定终局利润L，算合理市值与动态PE。"""
    fv = fair_value(args.l, args.e1, args.e2, args.e3, args.r)
    g = implied_growth_rate(args.l, args.e3)
    pe1 = fv / args.e1 if args.e1 > 0 else float('nan')
    print(f"输入: 终局L={args.l}亿, E1={args.e1}, E2={args.e2}, E3={args.e3}, r={args.r}%")
    print(f"  → 合理市值 = {fv:.1f} 亿")
    print(f"  → 对应动态PE(E1) = {pe1:.1f}x")
    print(f"  → 第4-8年隐含复合增速 g = {g:.1f}%/年")


def cmd_analyze(args):
    """完整分析：反算+解读。"""
    cmd_implied(args)
    print()
    print("=" * 80)
    print("进一步分析指引")
    print("=" * 80)
    for r in R_GRID:
        L = implied_ceiling(args.cap, args.e1, args.e2, args.e3, r)
        ratio = L / args.e3 if args.e3 > 0 else 0
        print(f"  [r={r}%] 隐含终局L={L:.1f}亿 (E3的{ratio:.2f}倍)")
        if ratio < 0.5:
            print(f"         ⚠ L<E3×0.5，市场预期业绩深度下滑，若产业空间支撑则可能低估")
        elif ratio < 1:
            print(f"         ℹ L<E3，市场预期业绩下滑，需判断下滑幅度是否过度")
        elif ratio < 2:
            print(f"         ℹ L≈E3~2×E3，市场预期温和增长")
        elif ratio < 5:
            print(f"         ℹ L=2~5×E3，市场预期较高增长，需验证天花板可达性")
        elif ratio < 10:
            print(f"         ⚠ L=5~10×E3，市场预期高增长，叙事已较饱满")
        else:
            print(f"         ⚠ L>10×E3，市场预期极高增长，叙事打满，透支风险大")


def cmd_sensitivity(args):
    """敏感性分析：不同终局利润对应的市值。"""
    print(f"敏感性分析：不同终局利润L对应的合理市值 (r={args.r}%)")
    print(f"输入: E1={args.e1}, E2={args.e2}, E3={args.e3}")
    print("-" * 50)
    print(f"{'终局L(亿)':>10} | {'L/E3':>8} | {'市值(亿)':>10} | {'动态PE':>8}")
    print("-" * 50)
    ratios = [0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 10.0, 15.0, 20.0]
    for ratio in ratios:
        L = args.e3 * ratio
        fv = fair_value(L, args.e1, args.e2, args.e3, args.r)
        pe1 = fv / args.e1 if args.e1 > 0 else 0
        print(f"{L:>10.1f} | {ratio:>6.1f}x | {fv:>10.1f} | {pe1:>6.1f}x")


def main():
    parser = argparse.ArgumentParser(
        description="穿透叙事 DCF 隐含天花板反算工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    # implied: 反算隐含终局利润
    p1 = sub.add_parser("implied", help="反算：由市值+前3年业绩 → 隐含终局利润L（三档r）")
    p1.add_argument("--cap", type=float, required=True, help="当前市值（亿元）")
    p1.add_argument("--e1", type=float, required=True, help="第1年一致预期净利润（亿元）")
    p1.add_argument("--e2", type=float, required=True, help="第2年一致预期净利润（亿元）")
    p1.add_argument("--e3", type=float, required=True, help="第3年一致预期净利润（亿元）")
    p1.set_defaults(func=cmd_implied)

    # calc: 正算
    p2 = sub.add_parser("calc", help="正算：由终局L+前3年业绩+r → 合理市值")
    p2.add_argument("--l", type=float, required=True, help="终局利润L（亿元）")
    p2.add_argument("--e1", type=float, required=True, help="第1年一致预期净利润")
    p2.add_argument("--e2", type=float, required=True, help="第2年一致预期净利润")
    p2.add_argument("--e3", type=float, required=True, help="第3年一致预期净利润")
    p2.add_argument("--r", type=float, required=True, help="折现率(%)，如10表示10%")
    p2.set_defaults(func=cmd_calc)

    # analyze: 完整分析
    p3 = sub.add_parser("analyze", help="完整分析：反算+解读")
    p3.add_argument("--cap", type=float, required=True, help="当前市值（亿元）")
    p3.add_argument("--e1", type=float, required=True, help="第1年一致预期净利润")
    p3.add_argument("--e2", type=float, required=True, help="第2年一致预期净利润")
    p3.add_argument("--e3", type=float, required=True, help="第3年一致预期净利润")
    p3.set_defaults(func=cmd_analyze)

    # sensitivity: 敏感性分析
    p4 = sub.add_parser("sensitivity", help="不同终局利润对应的市值")
    p4.add_argument("--e1", type=float, required=True, help="第1年一致预期净利润")
    p4.add_argument("--e2", type=float, required=True, help="第2年一致预期净利润")
    p4.add_argument("--e3", type=float, required=True, help="第3年一致预期净利润")
    p4.add_argument("--r", type=float, default=10, help="折现率(%)，默认10%")
    p4.set_defaults(func=cmd_sensitivity)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
