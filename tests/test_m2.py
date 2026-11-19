from app.domain import whole
from app.errors import DomainError
import pytest


def test_sku_leading_zero(ctx):  # TRAP-08
    assert ctx['stocks'][0]['sku'] == '0002'


def test_missing_quantity(ctx):  # TRAP-07
    assert any(r['原因码'] == 'MISSING' for r in ctx['quarantine'])


def test_conflict_not_override(ctx):  # TRAP-14：同单冲突不盲目覆盖，采信首条
    assert next(r for r in ctx['quarantine'] if r['record'] == 'S0002')['原因码'] == 'CONFLICT'
    lot = next(x for x in ctx['lot_ledger'] if x['批号'] == 'B0002')
    assert lot['到货量'] == 110 and lot['可调拨量'] == 100


def test_recall_dominates(ctx):  # TRAP-19：召回批不得进入可调拨库存
    assert all(s['lot'] != 'RECALL' for s in ctx['stocks'])
    assert all(s['lot'] != 'B0050' for s in ctx['stocks'])


def test_integral_quantity():
    assert whole('２５') == 25


def test_fraction_rejected():
    with pytest.raises(DomainError):
        whole('1.5')


def test_duplicate_count(ctx):
    # 样例中 S0001、S0001 各重跑一次，共两条完全相同重复
    assert ctx['duplicates'] == 2


def test_store_stock_exceeding_request_is_zero_need(raw):
    from app.pipeline import calculate
    lines = raw.splitlines()
    lines[25] = lines[25].replace(',31,2,', ',31,99,')
    result = calculate('\n'.join(lines) + '\n')
    demand = next(d for d in result['net_demands'] if d['record'] == 'D0001')
    assert demand['net_need'] == 0


def test_same_lot_in_different_drugs_is_separate(raw):
    from app.pipeline import calculate
    result = calculate(raw + 'S9999,stock,0099,B0001,100,0,5,2027-01-01,中央仓,0,2026-11-20,1,normal\n')
    assert len([s for s in result['stocks'] if s['lot'] == 'B0001']) == 2


# ---------- 多来源同批号合并 ----------

def test_multi_source_lot_merges_arrivals(ctx):
    # B0001 由 S0001(105/冻10) 与 S1001(40/冻5) 两张到货单分别入仓
    lot = next(x for x in ctx['lot_ledger'] if x['批号'] == 'B0001')
    assert lot['来源单数'] == 2
    assert lot['到货来源'] == 'S0001,S1001'
    assert lot['到货量'] == 145 and lot['已冻结'] == 15
    assert lot['可调拨量'] == 130  # (145-15)//5*5
    merged = next(s for s in ctx['stocks'] if s['lot'] == 'B0001')
    assert merged['sources'] == ['S0001', 'S1001'] and merged['available'] == 130


def test_rerun_same_arrival_order_never_doubles(ctx):
    # 末尾 S0001 是同一到货单重跑：只能计一次，B0001 不得变成 250
    lot = next(x for x in ctx['lot_ledger'] if x['批号'] == 'B0001')
    assert lot['到货量'] == 145
    assert sum(1 for r in ctx['quarantine'] if r.get('record') == 'S0001') == 0


def test_pack_remainder_kept_out_of_allocation(raw):
    from app.pipeline import calculate
    extra = 'S2001,stock,0002,B0077,107,10,5,2027-03-01,中央仓,0,2026-11-21,1,normal\n'
    result = calculate(raw + extra)
    lot = next(x for x in result['lot_ledger'] if x['批号'] == 'B0077')
    assert lot['可调拨量'] == 95 and lot['包装余数'] == 2
    # 守恒：到货量 = 已冻结 + 包装余数 + 可调拨量 + 停发量
    assert lot['到货量'] == lot['已冻结'] + lot['包装余数'] + lot['可调拨量'] + lot['停发量']


def test_ledger_quantity_identity(ctx):
    for row in ctx['lot_ledger']:
        if row['批号状态'] == '属性冲突隔离':
            continue  # 冲突批整批隔离，数量不参与守恒口径
        assert row['到货量'] == row['已冻结'] + row['包装余数'] + row['可调拨量'] + row['停发量']


# ---------- 召回：整批停发策略与备选口径 ----------

def test_recalled_multi_source_lot_whole_hold(ctx):
    # B0050：S1002 正常 60 + S1003 召回 30，整批停发
    lot = next(x for x in ctx['lot_ledger'] if x['批号'] == 'B0050')
    assert lot['批号状态'] == '召回整批停发'
    assert lot['召回量'] == 30 and lot['停发量'] == 90 and lot['可调拨量'] == 0
    assert lot['仅停来源备选可调拨'] == 60  # 备选口径本可释放的安全库存
    assert lot['处置策略'] == '整批停发' and lot['处置原因']


def test_recall_voucher_and_normal_source_both_retained(ctx):
    codes = {(r.get('record'), r['原因码']) for r in ctx['quarantine']}
    assert ('S1003', 'RECALL') in codes       # 召回凭证留存
    assert ('S1002', 'LOT_RECALL') in codes   # 同批正常来源一并停发留痕
    for r in ctx['quarantine']:
        if r.get('record') in ('S1002', 'S1003'):
            assert r['行号'] and r['原因']


def test_policy_explained_in_stage_result(ctx):
    policy = ctx['stages']['2']['policy']
    assert policy['name'] == '整批停发' and policy['detail'] and policy['alternative']


def test_lot_attribute_conflict_quarantines_whole_lot(raw):
    from app.pipeline import calculate
    # 同批号不同包装，无法安全合并，整批隔离
    extra = 'S2002,stock,0002,B0088,50,0,5,2027-03-01,中央仓,0,2026-11-21,1,normal\n' \
            'S2003,stock,0002,B0088,40,0,8,2027-03-01,中央仓,0,2026-11-21,1,normal\n'
    result = calculate(raw + extra)
    assert any(r['原因码'] == 'LOT_CONFLICT' for r in result['quarantine'])
    assert all(s['lot'] != 'B0088' for s in result['stocks'])


# ---------- 接口：幂等与失败保留旧版 ----------

def test_api_rerun_same_csv_is_idempotent(client, raw):
    first = client.post('/api/run', json={'csv': raw}).get_json()
    again = client.post('/api/run', json={'csv': raw}).get_json()
    assert first['manifest']['run_id'] == again['manifest']['run_id']
    assert again['reused'] is True
    assert len(client.get('/api/history').get_json()) == 1


def test_failed_run_preserves_previous_result(client, raw):
    good = client.post('/api/run', json={'csv': raw}).get_json()
    ident = good['manifest']['run_id']
    bad = client.post('/api/run', json={'csv': ''})
    assert bad.status_code == 422
    kept = client.get('/api/runs/' + ident).get_json()
    assert kept['manifest']['run_id'] == ident
    assert len(kept['lot_ledger']) == len(good['lot_ledger'])
    assert len(client.get('/api/history').get_json()) == 1


def test_evidence_contains_sources_and_recall_rows(client, raw):
    run = client.post('/api/run', json={'csv': raw}).get_json()
    text = client.get('/api/evidence.csv?run_id=' + run['manifest']['run_id']).data.decode('utf-8-sig')
    assert '到货来源' in text and '仅停来源备选可调拨' in text
    assert 'S1003' in text and 'LOT_RECALL' in text


def test_m2_endpoint_returns_ledger(client):
    payload = client.post('/api/m2', json={}).get_json()
    assert payload['stage'] == 2
    assert any('可调拨量' in row for row in payload['rows'])
