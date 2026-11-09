"""OceanBase queue selection: paginate candidates, then lock exact identities.

OceanBase applies LIMIT before SKIP LOCKED. Never use a limited locking scan
to infer that a queue is empty. Callers own the transaction and lease update.
"""
import re


class Rows:
    def __init__(self, rows): self.rows = rows
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return self.rows


def select(db, sql, args=()):
    match = re.fullmatch(r'(.*)\s+ORDER BY\s+(.+?)\s+LIMIT\s+(%s|\d+)\s+FOR UPDATE SKIP LOCKED\s*', sql.strip(), re.I | re.S)
    if not match:
        raise ValueError('Queue claims require an ordered, limited SELECT')
    base, order, limit = match.groups()
    args = list(args or ())
    count = int(args.pop() if limit == '%s' else limit)
    source = re.search(r'\bFROM\s+(\w+)(?:\s+(\w+))?', base, re.I)
    table, alias = source.groups()
    if alias is None or alias.upper() in ('WHERE', 'FORCE', 'JOIN'): alias = table
    keys = db.primary_keys(table)
    order_columns = [x.strip() for x in order.split(',')]
    if not all(re.fullmatch(r'\w+(?:\.\w+)?', c) for c in order_columns):
        raise ValueError('Queue order must use ascending columns')
    order_columns += [alias+'.'+k for k in keys if k not in [c.split('.')[-1] for c in order_columns]]
    after, selected = None, []
    while len(selected) < count:
        suffix, params = '', list(args)
        if after is not None:
            suffix = ' AND ('+','.join(order_columns)+')>('+','.join(['%s']*len(after))+')'
            params.extend(after)
        candidates = db.execute(base+suffix+' ORDER BY '+','.join(order_columns)+' LIMIT 64',params).fetchall()
        if not candidates: break
        for row in candidates:
            predicate = ' AND '+' AND '.join(alias+'.'+k+'=%s' for k in keys)
            current = db.execute(base+predicate+' FOR UPDATE SKIP LOCKED',(*args,*[row[k] for k in keys])).fetchone()
            if current is not None:
                selected.append(current)
                if len(selected) == count: break
        after = tuple(candidates[-1][c.split('.')[-1]] for c in order_columns)
    return Rows(selected)
