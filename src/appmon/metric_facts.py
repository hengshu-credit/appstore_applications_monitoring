"""Explicit regional daily measurements; estimates and currencies remain separate."""
import csv
import io
import re
import time
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from .intelligence import market


def prepare(value):
    rows = value.get('rows')
    if 'csv' in value:
        content=value['csv']
        if not isinstance(content,str) or len(content.encode('utf-8'))>3_500_000:raise ValueError('指标 CSV 最大 3.5MB')
        try:rows=list(csv.DictReader(io.StringIO(content.lstrip('\ufeff')),strict=True))
        except csv.Error:raise ValueError('指标 CSV 格式无效') from None
    if not isinstance(rows,list) or not 1<=len(rows)<=20000:raise ValueError('每次导入 1–20000 行指标')
    prepared={}
    for row in rows:
        if not isinstance(row,dict):raise ValueError('指标记录须为对象')
        row={key:value.strip() if isinstance(value,str) else value for key,value in row.items()}
        m=market(row|{'language':'auto'})
        try:
            day=date.fromisoformat(row['date'])
            if day>datetime.now(timezone.utc).date():raise ValueError()
        except (KeyError,TypeError,ValueError):raise ValueError('指标日期须为已发生的 YYYY-MM-DD') from None
        estimate=row.get('is_estimate')
        if isinstance(estimate,str):estimate=estimate.lower()
        if estimate in ('true','1'):estimate=True
        elif estimate in ('false','0'):estimate=False
        if type(estimate) is not bool:raise ValueError('必须明确 is_estimate：true/false')
        found=False
        for metric in ('downloads','revenue'):
            number=row.get(metric)
            if number is None or number=='':continue
            try:
                number=Decimal(str(number))
                if not number.is_finite() or not 0<=number<Decimal('1000000000000000000') or number.as_tuple().exponent < -6:raise ValueError()
                if metric=='downloads' and number!=number.to_integral_value():raise ValueError()
            except (InvalidOperation,ValueError):raise ValueError('下载量须为非负整数，收入须为最多六位小数的非负数') from None
            currency=str(row.get('currency','')).upper() if metric=='revenue' else ''
            if metric=='revenue' and not re.fullmatch('[A-Z]{3}',currency):raise ValueError('收入必须提供三字母币种')
            key=(m['store'],m['app_id'],m['country'],day.isoformat(),metric,estimate)
            record=(*key,number,currency,time.time())
            if key in prepared and prepared[key][6:8]!=record[6:8]:raise ValueError('同一应用/国家/日期出现冲突指标')
            prepared[key]=record;found=True
        if not found:raise ValueError('每行至少提供 downloads 或 revenue')
    return list(prepared.values())


def save(db, rows):
    db.executemany('''INSERT INTO app_metric_facts VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) AS incoming
        ON DUPLICATE KEY UPDATE value=incoming.value,currency=incoming.currency,imported_at=incoming.imported_at''',rows)
    return {'facts':len(rows)}


def summarize(rows):
    buckets={}
    for row in rows:
        key=(row['metric'],bool(row['is_estimate']),row['currency'])
        bucket=buckets.setdefault(key,dict(metric=key[0],is_estimate=key[1],currency=key[2],value=Decimal(0),days=set(),markets=set()))
        bucket['value']+=Decimal(str(row['value']));bucket['days'].add(row['day']);bucket['markets'].add((row['store'],row['app_id'],row['country']))
    return [dict(metric=b['metric'],is_estimate=b['is_estimate'],currency=b['currency'],value=str(b['value']),days=len(b['days']),markets=len(b['markets'])) for b in buckets.values()]
