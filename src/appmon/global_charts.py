"""Country-by-country store charts on one page; no synthetic worldwide ranking."""
import time
import hashlib
from . import charts, catalog, locales
from .db import dump
from .growth import cutoff
from .intelligence import hours

DEFAULT_COUNTRIES=['us','cn','jp','kr','gb','de','fr','in','br','th','vn','id','kz','mx','ca','au']


def countries(store,selection=''):
    if store not in ('google','apple','all'):raise ValueError('请选择有效的应用商店')
    if not isinstance(selection,str) or len(selection)>2000:raise ValueError('国家选择无效')
    known={c['code']:c for c in catalog.CATALOG['countries'] if store!='apple' or c['code'] in catalog.APPLE_COUNTRIES}
    chosen=list(known) if selection=='all' else list(dict.fromkeys(selection.lower().split(','))) if selection else [c for c in DEFAULT_COUNTRIES if c in known and (store!='google' or c!='cn')]
    if not chosen or any(c not in known for c in chosen):raise ValueError('国家列表包含该商店未配置的市场')
    return [known[c] for c in chosen]


def collections(store,value):
    if not isinstance(value,str):raise ValueError('榜单选择无效')
    values=list(dict.fromkeys(value.split(',')))
    if not 1<=len(values)<=7:raise ValueError('请选择1–7种榜单')
    for item in values:charts.definition(store,item)
    return values


def report(db,store,selection='',collection='top-free,top-paid,top-grossing',category='',as_of=None,depth=10,offset=0,page_size=6):
    if type(depth) is not int or not 1<=depth<=50 or not 0<=offset<=250 or not 1<=page_size<=12:raise ValueError('榜单深度或分页参数无效')
    if store=='all':
        if category:raise ValueError('同时选择多个商店时，请分别查看各商店官方分类')
        selected=countries(store,selection);page=selected[offset:offset+page_size];parts={};types=collections('google',collection)
        at=cutoff(as_of)
        for platform in ('google','apple'):
            eligible=[c['code'] for c in page if platform!='apple' or c['code'] in catalog.APPLE_COUNTRIES]
            parts[platform]=report(db,platform,','.join(eligible),collection,as_of=as_of,depth=depth,page_size=page_size) if eligible else None
        result=[]
        for country in page:
            for platform,part in parts.items():
                row=next((c for c in part['countries'] if c['country']==country['code']),None) if part else None
                if row is None:
                    row={'country':country['code'],'name':country['name'],'language':'','boards':[
                        {'collection':kind,'definition':charts.definition(platform,kind),'items':[],
                         'observed_at':None,'returned_count':0,'fresh':False,'unsupported':True,
                         'quality':'unsupported','reason':'App Store 暂未支持此地区'} for kind in types]}
                result.append(row|{'store':platform})
        return {'store':store,'countries':result,'selected_codes':[c['code'] for c in selected],
            'total_countries':len(selected),'offset':offset,'page_size':page_size,'page_country_count':len(page),
            'has_more':offset+page_size<len(selected),'fresh_boards':sum(p['fresh_boards'] for p in parts.values() if p),
            'observed_boards':sum(p['observed_boards'] for p in parts.values() if p),'requested_boards':len(result)*len(types),
            'as_of':at,'note':'各国家、各商店榜单独立展示，保留原始名次。未支持、未采集和过期状态分别标注。',
            'available_countries':countries(store,'all')}
    selected=countries(store,selection);types=collections(store,collection);at=cutoff(as_of)
    if category:catalog.category_aliases(store,category)
    result=[];cache={};fresh=0;observed=0
    for c in selected[offset:offset+page_size]:
        language=locales.language_for(store,c['code']);boards=[]
        for kind in types:
            data=charts.view(db,store,c['code'],language,kind,category,cache,as_of=at if as_of is not None else None)
            current=data['latest_success'];target=data['target']
            is_fresh=bool(current and target and data.get('ready',True) and at-current['observed_at']<=target['refresh_hours']*3600)
            if as_of is None:is_fresh=is_fresh and not target['failures'] and bool(target['enabled']) if target else False
            observed+=int(current is not None);fresh+=int(is_fresh)
            boards.append({'collection':kind,'definition':data['definition'],'items':data['items'][:depth],
                'observed_at':current['observed_at'] if current else None,'source':current['source'] if current else None,
                'returned_count':data['base_returned_count'],'fresh':is_fresh,'target_id':target['id'] if target else None,
                'reason':data['reason'],'unsupported':data.get('unsupported',False),
                'quality':data.get('quality','unsupported'),'lease_until':target['lease_until'] if target else None,
                'last_error':target['last_error'] if target and as_of is None else None})
        result.append({'country':c['code'],'name':c['name'],'language':language,'boards':boards})
    return {'store':store,'countries':result,'selected_codes':[c['code'] for c in selected],'total_countries':len(selected),'offset':offset,'page_size':page_size,
        'has_more':offset+page_size<len(selected),'fresh_boards':fresh,'observed_boards':observed,'requested_boards':len(result)*len(types),
        'as_of':at,'note':'各国家商店榜单并列展示，保留原始名次；不把国家排名相加成全球官方排名。未采集、失败和过期单独显示。',
        'available_countries':countries(store,'all')}


def preview(value):
    store=value.get('store');selected=countries(store,value.get('countries',''))
    if store=='all':
        if value.get('category'):raise ValueError('同时选择多个商店时，请分别查看各商店官方分类')
        actions=[];unsupported=[];note=''
        for platform in ('google','apple'):
            eligible=[c['code'] for c in selected if platform!='apple' or c['code'] in catalog.APPLE_COUNTRIES]
            types=collections(platform,value.get('collections','top-free,top-paid,top-grossing'))
            unsupported.extend({'store':platform,'country':c['code'],'collection':kind}
                for c in selected if c['code'] not in eligible for kind in types)
            if not eligible:continue
            part=preview(value|{'store':platform,'countries':','.join(eligible)})
            actions.extend(part['actions']);unsupported.extend(part['unsupported']);note=part['note']
        return {'actions':actions,'total':len(actions),'countries':len(selected),'unsupported':unsupported,'note':note}
    types=collections(store,value.get('collections','top-free,top-paid,top-grossing'));category=value.get('category','')
    if not isinstance(category,str) or len(category)>64:raise ValueError('分类无效')
    if category and category not in catalog.BY_ID[store] and not(store=='google' and category=='APPLICATION'):raise ValueError('请选择商店官方分类')
    depth=value.get('depth',100);interval=hours(value.get('refresh_hours',24))
    if type(depth) is not int or not 1<=depth<=200:raise ValueError('采集深度须为1–200')
    actions={};unsupported=[]
    for c in selected:
        for kind in types:
            spec=charts.definition(store,kind)
            if category and not spec['category_supported']:
                unsupported.append({'country':c['code'],'collection':kind});continue
            key=(c['code'],spec['base_collection'])
            actions[key]={'kind':'chart','preserve_existing':True,'value':{'kind':'chart','store':store,'country':c['code'],
                'language':locales.language_for(store,c['code']),'collection':spec['base_collection'],'category':category,'depth':depth,'refresh_hours':interval}}
    return {'actions':list(actions.values()),'total':len(actions),'countries':len(selected),'unsupported':unsupported,
            'note':'创建周期采集配置，完成入队不代表所有国家已采集成功。已有目标的暂停状态、深度和周期保留。'}


def create_plan(db,value):
    plan=preview(value)
    if not plan['actions']:raise ValueError('所选组合没有可采集的基榜')
    # Preserve at execution time, including settings edited after this preview.
    canonical=dump({'actions':sorted(plan['actions'],key=lambda a:dump(a['value']))})
    lock='global-plan:'+hashlib.sha256(canonical.encode()).hexdigest()[:40]
    now=time.time()
    with db.named_lock(lock):
        with db:
            old=db.execute("SELECT id,total,status FROM collection_plans WHERE name=%s AND specification=%s AND status IN ('queued','running') ORDER BY id DESC LIMIT 1",('全球榜单 / '+value['store'],canonical)).fetchone()
            if old:return old|{'reused':True,'note':plan['note']}
            db.execute('INSERT INTO collection_plans(name,specification,actions,total,created_at,updated_at) VALUES(%s,%s,%s,%s,%s,%s)',
                       ('全球榜单 / '+value['store'],canonical,dump(plan['actions']),plan['total'],now,now))
            identifier=db.cursor.lastrowid
    return {'id':identifier,'total':plan['total'],'status':'queued','note':plan['note']}
