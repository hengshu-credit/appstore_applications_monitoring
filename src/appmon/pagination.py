"""Collect unique records until the requested depth or a real source boundary."""
from .stores import FetchError


class PageWindow:
    def __init__(self, depth, id_field='id', max_pages=100):
        self.depth,self.id_field,self.max_pages=depth,id_field,max_pages
        self.items,self.seen,self.tokens=[],set(),set()
        self.cursor,self.pages,self.empty_pages,self.end=None,0,0,None

    def add(self, rows, next_cursor):
        if next_cursor is not None and (not isinstance(next_cursor,str) or not next_cursor):
            raise FetchError('分页游标格式异常，未完成采集')
        before=len(self.items)
        for row in rows:
            identifier=row.get(self.id_field)
            if not isinstance(identifier,str) or not identifier:
                raise FetchError('分页记录缺少唯一标识，未完成采集')
            if identifier not in self.seen:
                self.seen.add(identifier);self.items.append(row)
        self.pages+=1
        self.empty_pages=self.empty_pages+1 if len(self.items)==before else 0
        if len(self.items)>=self.depth:
            self.items=self.items[:self.depth];self.end='depth_reached'
        elif next_cursor is None:
            self.end='source_exhausted'
        elif next_cursor in self.tokens:
            raise FetchError(f'分页游标循环，已取得 {len(self.items)}/{self.depth} 条，未完成采集')
        elif self.empty_pages>=3 or self.pages>=self.max_pages:
            raise FetchError(f'分页未正常结束，已取得 {len(self.items)}/{self.depth} 条，未完成采集')
        if next_cursor:
            self.tokens.add(next_cursor)
        self.cursor=next_cursor
        return self.end is not None

    def summary(self):
        return {'requested':self.depth,'collected':len(self.items),'pages':self.pages,'end':self.end}


def completion_note(result):
    info=getattr(result,'pagination',None)
    if not info:
        return ''
    reason={'depth_reached':'达到目标数量','source_exhausted':'来源未提供下一页',
            'source_limit':'达到来源窗口上限，非历史全量'}.get(info['end'],'来源返回范围')
    return f"取得 {info['collected']}/{info['requested']} 条；{info['pages']} 次请求；{reason}"
