"""OceanBase queue selection: paginate candidates, then lock exact identities.

OceanBase applies LIMIT before SKIP LOCKED. Never use a limited locking scan
to infer that a queue is empty. Callers own the transaction and lease update.
"""
import re


class Rows:
    def __init__(self, rows): self.rows = rows
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return self.rows


def _lock_identities(db,base,args,alias,keys,identities):
    # For a single-market details batch this becomes equality on the store,
    # country and language plus one IN on app_id. Preserve exact tuples for
    # mixed compound keys so no other identity is locked.
    predicate = []
    params = list(args)
    varying = []
    for pos,key in enumerate(keys):
        values = {identity[pos] for identity in identities}
        if len(values)==1:
            predicate.append(f'{alias}.{key}=%s')
            params.append(identities[0][pos])
        else:
            varying.append(pos)
    if varying:
        cols = [f'{alias}.{keys[pos]}' for pos in varying]
        if len(cols)==1:
            predicate.append(cols[0]+' IN %s')
            params.append(tuple(identity[varying[0]] for identity in identities))
        else:
            predicate.append('('+','.join(cols)+') IN %s')
            params.append(tuple(tuple(identity[pos] for pos in varying) for identity in identities))
    locked = db.execute(base+' AND '+' AND '.join(predicate)+' FOR UPDATE SKIP LOCKED',params).fetchall()
    return {tuple(r[k] for k in keys):r for r in locked}


def select(db, sql, args=(), *, candidate_hint=None):
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
    # Candidate scans only need the cursor and identity. Fetching full payloads
    # before LIMIT prevents covering-index scans on a multi-million-row queue.
    hint=('/*+ '+candidate_hint+' */ ') if candidate_hint else ''
    candidates_base='SELECT '+hint+','.join(order_columns)+' '+base[source.start():]
    # Retry indexes accelerate discovery, but make poor locking plans after all
    # primary key values are known. Keep hints on candidate scans only.
    lock_base = re.sub(rf'(\bFROM\s+{table}\b(?:\s+{alias}\b)?\s+)FORCE INDEX\s*\(\w+\)',
                       r'\1FORCE INDEX (PRIMARY)',base,count=1,flags=re.I)
    after, selected, seen = None, [], set()
    while len(selected) < count:
        suffix, params = '', list(args)
        if after is not None:
            suffix = ' AND ('+','.join(order_columns)+')>('+','.join(['%s']*len(after))+')'
            params.extend(after)
        candidates = db.execute(candidates_base+suffix+' ORDER BY '+','.join(order_columns)+' LIMIT 64',params).fetchall()
        if not candidates: break
        # Lock at most the remaining capacity. LIMIT on a locking scan would
        # discard locked candidates before SKIP LOCKED can advance past them.
        offset = 0
        while offset < len(candidates) and len(selected) < count:
            batch = candidates[offset:offset+count-len(selected)]
            offset += len(batch)
            identities = [pk for r in batch if (pk:=tuple(r[k] for k in keys)) not in seen]
            if not identities:continue
            by_id = _lock_identities(db,lock_base,args,alias,keys,identities)
            selected.extend(by_id[identity] for identity in identities if identity in by_id)
            seen.update(by_id)
        after = tuple(candidates[-1][c.split('.')[-1]] for c in order_columns)
    return Rows(selected)
