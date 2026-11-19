from app.domain import whole, day
from app.errors import DomainError
from app.reasons import isolate

# 召回处置策略（固定，写入每次裁决的原因列）：
# 整批停发：同一药品+批号下只要有一张到货单被召回，该批号所有来源全部停发。
# 仅停来源（备选，未采用）：只隔离被召回的到货单，其余到货来源仍可调拨。
# 药品召回按 GSP 要求按批号控制，且无法仅凭到货单区分同批实物，故采用整批停发；
# 备选口径的数量仍在台账中列出，供核对员评估被压住的安全库存代价。
RECALL_POLICY = '整批停发'
RECALL_REASON = '召回按批号控制：同批号任一来货单召回即整批停发，避免门店收到同批风险货；仅停来源的备选数量见台账'


def _row_digest(r):
    return tuple((k, v) for k, v in r.items() if k != '行号')


def _validate_rows(ctx):
    """逐行校验：无效行进隔离；同到货单重跑不重复加量（相同内容计重复，冲突进隔离）。"""
    stocks = []
    demands = []
    seen = {}
    duplicates = 0
    for r in ctx['rows']:
        try:
            if r['kind'] not in ('stock', 'demand'):
                raise DomainError('KIND', '记录类别无效')
            qty = whole(r['qty'])
            frozen = whole(r['frozen'])
            pack = whole(r['pack'])
            days = whole(r['min_days'])
            if (r['kind'] == 'stock' and frozen > qty) or pack < 1:
                raise DomainError('QUANTITY', '冻结量或包装数量无效')
            if not r['sku'] or not r['record']:
                raise DomainError('MISSING', '业务编号为空')
            if r['version'] != '1':
                raise DomainError('VERSION', '来源版本不支持')
            day(r['arrival'])
            if r['kind'] == 'stock':
                day(r['expiry'])
                if not r['lot']:
                    raise DomainError('MISSING', '批号为空')
            key = (r['kind'], r['record'])
            fingerprint = _row_digest(r)
            if key in seen:
                # 同一到货单重跑：内容一致只计一次，绝不重复加量；内容冲突隔离，不盲目覆盖。
                if seen[key] == fingerprint:
                    duplicates += 1
                else:
                    isolate(ctx, r, 'CONFLICT', '来源单号内容冲突')
                continue
            seen[key] = fingerprint
            item = {**r, 'qty_n': qty, 'frozen_n': frozen, 'pack_n': pack, 'min_days_n': days}
            (stocks if r['kind'] == 'stock' else demands).append(item)
        except DomainError as e:
            isolate(ctx, r, e.code, e.message)
    return stocks, demands, duplicates


def _pack_available(qty, frozen, pack):
    """先扣冻结，再按整包装向下取整得到可调拨量。"""
    return max(0, (qty - frozen) // pack * pack)


def apply(ctx):
    stocks, demands, duplicates = _validate_rows(ctx)

    # 批号维度聚合：同一药品+批号可由多张到货单分别入仓，按 (sku, lot) 合并。
    groups = {}
    order = []
    for s in stocks:
        key = (s['sku'], s['lot'])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(s)

    ledger = []          # 批号台账：页面与证据使用，含全部批号（停发/冲突也留痕）
    valid = []           # 可继续进入效期分层的合并批号
    recalled_lots = 0
    conflict_lots = 0

    for key in order:
        sources = sorted(groups[key], key=lambda x: (x['arrival'], x['record'], x['行号']))
        sku, lot = key
        pack = sources[0]['pack_n']
        expiry = sources[0]['expiry']
        recalled_sources = [s for s in sources if s['status'] == 'recall']
        normal_sources = [s for s in sources if s['status'] != 'recall']

        # 属性冲突：同批号包装或到期日不一致时无法安全合并，整批隔离等待人工裁决，不按文件顺序覆盖。
        attr_conflict = [s for s in sources if s['pack_n'] != pack or s['expiry'] != expiry]
        for s in attr_conflict:
            isolate(ctx, s, 'LOT_CONFLICT', '同批号包装或到期日与首条来源不一致，整批隔离待裁决')
        mergeable = [s for s in sources if s not in attr_conflict]

        arrived = sum(s['qty_n'] for s in mergeable)
        frozen = sum(s['frozen_n'] for s in mergeable)
        source_records = sorted({s['record'] for s in mergeable})
        source_lines = ','.join(source_records)

        if attr_conflict:
            conflict_lots += 1
            ledger.append({
                '药品': sku, '批号': lot, '到期日': expiry, '到货来源': source_lines,
                '来源单数': len(source_records), '到货量': arrived, '已冻结': frozen,
                '召回量': sum(s['qty_n'] for s in mergeable if s['status'] == 'recall'),
                '停发量': arrived, '可调拨量': 0, '包装余数': 0,
                '批号状态': '属性冲突隔离', '处置策略': '人工裁决',
                '处置原因': '同批号来源属性冲突，全部数量隔离，不参与调拨',
                '仅停来源备选可调拨': 0,
            })
            continue

        if recalled_sources:
            # 整批停发：召回凭证行与同批正常来源全部隔离留痕，原到货单和召回凭证均不丢失。
            recalled_lots += 1
            recalled_qty = sum(s['qty_n'] for s in recalled_sources)
            for s in recalled_sources:
                isolate(ctx, s, 'RECALL', '召回凭证：该到货单被召回，凭证与原始行留存')
            for s in normal_sources:
                isolate(ctx, s, 'LOT_RECALL', RECALL_REASON)
            # 备选口径（仅停来源）：剔除被召回到货单后，其余来源本可释放的数量。
            alt_qty = sum(s['qty_n'] for s in normal_sources)
            alt_frozen = sum(s['frozen_n'] for s in normal_sources)
            alt_available = _pack_available(alt_qty, alt_frozen, pack)
            ledger.append({
                '药品': sku, '批号': lot, '到期日': expiry, '到货来源': source_lines,
                '来源单数': len(source_records), '到货量': arrived, '已冻结': frozen,
                '召回量': recalled_qty, '停发量': arrived, '可调拨量': 0, '包装余数': 0,
                '批号状态': '召回整批停发', '处置策略': RECALL_POLICY,
                '处置原因': RECALL_REASON,
                '仅停来源备选可调拨': alt_available,
            })
            continue

        available = _pack_available(arrived, frozen, pack)
        stock_key = sku + ':' + lot
        merged = {
            'record': source_records[0], 'kind': 'stock', 'sku': sku, 'lot': lot,
            'qty': str(arrived), 'frozen': str(frozen), 'pack': str(pack),
            'expiry': expiry, 'store': sources[0]['store'], 'min_days': sources[0]['min_days'],
            'arrival': min(s['arrival'] for s in sources), 'version': '1', 'status': 'normal',
            '行号': min(s['行号'] for s in sources),
            'qty_n': arrived, 'frozen_n': frozen, 'pack_n': pack,
            'min_days_n': sources[0]['min_days_n'], 'stock_key': stock_key,
            'available': available, 'sources': source_records, 'source_count': len(source_records),
        }
        valid.append(merged)
        ledger.append({
            '药品': sku, '批号': lot, '到期日': expiry, '到货来源': source_lines,
            '来源单数': len(source_records), '到货量': arrived, '已冻结': frozen,
            '召回量': 0, '停发量': 0, '可调拨量': available,
            '包装余数': max(0, arrived - frozen) % pack,
            '批号状态': '可调拨', '处置策略': '正常合并',
            '处置原因': '多张到货单按批号合并，扣冻结后按整包装取整',
            '仅停来源备选可调拨': available,
        })

    ctx.update(stocks=valid, lot_ledger=ledger, demands=demands, duplicates=duplicates,
               recalled_lots=recalled_lots, conflict_lots=conflict_lots)
    metrics = [
        ['批号台账', len(ledger), '批'],
        ['可调拨批号', len(valid), '批'],
        ['裁决可调拨量', sum(x['可调拨量'] for x in ledger), '件'],
        ['召回整批停发', recalled_lots, '批'],
        ['隔离', len(ctx['quarantine']), '条'],
    ]
    return {'rows': ledger, 'metrics': metrics,
            'policy': {'name': RECALL_POLICY, 'detail': RECALL_REASON,
                       'alternative': '仅停来源：只隔离被召回到货单，保留同批其余来源可调拨（未采用，备选数量见台账“仅停来源备选可调拨”列）'}}
