REASONS = {"COLUMNS":"列数不符", "HEADER":"字段缺失", "MISSING":"必填值为空", "NUMBER":"数值无效", "RANGE":"超出范围", "DUPLICATE":"相同行重复", "CONFLICT":"同键数值冲突", "VERSION":"版本冲突", "EMPTY":"输入为空", "STRUCTURE":"文件结构错误",
           "RECALL":"召回凭证：该到货单被召回", "LOT_RECALL":"同批号存在召回凭证，整批停发", "LOT_CONFLICT":"同批号属性冲突，整批隔离裁决"}
def isolate(ctx, row, code, detail):
    ctx['quarantine'].append({**row, '原因码': code, '原因': detail})
