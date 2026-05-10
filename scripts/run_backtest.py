#!/usr/bin/env python3
"""v5-Top3竞价选股策略 — 回测验证脚本
用法: python run_backtest.py --start 20260409 --end 20260508
      python run_backtest.py --quick  # 快速回测(近19天)
"""
import argparse, sys, os
import tushare as ts
import pandas as pd
import time
import json

# Token
TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN")
if not TUSHARE_TOKEN:
    try:
        with open(os.path.expanduser("~/.openclaw/openclaw.json")) as f:
            cfg = json.load(f)
            TUSHARE_TOKEN = cfg.get("skills",{}).get("entries",{}).get("tushare-data",{}).get("env",{}).get("TUSHARE_TOKEN")
    except: pass
if not TUSHARE_TOKEN:
    print("❌ TUSHARE_TOKEN not found"); sys.exit(1)

ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

def fetch_with_retry(fn, name, max_retries=6, base_wait=5):
    """带重试的数据获取"""
    for attempt in range(1, max_retries + 1):
        try:
            result = fn()
            if result is not None and not result.empty:
                return result
            if attempt < max_retries:
                wait = base_wait * (1.5 ** (attempt - 1))
                print(f"  ⏳ {name}为空,{wait:.0f}s后重试({attempt}/{max_retries})...")
                time.sleep(wait)
        except Exception as e:
            if attempt < max_retries:
                wait = base_wait * (1.5 ** (attempt - 1))
                print(f"  ⚠️ {name}失败:{e},{wait:.0f}s后重试({attempt}/{max_retries})...")
                time.sleep(wait)
    return None

parser = argparse.ArgumentParser(description="v5竞价选股回测")
parser.add_argument("--start", type=str, help="起始日期 YYYYMMDD")
parser.add_argument("--end", type=str, default="20260508", help="结束日期 YYYYMMDD")
parser.add_argument("--quick", action="store_true", help="快速回测(近19天)")
args = parser.parse_args()

if args.quick or args.start is None:
    days = ['20260409','20260410','20260413','20260414','20260415','20260416',
            '20260417','20260420','20260421','20260422','20260423','20260424',
            '20260427','20260428','20260429','20260430','20260506','20260507','20260508']
else:
    cal_bt = pro.trade_cal(start_date=args.start, end_date=args.end)
    days = cal_bt[cal_bt["is_open"] == 1]["cal_date"].tolist()
days.sort()
cal = pro.trade_cal(start_date="20251109",end_date="20260508")
all_d = cal[cal["is_open"]==1]["cal_date"].tolist(); all_d.sort()

# 数据
zt_all = {}
for d in all_d:
    df = pro.limit_list_d(trade_date=d,limit_type="U")
    if df is not None and not df.empty: zt_all[d] = df
    time.sleep(0.12)
zt = {d:zt_all[d] for d in days if d in zt_all}

auction = {}
for d in days:
    df = pro.stk_auction(trade_date=d)
    if df is not None and not df.empty:
        for _, r in df.iterrows():
            auction[(d,r["ts_code"])]=(float(r["price"]),(float(r["price"])/float(r["pre_close"])-1)*100,float(r["amount"]))
    time.sleep(0.25)

closes = {}
for d in days:
    df = pro.daily(trade_date=d)
    if df is not None and not df.empty:
        for _, r in df.iterrows(): closes[(d,r["ts_code"])]=float(r["close"])
    time.sleep(0.25)

# 股性（简化）
zt_by_c = {}
for d in all_d:
    if d in zt_all:
        for _, r in zt_all[d].iterrows():
            c=r["ts_code"]
            if c not in zt_by_c: zt_by_c[c]=set()
            zt_by_c[c].add(d)

def gx(code,ref,n):
    ds=zt_by_c.get(code,set()); idx=all_d.index(ref); w=all_d[max(0,idx-n):idx]
    return sum(1 for d in w if d in ds)

# 情绪 (v4原版)
def get_mood(idx):
    prv=all_d[idx-1]; p=zt_all.get(prv)
    if p is None: return "未知",0,0
    ds={}
    for _,r in p.iterrows():
        lt=int(r.get("limit_times",1)); ds[lt]=ds.get(lt,0)+1
    mb=max(ds.keys()) if ds else 0
    n2=ds.get(2,0);n3=ds.get(3,0);n4=ds.get(4,0)
    r23=n3/n2*100 if n2>0 else 0;r34=n4/n3*100 if n3>0 else 0;ar=(r23+r34)/2
    hs=p[p["limit_times"]==mb] if mb>0 else pd.DataFrame()
    hg=[]; td=all_d[idx]
    for _,r in hs.iterrows():
        if r["ts_code"] in auction: hg.append(auction[(td,r["ts_code"])][1])
    hn=min(hg)<-5 if hg else False
    if ar<20 or mb<3 or hn: return "退潮防守期 🛑",ar,mb
    elif ar>=35 and mb>=4 and not hn: return "接力友好期 ✅",ar,mb
    else: return "分歧观察期 ⚡",ar,mb

# ===== 回测 =====
trades=[]
for i in range(1,len(days)):
    prv=days[i-1];tdy=days[i]
    if prv not in zt: continue
    mood,ar,mb=get_mood(i)
    if "退潮" in mood: continue
    
    cand=[]
    for _,r in zt[prv].iterrows():
        code=r["ts_code"];name=str(r.get("name",""))
        if "ST" in name: continue
        mv=float(r["float_mv"])/1e8 if r["float_mv"] else 0
        if mv<20: continue
        if (tdy,code) not in auction: continue
        
        buy_p,gap,auc_amt=auction[(tdy,code)]
        if gap<-3:continue
        vr=auc_amt/(mv*1e8)*100
        if vr<0.05:continue
        
        lt=int(r.get("limit_times",1))
        pa=float(r["amount"]) if r["amount"] else 0
        fa=float(r["fd_amount"]) if r["fd_amount"] else 0
        ot=int(r["open_times"]) if r["open_times"] is not None else 0
        ls=str(r.get("last_time",""))
        lr=fa/pa*100 if pa>0 else 0
        if lr<5:continue
        
        is_lb=ot>=2 or (ls and ls!="nan" and int(ls)>143000)
        gx10=gx(code,tdy,10)
        
        # v4评分 (完全一致)
        if mv>200:a=5 if vr>=0.5 else 4 if vr>=0.3 else 4 if vr>=0.2 else 3 if vr>=0.1 else 2
        else:a=5 if vr>=0.5 else 4 if vr>=0.3 else 3 if vr>=0.2 else 2 if vr>=0.1 else 1
        if vr>1.0:a-=0.5
        
        if 8<=gap<10:b=5
        elif gap>=10:b=4.5
        elif 3<=gap<8:b=4
        elif gap>=1:b=3
        elif gap>=-0.5:b=3
        else:b=2
        
        c_s=4.5 if lt==3 else 4 if lt==2 else 2.5 if lt==1 else 2
        d_s=4
        
        if lt>=2 and gap>=6 and vr>0.3:e=5 if not is_lb else 4
        elif lt>=2 and gap>=4:e=4 if not is_lb else 3
        elif lt>=2 and gap>=1:e=2
        elif lt>=2:e=2
        elif lt==1 and gap>=6:e=3
        elif lt==1 and gap>=3:e=2.5
        elif lt==1 and gap>=1:e=2
        else:e=2
        
        risk=0
        if lr<10:risk+=2
        elif lr<30:risk+=1.5
        elif lr<50:risk+=0.5
        if lt>=4:risk+=2
        if lt>=3 and gap<2:risk+=1
        if mv>=500:risk-=1
        elif mv>=200:risk-=0.5
        if is_lb:risk+=0.5
        
        raw=a*0.25+b*0.20+c_s*0.15+d_s*0.20+e*0.20
        score=raw*4-risk
        
        # v5唯一新增：微量股性加分
        if gx10>=5:score+=1.0
        elif gx10>=3:score+=0.5
        
        if "分歧" in mood:score-=1.5
        
        if score>=16:
            cand.append({"code":code,"name":name,"score":score,"gap":gap,
                        "lt":lt,"buy_p":buy_p,"vr":vr,"mv":mv,"e":e,"gx10":gx10})
    
    cand.sort(key=lambda x:x["score"],reverse=True)
    for c in cand[:3]:
        ret=None
        if i+1<len(days):
            nxt=days[i+1];no=auction.get((nxt,c["code"]));nc=closes.get((nxt,c["code"]))
            sl=[x for x in [no[0] if no else 0,nc if nc else 0] if x>0]
            if sl:ret=(max(sl)/c["buy_p"]-1)*100
        trades.append({
            "date":tdy,"code":c["code"],"name":c["name"],
            "s":round(c["score"],1),"gap":c["gap"],"lt":c["lt"],
            "vr":round(c["vr"],3),"mv":c["mv"],"e":c["e"],
            "gx":c["gx10"],"ret":ret,"mood":mood,
        })

df=pd.DataFrame(trades);dv=df[df["ret"].notna()]
r=dv["ret"];w=(r>0).sum();t=len(dv);aw=r[r>0].mean();al=r[r<=0].mean()

print(f"\n🏆 v5最终版: {t}笔, 胜率{w/t*100:.1f}%, 均收{r.mean():+.2f}%, 累计{r.sum():+.2f}%")
print(f"   盈亏比{abs(aw/al):.2f}, 最大赚{r.max():+.2f}%, 最大亏{r.min():+.2f}%")

print(f"\n【每日】")
for dt,grp in dv.groupby("date"):
    rr=grp["ret"]
    print(f"  {dt}:{len(grp)}笔,胜{(rr>0).sum()}笔({(rr>0).mean()*100:.0f}%),均{rr.mean():+.1f}%,总{rr.sum():+.1f}%")

print(f"\n【分数段】")
for lo,hi in [(14,16.5),(16.5,18),(18,20),(20,25)]:
    sub=dv[(dv["s"]>=lo)&(dv["s"]<hi)]
    if len(sub)>0:print(f"  {lo:.0f}~{hi:.0f}分:{len(sub)}笔,胜率{(sub['ret']>0).mean()*100:.0f}%,均收{sub['ret'].mean():+.2f}%")

print(f"\n【TOP3详情】")
for _,rr in dv.iterrows():
    gs=f"股{rr['gx']}次"if rr["gx"]>0 else""
    rs=f"✅ +{rr['ret']:.1f}%"if rr["ret"]>0 else f"❌ {rr['ret']:.1f}%"
    print(f"  {rr['date']} {rr['name']:>6s}: {rr['s']:.1f}分 {rr['lt']}板 {rr['gap']:+.0f}% {gs} → {rs}")

print(f"\n【vs全版本】")
print(f"  v1原始(全量):     152笔, 63.2%, +2.24%, +340%")
print(f"  v4-TOP3(基准):     32笔, 75.0%, +3.58%, +114%")
print(f"  v5(最终):          {t}笔, {w/t*100:.1f}%, {r.mean():+.2f}%, {r.sum():+.2f}%")
