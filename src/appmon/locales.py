import json
import re
from importlib.resources import files

PREFERENCES=json.loads(files('appmon').joinpath('market_locales.json').read_text(encoding='utf-8'))


def language_for(store,country,value='auto'):
    value=str(value or 'auto').lower().replace('_','-')
    if value=='auto':return PREFERENCES['countries'].get(country.lower(),{}).get(store,{}).get('default','en')
    if not re.fullmatch(r'[a-z]{2,3}(?:-[a-z]{4})?(?:-(?:[a-z]{2}|[0-9]{3}))?',value):raise ValueError('请求语言代码无效')
    if value.split('-')[0] not in PREFERENCES['languages'] or value.split('-')[0] in ('und','zxx'):
        raise ValueError('请求语言不是国家代码，请选择“自动匹配”或有效语言，例如 en、kk、ru')
    return value


def stores(value):
    if value in ('',None,'all'):return ['google','apple']
    result=list(dict.fromkeys(value.split(',')))
    if not result or any(v not in ('google','apple') for v in result):raise ValueError('未知应用商店')
    return result


def where(store='',country='',language='',alias=''):
    prefix=alias+'.' if alias else '';selected=stores(store)
    clauses=[prefix+'store IN ('+','.join('%s' for _ in selected)+')'];args=list(selected)
    if country:clauses.append(prefix+'country=%s');args.append(country.lower())
    if language:
        if language=='auto' and country:
            clauses.append('('+' OR '.join(f'({prefix}store=%s AND {prefix}language=%s)' for _ in selected)+')')
            for name in selected:args.extend([name,language_for(name,country)])
        elif language!='auto':clauses.append(prefix+'language=%s');args.append(language_for(selected[0],country,language))
    return ' AND '.join(clauses),args
