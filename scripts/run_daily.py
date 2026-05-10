#!/usr/bin/env python3
"""v5-Top3竞价选股 — 每日运行脚本
用法: python run_daily.py --date 20260508
      python run_daily.py  # 默认取最近交易日
"""
import argparse, sys, time
import tushare as ts
import pandas as pd

TUSHARE_TOKEN = None
for key in ["TUSHARE_TOKEN"]:
    import os
    TUSHARE_TOKEN = os.environ.get(key)
    if TUSHARE_TOKEN: break

if not TUSHARE_TOKEN:
    # try config
    import json
    try:
        with open(os.path.expanduser("~/.openclaw/openclaw.json")) as f:
            cfg = json.load(f)
            TUSHARE_TOKEN = cfg.get("skills",{}).get("entries",{}).get("tushare-data",{}).get("env",{}).get("TUSHARE_TOKEN")
    except: pass

if not TUSHARE_TOKEN:
    print("❌ TUSHARE_TOKEN not found. Please set TUSHARE_TOKEN environment variable.")
    sys.exit(1)

ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

def fetch_with_retry(fn, name, max_retries=6, base_wait=5):
    """带重试的数据获取，适用于9:25后数据延迟的场景"""
    for attempt in range(1, max_retries + 1):
        try:
            result = fn()
            if result is not None and not result.empty:
                return result
            if attempt < max_retries:
                wait = base_wait * (1.5 ** (attempt - 1))
                print(f"  ⏳ {name}数据为空，{wait:.0f}秒后重试 ({attempt}/{max_retries})...")
                time.sleep(wait)
        except Exception as e:
            if attempt < max_retries:
                wait = base_wait * (1.5 ** (attempt - 1))
                print(f"  ⚠️ {name}请求失败: {e}, {wait:.0f}秒后重试 ({attempt}/{max_retries})...")
                time.sleep(wait)
            else:
                print(f"  ❌ {name}请求最终失败: {e}")
    return None

def get_recent_days(target_date=None):
    """获取最近交易日"""
    if target_date:
        cal = pro.trade_cal(start_date="20260101", end_date="20261231")
    else:
        from datetime import datetime
        today = datetime.now().strftime("%Y%m%d")
        cal = pro.trade_cal(start_date="20260101", end_date=today)
    cal = cal[cal["is_open"] == 1]["cal_date"].tolist()
    cal.sort()
    if target_date and target_date in cal:
        idx = cal.index(target_date)
        return cal[max(0, idx-2):idx+1]
    return cal[-5:]

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="v5竞价选股")
    parser.add_argument("--date", type=str, help="目标日期 YYYYMMDD")
    args = parser.parse_args()

    days = get_recent_days(args.date)
    target = args.date or days[-1]
    prev_idx = days.index(target) - 1 if target in days else -1
    
    if prev_idx < 0:
        print(f"❌ 日期 {target} 无前一日数据")
        sys.exit(1)
    
    prev = days[prev_idx]
    
    print(f"📊 竞价选股: {target} (前一日 {prev})")
    
    # 数据获取 — 带重试（9:25后竞价数据可能延迟更新）
    print("  获取昨日涨停数据...")
    prev_zt = fetch_with_retry(
        lambda: pro.limit_list_d(trade_date=prev, limit_type="U"),
        "昨日涨停", max_retries=3, base_wait=3
    )
    if prev_zt is None:
        print("  ❌ 无法获取昨日涨停数据，终止")
        sys.exit(1)
    
    print("  获取今日竞价数据（等待数据更新）...")
    today_auction = fetch_with_retry(
        lambda: pro.stk_auction(trade_date=target),
        "竞价(stk_auction)", max_retries=12, base_wait=5
    )
    if today_auction is None:
        print("  ❌ 竞价数据多次重试后仍为空，可能今日非交易日或接口异常")
        print("  建议：稍后手动重试，或检查交易日历")
        sys.exit(1)
    print(f"  ✅ 竞价数据获取成功 ({len(today_auction)}条)")
    
    auc_map = {}
    for _, r in today_auction.iterrows():
        c = r["ts_code"]; p = float(r["price"]); pc = float(r["pre_close"])
        auc_map[c] = (p, (p/pc-1)*100, float(r["amount"]))
    
    # 进度
    zt_set = set(prev_zt["ts_code"])
    print(f"  候选池: {len(zt_set)}只")
    
    cand = []
    for _, r in prev_zt.iterrows():
        code = r["ts_code"]; name = str(r.get("name", ""))
        if "ST" in name or code.endswith(".BJ"): continue
        mv = float(r["float_mv"])/1e8 if r["float_mv"] else 0
        if mv < 20: continue
        if code not in auc_map: continue
        
        buy_p, gap, auc_amt = auc_map[code]
        if gap < -3: continue
        vr = auc_amt/(mv*1e8)*100
        if vr < 0.05: continue
        
        lt = int(r.get("limit_times", 1))
        pa = float(r["amount"]) if r["amount"] else 0
        fa = float(r["fd_amount"]) if r["fd_amount"] else 0
        ot = int(r["open_times"]) if r["open_times"] is not None else 0
        ls = str(r.get("last_time", ""))
        lr = fa/pa*100 if pa > 0 else 0
        if lr < 5: continue
        
        is_lb = ot >= 2 or (ls and ls != "nan" and int(ls) > 143000)
        
        # v5评分
        if mv > 200:
            a = 5 if vr>=0.5 else 4 if vr>=0.3 else 4 if vr>=0.2 else 3 if vr>=0.1 else 2
        else:
            a = 5 if vr>=0.5 else 4 if vr>=0.3 else 3 if vr>=0.2 else 2 if vr>=0.1 else 1
        if vr > 1.0: a -= 0.5
        
        if 8 <= gap < 10: b = 5
        elif gap >= 10: b = 4.5
        elif 3 <= gap < 8: b = 4
        elif gap >= 1: b = 3
        elif gap >= -0.5: b = 3
        else: b = 2
        
        c_s = 4.5 if lt == 3 else 4 if lt == 2 else 2.5 if lt == 1 else 2
        d_s = 4
        
        if lt >= 2 and gap >= 6 and vr > 0.3: e = 5 if not is_lb else 4
        elif lt >= 2 and gap >= 4: e = 4 if not is_lb else 3
        elif lt >= 2 and gap >= 1: e = 2
        elif lt >= 2: e = 2
        elif lt == 1 and gap >= 6: e = 3
        elif lt == 1 and gap >= 3: e = 2.5
        elif lt == 1 and gap >= 1: e = 2
        else: e = 2
        
        risk = 0
        if lr < 10: risk += 2
        elif lr < 30: risk += 1.5
        elif lr < 50: risk += 0.5
        if lt >= 4: risk += 2
        if lt >= 3 and gap < 2: risk += 1
        if mv >= 500: risk -= 1
        elif mv >= 200: risk -= 0.5
        if is_lb: risk += 0.5
        
        raw = a*0.25 + b*0.20 + c_s*0.15 + d_s*0.20 + e*0.20
        score = raw * 4 - risk
        
        if score >= 16:
            cand.append({
                "name": name, "code": code, "score": round(score, 1),
                "gap": round(gap, 1), "lt": lt, "vr": round(vr, 3),
                "mv": round(mv, 0), "e": round(e, 1),
            })
    
    cand.sort(key=lambda x: x["score"], reverse=True)
    top3 = cand[:3]
    
    print(f"\n## 一、市场情绪")
    # 快速情绪
    max_lt = max(r.get("limit_times", 1) for _, r in prev_zt.iterrows())
    n3 = sum(1 for _, r in prev_zt.iterrows() if r.get("limit_times") == 3)
    n4 = sum(1 for _, r in prev_zt.iterrows() if r.get("limit_times") == 4)
    r34 = n4/n3*100 if n3 > 0 else 0
    
    print(f"| 指标 | 数据 |")
    print(f"|---:|:---|")
    print(f"| 昨日最高板 | {max_lt}板 |")
    print(f"| 3进4晋级率 | {r34:.0f}% |")
    
    is_defense = r34 < 20 or max_lt < 3
    
    # 退潮期输出观察池, 友好期输出首选池
    if is_defense:
        print(f"\n## 二、📋 观察池（退潮防守期，不推荐买入）")
    else:
        print(f"\n## 二、🏆 首选买入池 (TOP{len(top3)})")
    print(f"| {'股票':>6s} | {'代码':>10s} | {'连板':>3s} | {'竞开%':>5s} | {'量比%':>5s} | {'评分':>4s} |")
    print(f"|---:|---:|---:|---:|---:|---:|")
    for c in top3:
        print(f"| {c['name']:>6s} | {c['code']} | {c['lt']}板 | {c['gap']:+5.1f}% | {c['vr']:>4.2f}% | {c['score']:>4.1f} |")
    
    print(f"\n## 三、最终结论")
    if is_defense:
        print(f"  🛑 退潮防守期，今日不推荐接力")
        if top3:
            print(f"  可等9:30后承接验证：{'、'.join([c['name'] for c in top3[:3]])}")
    else:
        print(f"  ✅ 可操作，TOP3评分最高3只")
        best = top3[0] if top3 else None
        if best:
            print(f"  首选: {best['name']}({best['code']}) {best['score']}分")
    
    print(f"\n⚠️ 仅策略研究，不构成投资建议")
