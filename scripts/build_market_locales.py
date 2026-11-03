"""Build reviewed locale recommendations from the project's pinned CLDR archives."""
import json
import tarfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def build():
    with tarfile.open(ROOT/'work/catalog-sources/cldr-core.tgz') as archive:
        territories=json.load(archive.extractfile('package/supplemental/territoryInfo.json'))['supplemental']['territoryInfo']
    with tarfile.open(ROOT/'work/catalog-sources/cldr-localenames-full.tgz') as archive:
        names=json.load(archive.extractfile('package/main/zh/languages.json'))['main']['zh']['localeDisplayNames']['languages']
    google=set('af sq am ar hy az bn eu be bg my ca zh hr cs da nl en et fil fi fr gl ka de el gu he hi hu is id it ja kn kk km ko ky lo lv lt mk ms ml mr mn ne no fa pl pt pa ro rm ru sr si sk sl es sw sv ta te th tr uk ur vi'.split())
    apple=set('ar bn ca zh hr cs da nl en fi fr de el gu he hi hu id it ja kn ko ms ml mr no or pl pt pa ro ru sk sl es sv ta te th tr uk ur vi'.split())
    overrides={'kz':'kk','ph':'fil','cn':'zh-cn','tw':'zh-tw','hk':'zh-tw','mo':'zh-tw','br':'pt-br'}
    labels={k.replace('_','-').lower():v for k,v in names.items() if len(k.split('-')[0]) in (2,3) and '-alt-' not in k}
    labels.update({'zh-cn':'简体中文','zh-tw':'繁体中文','zh-hk':'繁体中文（香港）','pt-br':'葡萄牙语（巴西）','iw':'希伯来语'})
    countries={}
    for row in json.loads((ROOT/'src/appmon/store_catalog.json').read_text(encoding='utf-8'))['countries']:
        code=row['code'];data=territories.get(code.upper(),{}).get('languagePopulation',{})
        priority={'official':0,'de_facto_official':1,'official_regional':2}
        ordered=sorted(data,key=lambda l:(priority.get(data[l].get('_officialStatus'),3),-float(data[l].get('_populationPercent',0))))
        candidates=[]
        for lang in ([overrides[code]] if code in overrides else [])+ordered+['en']:
            base=lang.split('_')[0].split('-')[0]
            candidate=lang if lang in ('zh-cn','zh-tw','pt-br') else ('zh-tw' if lang=='zh_Hant' else 'zh-cn' if base=='zh' else base)
            if candidate not in candidates:candidates.append(candidate)
        entry={}
        for store,supported in [('google',google),('apple',apple)]:
            options=[l for l in candidates if l.split('-')[0] in supported][:4]
            if not options:options=['en']
            entry[store]={'default':options[0],'options':options}
        countries[code]=entry
    result={'version':'2026-11-03','sources':['Unicode CLDR 49 territoryInfo / Chinese language names',
        'https://support.google.com/googleplay/android-developer/answer/9844778',
        'https://developer.apple.com/help/app-store-connect/reference/app-information/app-store-localizations/'],
        'note':'按地区语言及商店本地化能力推荐请求语言；不是对实际返回语言的保证。','languages':labels,'countries':countries}
    (ROOT/'src/appmon/market_locales.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Wrote market locale recommendations for',len(countries),'regions')


if __name__=='__main__':build()
