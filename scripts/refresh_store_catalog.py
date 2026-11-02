"""Rebuild offline country/flag assets and store categories from pinned sources.

Run deliberately when reviewing upstream taxonomy changes, never at app startup.
"""
import hashlib
import json
import re
import tarfile
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'work/catalog-sources'
APPLE_URL = 'https://itunes.apple.com/WebObjects/MZStoreServices.woa/ws/genres?id=36'

GOOGLE_APPS = '''ART_AND_DESIGN|艺术与设计|Art & Design
AUTO_AND_VEHICLES|汽车与交通工具|Auto & Vehicles
BEAUTY|美容时尚|Beauty
BOOKS_AND_REFERENCE|图书与工具书|Books & Reference
BUSINESS|商务|Business
COMICS|动漫|Comics
COMMUNICATION|通讯|Communication
DATING|交友|Dating
EDUCATION|教育|Education
ENTERTAINMENT|娱乐|Entertainment
EVENTS|活动|Events
FINANCE|财经|Finance
FOOD_AND_DRINK|食品与饮料|Food & Drink
HEALTH_AND_FITNESS|健康与健身|Health & Fitness
HOUSE_AND_HOME|家居|House & Home
LIBRARIES_AND_DEMO|软件库与演示|Libraries & Demo
LIFESTYLE|生活时尚|Lifestyle
MAPS_AND_NAVIGATION|地图和导航|Maps & Navigation
MEDICAL|医疗|Medical
MUSIC_AND_AUDIO|音乐和音频|Music & Audio
NEWS_AND_MAGAZINES|新闻与杂志|News & Magazines
PARENTING|育儿|Parenting
PERSONALIZATION|个性化|Personalization
PHOTOGRAPHY|摄影|Photography
PRODUCTIVITY|工作效率|Productivity
SHOPPING|购物|Shopping
SOCIAL|社交|Social
SPORTS|体育|Sports
TOOLS|工具|Tools
TRAVEL_AND_LOCAL|旅游与本地出行|Travel & Local
VIDEO_PLAYERS|视频播放器与编辑器|Video Players & Editors
WATCH_FACE|表盘（Wear OS）|Watch Faces
WEATHER|天气|Weather'''
GAME_LABELS = {'Action':'动作','Adventure':'冒险','Arcade':'街机','Board':'桌面和棋类','Card':'卡牌',
    'Casino':'娱乐场','Casual':'休闲','Dice':'骰子','Educational':'教育','Family':'家庭','Music':'音乐',
    'Puzzle':'益智解谜','Racing':'竞速','Roleplaying':'角色扮演','Role Playing':'角色扮演',
    'Simulation':'模拟','Sports':'体育','Strategy':'策略','Trivia':'知识问答','Word':'文字'}
APPLE_LABELS = dict(zip(['6000','6001','6002','6003','6004','6005','6006','6007','6008','6009','6010','6011',
    '6012','6013','6014','6015','6016','6017','6018','6020','6021','6022','6023','6024','6025','6026','6027'],
    ['商务','天气','工具','旅游','体育','社交','参考资料','效率','摄影与录像','新闻','导航','音乐','生活','健康健美',
     '游戏','财务','娱乐','教育','图书','医疗','报刊杂志','商品指南（历史分类）','美食佳饮','购物','贴纸（iMessage）','开发者工具','图形与设计']))


def package(name, version):
    target = SOURCE / (name + '.tgz')
    url = f'https://registry.npmjs.org/{name}/-/{name}-{version}.tgz'
    if not target.exists():
        response = requests.get(url, timeout=90)
        response.raise_for_status()
        target.write_bytes(response.content)
    return tarfile.open(target), {'url': url, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}


def aliases(identifier, name, zh, extras=()):
    values = {identifier, name, zh, *extras}
    for value in list(values):
        values.update([value.lower(), value.upper(), value.lower().replace(' & ', '_and_').replace(' ', '_'),
                       value.lower().replace(' & ', '_and_')])
    return sorted(values)


def main():
    SOURCE.mkdir(parents=True, exist_ok=True)
    core, core_source = package('cldr-core', '49.0.0')
    names, names_source = package('cldr-localenames-full', '49.0.0')
    flags, flag_source = package('flag-icons', '7.6.0')
    containment = json.load(core.extractfile('package/supplemental/territoryContainment.json'))['supplemental']['territoryContainment']
    mappings = json.load(core.extractfile('package/supplemental/codeMappings.json'))['supplemental']['codeMappings']
    locales = {locale: json.load(names.extractfile(f'package/main/{locale}/territories.json'))['main'][locale]['localeDisplayNames']['territories'] for locale in ('zh', 'en')}
    def leaves(code):
        return {v for child in containment.get(code, {}).get('_contains', []) for v in (leaves(child) if child.isdigit() else {child})}
    continents = [('asia','亚洲','142'),('north_america','北美洲','003'),('south_america','南美洲','005'),
                  ('europe','欧洲','150'),('africa','非洲','002'),('oceania','大洋洲','009'),('antarctica','南极洲','010')]
    regions = [('east_asia','东亚','030'),('southeast_asia','东南亚','035'),('south_asia','南亚','034'),
               ('central_asia','中亚','143'),('west_asia','西亚','145')]
    countries = []
    for code in sorted(mappings):
        numeric = mappings[code].get('_numeric', '')
        if len(code) != 2 or not numeric.isdigit() or int(numeric) >= 900 or code not in locales['en']:
            continue
        flag_path = f'package/flags/4x3/{code.lower()}.svg'
        if flag_path not in flags.getnames():
            continue
        continent = next((c[0] for c in continents if code in leaves(c[2])), 'antarctica' if code == 'AQ' else None)
        if not continent:
            continue
        label = {'HK':'中国香港','MO':'中国澳门','TW':'中国台湾'}.get(code, locales['zh'].get(code, locales['en'][code]))
        countries.append({'code':code.lower(),'name':label,'english':locales['en'][code],
            'continent':continent,'region':next((r[0] for r in regions if code in leaves(r[2])), ''),
            'aliases':[code, mappings[code].get('_alpha3',''), locales['zh'].get(code,''), locales['en'][code]]})
        out = ROOT / 'frontend/public/flags' / (code.lower() + '.svg')
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(flags.extractfile(flag_path).read())
    license_dir = ROOT / 'frontend/public/licenses'
    license_dir.mkdir(parents=True, exist_ok=True)
    (license_dir / 'unicode-cldr.txt').write_bytes(core.extractfile('package/LICENSE').read())
    (license_dir / 'flag-icons.txt').write_bytes(flags.extractfile('package/LICENSE').read())

    apple_path = SOURCE / 'apple-genres.json'
    if not apple_path.exists():
        response = requests.get(APPLE_URL, timeout=45)
        response.raise_for_status()
        apple_path.write_bytes(response.content)
    apple_tree = json.loads(apple_path.read_bytes())['36']['subgenres']
    categories = {'apple': [], 'google': []}
    for identifier, genre in sorted(apple_tree.items()):
        if identifier not in APPLE_LABELS:
            raise ValueError('Unreviewed Apple genre: ' + identifier)
        name, zh = genre['name'], APPLE_LABELS[identifier]
        extra = ['book'] if identifier == '6018' else []
        categories['apple'].append({'id':identifier,'name':zh,'english':name,'group':'games' if identifier=='6014' else 'apps',
            'parent_id':None,'legacy':identifier=='6022','aliases':aliases(identifier,name,zh,extra)})
        if identifier == '6014':
            for gid, game in sorted(genre['subgenres'].items()):
                name = game['name']
                zh = GAME_LABELS[name]
                extra = ['game_' + name.lower().replace(' ', '_'), 'games_' + name.lower().replace(' ', '_')]
                if gid == '7003':
                    extra += ['game_arcade','games_arcade','Arcade']
                if gid == '7014':
                    extra += ['game_role_playing','game_role playing','Role Playing']
                categories['apple'].append({'id':gid,'name':zh,'english':name,'group':'games','parent_id':'6014',
                    'legacy':False,'aliases':aliases(gid,name,zh,extra)})
    for line in GOOGLE_APPS.splitlines():
        identifier, zh, name = line.split('|')
        categories['google'].append({'id':identifier,'name':zh,'english':name,'group':'apps','parent_id':None,
                                    'legacy':False,'aliases':aliases(identifier,name,zh)})
    categories['google'].append({'id':'GAME','name':'游戏','english':'Games','group':'games','parent_id':None,'legacy':False,'aliases':['GAME','game','Games','games','游戏']})
    for name in ('Action','Adventure','Arcade','Board','Card','Casino','Casual','Educational','Music','Puzzle','Racing','Role Playing','Simulation','Sports','Strategy','Trivia','Word'):
        identifier = 'GAME_' + name.upper().replace(' ', '_')
        # Plain "Sports"/"Education" must remain app categories, never game aliases.
        categories['google'].append({'id':identifier,'name':GAME_LABELS[name],'english':name,'group':'games','parent_id':'GAME',
            'legacy':False,'aliases':aliases(identifier, identifier.lower(), '游戏 / ' + GAME_LABELS[name])})
    # Apple app and game subgenres share names such as Music and Sports. Bare
    # names belong to the top-level app category; games require explicit IDs/prefixes.
    app_aliases = {a.casefold() for c in categories['apple'] if c['group']=='apps' for a in c['aliases']}
    for c in categories['apple']:
        if c['parent_id']:
            c['aliases'] = [a for a in c['aliases'] if a.casefold() not in app_aliases]
    result = {'version':'2026-11-03','countries':countries,'continents':[{'id':a,'name':b} for a,b,_ in continents],
              'asia_regions':[{'id':a,'name':b} for a,b,_ in regions], 'popular':['cn','us','tw','hk','kr','jp','gb','de','th','vn','ph','in'],
              'categories':categories,'sources':{'countries':core_source,'names':names_source,'flags':flag_source,
                'apple':{'url':APPLE_URL,'sha256':hashlib.sha256(apple_path.read_bytes()).hexdigest()},
                'google':{'url':'https://support.google.com/googleplay/android-developer/answer/9859673?hl=zh-Hans',
                          'watch_face':'https://play.google.com/store/apps/category/WATCH_FACE'}}}
    (ROOT / 'src/appmon/store_catalog.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'countries':len(countries),'categories':{k:len(v) for k,v in categories.items()}},ensure_ascii=False))


if __name__ == '__main__':
    main()
