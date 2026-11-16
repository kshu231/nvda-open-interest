# usage: intraday.py SYM prevclose  (reads cboe/y_SYM.json)
import json,datetime,statistics as st,math,sys
tz=datetime.timezone(datetime.timedelta(hours=-5))
t=sys.argv[1];pc=float(sys.argv[2]) if len(sys.argv)>2 else None
r=json.load(open('cboe/y_'+t+'.json'))['chart']['result'][0];q=r['indicators']['quote'][0];m=r['meta']
b=[(datetime.datetime.fromtimestamp(ts,tz),q['high'][i],q['low'][i],q['close'][i],q['volume'][i] or 0) for i,ts in enumerate(r['timestamp'] or []) if q['close'][i] is not None]
pre=[x for x in b if x[0].time()<datetime.time(9,30)];reg=[x for x in b if datetime.time(9,30)<=x[0].time()<datetime.time(16,0)]
s=f"{t} now {m['regularMarketPrice']} hi {m.get('regularMarketDayHigh')} lo {m.get('regularMarketDayLow')}"
if pc: s+=' (%+.2f%%)'%((m['regularMarketPrice']/pc-1)*100)
if pre: s+=f" pre {min(x[2] for x in pre):.2f}-{max(x[1] for x in pre):.2f}"
if len(reg)>2 and sum(x[4] for x in reg)>0:
    med=st.median(x[4] for x in reg[1:]) or 1;pv=v=pv2=0;hist=[]
    for i,x in enumerate(reg):
        if i>0 and x[4]>20*med: continue
        h=(x[1]+x[2]+x[3])/3;pv+=h*x[4];v+=x[4];pv2+=h*h*x[4]
        vw=pv/v;sd=math.sqrt(max(pv2/v-vw*vw,0));hist.append((x[0].strftime('%H:%M'),x[3],vw,sd))
    vw,sd=hist[-1][2],hist[-1][3]
    s+=f"\n  open {reg[0][3]:.2f} last {reg[-1][0].strftime('%H:%M')} {reg[-1][3]:.2f} vwap {vw:.2f} sd {sd:.2f} z {(reg[-1][3]-vw)/max(sd,1e-6):+.2f}"
    s+=f"\n  bands -3.09 {vw-3.09*sd:.2f} -2.51 {vw-2.51*sd:.2f} -2.01 {vw-2.01*sd:.2f} -1.28 {vw-1.28*sd:.2f} +1.28 {vw+1.28*sd:.2f} +2.01 {vw+2.01*sd:.2f}"
    hi=max(reg,key=lambda x:x[1]);lo=min(reg,key=lambda x:x[2]);s+=f"\n  HOD {hi[0].strftime('%H:%M')} {hi[1]:.2f} LOD {lo[0].strftime('%H:%M')} {lo[2]:.2f}"
    s+='\n  sd path: '+' '.join(f"{h[0]}:{h[3]:.2f}" for h in hist[::30])
    s+='\n  px path: '+' '.join(f"{h[0]}:{h[1]:.2f}" for h in hist[::15])
print(s)
