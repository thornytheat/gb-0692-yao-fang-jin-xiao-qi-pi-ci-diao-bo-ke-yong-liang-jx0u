from app.domain import eligible
def test_expired_isolated(ctx): # TRAP-23
    assert any(r['原因码']=='EXPIRED' for r in ctx['quarantine'])
def test_pack_rounding(ctx): assert all(r['available']%r['pack_n']==0 for r in ctx['usable'])
def test_frozen_removed(ctx): assert ctx['usable'][0]['available']==130
def test_near_expiry_class(ctx): assert ctx['usable'][0]['效期层']=='近效期'
def test_expiry_day_ineligible():
    assert not eligible({'sku':'x','status':'normal','expiry':'2026-11-20'},{'sku':'x','arrival':'2026-11-20','min_days_n':0})
def test_minimum_day_inclusive():
    assert eligible({'sku':'x','status':'normal','expiry':'2026-11-30'},{'sku':'x','arrival':'2026-11-20','min_days_n':10})
def test_lots_survive(ctx): assert len(ctx['usable'])==24
def test_m2_adjudicated_quantity_minus_expired_equals_m3(ctx):
    # M2 裁决口径含未到期判定；M3 剔除到期批号后才是最终可直接调拨量
    from datetime import date
    from app.domain import AS_OF
    m2_adjudicated=sum(x['可调拨量'] for x in ctx['lot_ledger'])
    expired=sum(x['可调拨量'] for x in ctx['lot_ledger'] if date.fromisoformat(x['到期日'])<=AS_OF)
    m3_final=sum(x['可调拨'] for x in ctx['stages']['3']['rows'])
    assert m2_adjudicated-expired==m3_final
def test_m3_rows_carry_arrival_sources(ctx):
    row=next(r for r in ctx['stages']['3']['rows'] if r['批号']=='B0001')
    assert row['到货来源']=='S0001,S1001' and row['来源单数']==2

