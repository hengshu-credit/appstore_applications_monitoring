"""Split verified read-only batches on size limits and keep completed parts."""
from .stores import FetchError, CollectionDeferred


def cooling(error):
    if isinstance(error,CollectionDeferred):
        return True
    if not isinstance(error,FetchError):
        return False
    status = error.details.get('status_code')
    if not isinstance(status,int) and str(error).startswith('HTTP '):
        code = str(error).split()[1:2]
        status = int(code[0]) if code and code[0].isdigit() else 0
    return isinstance(status,int) and (status in (403,429) or status>=500)


def read_sized(items, capacity, protocol, window, read, *, max_splits):
    """read(part) returns aligned values and raw bytes. Only actual attempts fail."""
    if capacity<1:raise ValueError('Batch capacity must be positive')
    initial=window.limit(protocol,capacity)
    attempts_left=max((1 << (max_splits+1))-1,(len(items)+initial-1)//initial)

    def fetch(part, remaining_splits):
        nonlocal attempts_left
        if not attempts_left:
            raise CollectionDeferred('拆包请求已达本批上限，剩余目标按收窄后的批量重新领取',1)
        attempts_left-=1
        try:
            return read(part)
        except FetchError as exc:
            oversized = str(exc) in ('HTTP 413','HTTP 414') or exc.details.get('batch_budget')
            if not oversized or len(part)<2:
                raise
            window.too_large(protocol,len(part))
            if remaining_splits==0:
                raise CollectionDeferred('响应仍超过预算，按收窄后的批量重新领取',1) from exc
        return portions(part,remaining_splits-1)

    def portions(batch, remaining_splits):
        values, bodies = [], []
        while len(values)<len(batch):
            size = window.limit(protocol,capacity)
            part = batch[len(values):len(values)+size]
            try:
                results,raw = fetch(part,remaining_splits)
            except FetchError as exc:
                results,raw = [exc]*len(part),b''
            if len(results)!=len(part):
                raise FetchError('批次结果数量异常')
            values.extend(results)
            if raw:bodies.append(raw)
            stop = next((result for result in results if cooling(result)),None)
            if stop is not None:
                values.extend([CollectionDeferred('等待共享限速窗口，剩余目标稍后领取',max(1,stop.retry_after))]*(len(batch)-len(values)))
                break
        return values,b'\n'.join(bodies)

    if len(items)<=initial:
        return fetch(items,max_splits)
    return portions(items,max_splits)
