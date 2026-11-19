from app.domain import AS_OF, day
from app.reasons import isolate
def apply(ctx):
    rows=[];usable=[]
    for s in ctx['stocks']:
        days=(day(s['expiry'])-AS_OF).days
        if days<=0: isolate(ctx,s,'EXPIRED','截至日已失效');continue
        # 可调拨量沿用批号裁决（M2）合并口径：多来源到货量扣冻结后整包装取整，保证两页数字一致。
        available=s['available']
        item={**s,'available':available,'余期_天':days,'效期层':'近效期' if days<=30 else '常规'}
        usable.append(item)
        rows.append({'批号':s['lot'],'药品':s['sku'],'到货来源':','.join(s.get('sources',[])),'来源单数':s.get('source_count',1),'到期日':s['expiry'],'余期_天':days,'可调拨':available,'冻结':s['frozen_n'],'包装余数':(s['qty_n']-s['frozen_n'])%s['pack_n'],'层级':item['效期层']})
    ctx['usable']=usable
    return {'rows':rows,'metrics':[['近效期',sum(r['层级']=='近效期' for r in rows),'批'],['可调拨',sum(r['可调拨'] for r in rows),'件'],['有效批号',len(rows),'批']]}
