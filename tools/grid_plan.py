#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""grid_plan.py —— 生成每只 ETF 的网格档位表（读 data/grid_data.json，不联网）"""
import json
import math
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from paths import data  # noqa: E402  路径唯一真源（ADR-001）


# gap: 网格间距；on: 是否纳入网格；reason: 不做的原因
SPEC = {
    "512170": {"gap": 0.040, "on": True,  "note": "年波25%、4年最大回撤−52%，振幅最大，间距给到4%"},
    "515180": {"gap": 0.030, "on": True,  "note": "年波15%，区间宽（1.17→1.62），最适合低吸"},
    "515450": {"gap": 0.030, "on": True,  "note": "年波15%，但已在73%高位，下方空间大于上方"},
    "563800": {"on": False, "reason": "市值 9,202 元，30% 网格仓只有 2,760 元，摊到 12 格每格 230 元，佣金吃掉收益"},
    "159928": {"on": False, "reason": "市值 1,977 元，仓位太小，单格不足 100 份"},
    "513500": {"on": False, "reason": "处在 4 年 91% 分位，上方只剩 5% 空间；高位建网格等于高位接货"},
    "513700": {"on": False, "reason": "现价 0.626 距 +30% 目标 0.657 只剩 5%，网格仓几乎没有可卖格"},
}

MIN_CELL = 300.0   # 每格最小金额（元），低于此佣金占比过高


def main():
    rows = json.load(open(data("grid_data.json"), encoding="utf-8"))
    out = []
    for r in rows:
        sp = SPEC.get(r["code"], {})
        if not sp.get("on"):
            out.append({**r, "on": False, "reason": sp.get("reason", "未纳入")})
            continue
        lo = r["stop"]
        hi = min(r["target"], r["hi4y"] * 1.03)
        g = sp["gap"]
        n = max(2, round(math.log(hi / lo) / math.log(1 + g)))
        hi = lo * (1 + g) ** n
        cap = r["shares"] * 0.30
        per = int(round(cap / n / 100.0)) * 100
        if per < 100 or per * r["now"] < MIN_CELL:
            out.append({**r, "on": False,
                        "reason": f"单格仅 {per} 份（约 {per*r['now']:.0f} 元），低于 {MIN_CELL:.0f} 元门槛"})
            continue
        levels = [round(lo * (1 + g) ** i, 3) for i in range(n + 1)]
        idx = math.log(r["now"] / lo) / math.log(1 + g)
        idx = max(0.0, min(float(n), idx))
        held = n - idx
        below = [v for v in levels if v < r["now"] - 1e-9]
        above = [v for v in levels if v > r["now"] + 1e-9]
        out.append({**r, "on": True, "gap": g, "n": n, "lo": lo, "hi": hi,
                    "per": per, "cell": per * r["now"], "cap": per * n,
                    "cap_pct": per * n / r["shares"] * 100,
                    "levels": levels, "idx": idx, "held_grids": held,
                    "held_shares": round(held * per, -2),
                    "buy_next": below[-1] if below else None,
                    "sell_next": above[0] if above else None,
                    "note": sp["note"]})

    print("【网格档位】下沿=止损价，上沿=min(目标价, 4年高×1.03)\n")
    for o in out:
        if not o["on"]:
            print(f"✗ {o['name']} {o['code']} —— 不做：{o['reason']}\n")
            continue
        print(f"✓ {o['name']}  {o['code']}   现价 {o['now']:.3f}  成本 {o['cost']:.3f}")
        print(f"   区间 {o['lo']:.3f} ~ {o['hi']:.3f}  间距 {o['gap']*100:.1f}%  {o['n']} 格  "
              f"每格 {o['per']} 份 / {o['cell']:.0f} 元")
        print(f"   网格仓 {o['cap']} 份 = 底仓 {o['cap_pct']:.0f}%")
        print(f"   现价档位 {o['idx']:.1f}/{o['n']} → 应持 {o['held_grids']:.1f} 格 ≈ {o['held_shares']:.0f} 份")
        print(f"   下一买 {o['buy_next']}  下一卖 {o['sell_next']}")
        print("   " + "  ".join(f"{i}:{v:.3f}" for i, v in enumerate(o["levels"])))
        print()
    json.dump(out, open(data("grid_plan.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("已写出 grid_plan.json")


if __name__ == "__main__":
    main()
