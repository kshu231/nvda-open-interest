# usage: summ.py cboe.json SYM EXPIRY(YYMMDD) SPOT TDAYS [save.csv] [prev.csv] [date]
import json,re,math,csv,sys
f,sym,exp,S,td=sys.argv[1],sys.argv[2],sys.argv[3],float(sys.argv[4]),float(sys.argv[5])
save=sys.argv[6] if len(sys.argv)>6 and sys.argv[6]!='-' else None
prev=sys.argv[7] if len(sys.argv)>7 and sys.argv[7]!='-' else None
date=sys.argv[8] if len(sys.argv)>8 else ''
d=json.load(open(f));R={}
for o in d['data']['options']:
    m=re.match(sym+r'(\d{6})([CP])(\d{8})$',o['option'])
    if m and m.group(1)==exp: R.setdefault(int(m.group(3))/1000,{})[m.group(2)]=o
npdf=lambda x:math.exp(-x*x/2)/math.sqrt(2*math.pi)
K=sorted(R);oi=lambda k,t:(R[k].get(t,{}).get('open_interest') or 0)
pain=min(K,key=lambda s:sum(oi(k,'C')*max(s-k,0)+oi(k,'P')*max(k-s,0) for k in K))
near=[k for k in K if abs(k/S-1)<0.03];top=sorted(near,key=lambda k:-(oi(k,'C')+oi(k,'P')))[:7]
tc=sum(oi(k,'C') for k in K);tp=sum(oi(k,'P') for k in K)
atm=min(K,key=lambda k:abs(k-S));mid=lambda o:((o.get('bid') or 0)+(o.get('ask') or 0))/2;strad=mid(R[atm]['C'])+mid(R[atm]['P'])
t=max(td,0.02)/365;tot=0
for k in near:
    for typ,sg in (('C',1),('P',-1)):
        o=R[k].get(typ);iv=(o or {}).get('iv') or 0
        if not o or iv<=0: continue
        d1=(math.log(S/k)+iv*iv/2*t)/(iv*math.sqrt(t));tot+=sg*oi(k,typ)*npdf(d1)/(S*iv*math.sqrt(t))*100*S*S*0.01
print(f"{sym} {exp} (cboe {d['timestamp']} px {d['data']['current_price']}): OI C {tc:,.0f} P {tp:,.0f}; MaxPain {pain}; straddle {strad:.2f} EM~±{strad*0.85:.2f}; netGEX ${tot/1e9:.2f}B/1%")
print('  top:',', '.join(f"{k:g} C{oi(k,'C'):,.0f}/P{oi(k,'P'):,.0f}" for k in top))
if prev:
    p={float(r['strike']):(int(float(r['call_oi'])),int(float(r['put_oi']))) for r in csv.DictReader(open(prev))}
    ch=[(k,oi(k,'C')-p[k][0],oi(k,'P')-p[k][1]) for k in K if k in p]
    print('  biggest changes:',', '.join(f"{k:g} C{c:+,.0f}/P{q:+,.0f}" for k,c,q in sorted(ch,key=lambda x:-(abs(x[1])+abs(x[2])))[:8]))
    print('  total change: C %+d P %+d'%(sum(x[1] for x in ch),sum(x[2] for x in ch)))
if save:
    with open(save,'w') as fo:
        w=csv.writer(fo);w.writerow(['date','strike','call_oi','put_oi'])
        for k in K:
            if abs(k/S-1)<0.06: w.writerow([date,k,oi(k,'C'),oi(k,'P')])
