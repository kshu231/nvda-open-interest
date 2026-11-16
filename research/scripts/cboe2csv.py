import json, sys, re
src, sym, expiry, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
d = json.load(open(src)); rows = []
for o in d['data']['options']:
    m = re.match(sym + r'(\d{6})([CP])(\d{8})$', o['option'])
    if not m or m.group(1) != expiry: continue
    rows.append((int(m.group(3))/1000, m.group(2), int(o['open_interest'] or 0), int(o['volume'] or 0), o['bid'], o['ask'], o['iv']))
with open(out, 'w') as f:
    f.write('strike,type,open_interest,volume,bid,ask,iv\n')
    for r in sorted(rows): f.write('%g,%s,%d,%d,%s,%s,%s\n' % r)
print(len(rows), 'rows ->', out)
