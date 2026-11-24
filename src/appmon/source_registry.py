"""Admin-editable source bindings; credentials stay in the encrypted account store."""
import json,time,os,re
from pathlib import Path
from .db import dump
from .batch_limits import APPLE_DETAIL_MAX,GOOGLE_RPC_MAX

CATALOG={
 'apple_lookup':dict(name='Apple 应用详情',origin='https://itunes.apple.com/lookup',access='公开接口',fields='应用详情、开发者、评分、素材地址',batch_max=APPLE_DETAIL_MAX),
 'google_play':dict(name='Google Play',origin='https://play.google.com',access='公开商店',fields='详情、榜单、评论、关键词、权限',batch_max=GOOGLE_RPC_MAX,batch_scope='应用详情；其他类型保持原验证批量'),
 'apple_rss':dict(name='Apple 榜单与评论',origin='https://itunes.apple.com',access='公开 RSS',fields='榜单及近期评论；不保证全历史'),
 'apple_search':dict(name='Apple 搜索',origin='https://itunes.apple.com/search',access='公开接口',fields='关键词搜索结果与排名'),
 'apple_today':dict(name='Apple Today',origin='https://apps.apple.com',access='公开页面',fields='可见编辑推荐'),
 'store_cdn':dict(name='商店图片 CDN',origin='https://is1.mzstatic.com / https://play-lh.googleusercontent.com',access='公开素材',fields='关注应用及当前榜单 Top50 图片；视频仅来源链接'),
 'appgoblin_seed':dict(name='AppGoblin 免费数据包',origin='https://github.com/appgoblin-dev/appgoblin-data',access='公开批量文件 · CC BY 4.0',fields='应用目录、开发者、更新时间、广告/内购、安装与评分数量；字段依文件而异'),
 'community':dict(name='公开元数据',access='公开页面 / 账户',fields='由来源实际授权范围决定',editable_origin=True),
 'directory':dict(name='安装指标',access='公开页面 / 账户',fields='Android 安装参考指标',editable_origin=True),
 'marketdata':dict(name='商业市场数据',access='账户 / Token',fields='授权的下载、收入、历史等；不视为免费全量',editable_origin=True),
 'web_feed':dict(name='网页趋势 RSS',access='公开 RSS',fields='关键词趋势，不替代应用完整详情',editable_origin=True),
 'apple_ads':dict(name='Apple Ads',origin='https://api.ads.apple.com/v1/insights',access='授权 API',fields='授权关键词热度；不提供全库全字段',editable_token=True),
 'ad_google':dict(name='Google 广告透明度中心',origin='https://adstransparency.google.com',access='公开资料库',fields='开发者域名广告样本、素材与已核实的商店链接'),
 'ad_apple':dict(name='Apple 广告资料库',origin='https://adrepository.apple.com',access='公开 API',fields='支持地区近 90 天的 App Store 广告与素材'),
 'ad_creatives':dict(name='AppGoblin 推广素材与广告主榜',origin='https://appgoblin.info',access='公开页面',fields='Google、Yandex 等广告网络的应用推广素材和广告主排名样本'),
 'ad_meta':dict(name='Meta / Instagram 广告资料库',origin='https://graph.facebook.com',access='Ad Library API 授权',fields='按实际发布平台归属的广告文案与原始广告页',editable_token=True,account_id_required=False),
 'ad_tiktok':dict(name='TikTok 商业内容库',origin='https://open.tiktokapis.com',access='Commercial Content API 授权',fields='按下载链接确认应用的广告、图片、视频和落地页',editable_token=True,account_id_required=False),
 'ad_reddit':dict(name='Reddit Ads',origin='https://ads-api.reddit.com',access='广告账户只读授权',fields='授权账户中匹配应用的广告、推广帖、媒体与目标链接',editable_token=True,account_id_required=True),
}

def migrate(db,cfg):
    """Idempotently import nonsecret legacy endpoints. Never overwrite admin edits."""
    path=Path(cfg['data_dir'])/'secrets'/'source-profiles.json'
    legacy=cfg.get('source_profiles')
    if legacy is None:legacy=json.loads(path.read_text(encoding='utf8')) if path.exists() else {}
    from .source_profiles import validate
    for key,definition in CATALOG.items():
        value={}
        if definition.get('editable_origin') and legacy.get(key):
            binding=validate(legacy[key]);value={'origin':binding['origin'],'hosts':binding['hosts']}
        if key in ('apple_lookup','google_play'):
            value['batch_size']=cfg.get('apple_lookup_batch_size' if key=='apple_lookup' else 'google_rpc_batch_size',50 if key=='apple_lookup' else 20)
        db.execute('INSERT IGNORE INTO data_source_configs VALUES(%s,%s,%s)',(key,dump(value),time.time()))
    token=os.environ.get('APPLE_ADS_ACCESS_TOKEN');account=os.environ.get('APPLE_ADS_AD_ACCOUNT_ID')
    if token and account and not db.execute("SELECT 1 FROM source_accounts WHERE provider='apple_ads'").fetchone():
        save_ads(db,cfg,{'access_token':token,'ad_account_id':account},only_missing=True)

def rows(db):
    return {r['name']:json.loads(r['settings']) for r in db.execute('SELECT name,settings FROM data_source_configs').fetchall()}

def report(db,cfg):
    migrate(db,cfg);saved=rows(db)
    accounts={r['provider']:dict(configured=True,status=r['status'],checked_at=r['checked_at']) for r in db.execute('SELECT provider,status,checked_at FROM source_accounts').fetchall()}
    for name in ('apple_ads','ad_reddit'):
        if accounts.get(name):
            try:saved[name]=saved.get(name,{})|{'ad_account_id':ads_credentials(db,cfg,name)['ad_account_id']}
            except ValueError:accounts[name]['status']='key_unavailable'
    return [{'id':key,**definition,**saved.get(key,{}),'editable':bool(definition.get('editable_origin') or definition.get('batch_max') or definition.get('editable_token')),
        'account':accounts.get(key,{'configured':False})} for key,definition in CATALOG.items()]

def save(db,cfg,name,value):
    definition=CATALOG.get(name)
    if not definition or not isinstance(value,dict):raise ValueError('未知数据源配置')
    if definition.get('editable_token'):
        save_ads(db,cfg,value,name=name)
        return next(r for r in report(db,cfg) if r['id']==name)
    allowed={'origin','hosts'} if definition.get('editable_origin') else {'batch_size'} if definition.get('batch_max') else set()
    if not allowed or set(value)-allowed:raise ValueError('此来源不支持修改这些参数')
    migrate(db,cfg)
    with db.named_lock('data-source-'+name), db:
        row=db.execute('SELECT settings FROM data_source_configs WHERE name=%s FOR UPDATE',(name,)).fetchone()
        old=json.loads(row['settings']);new=old|value
        if definition.get('editable_origin'):
            from .source_profiles import validate
            new=validate(new)
            if old.get('origin')!=new['origin']:
                # An endpoint change must never send old credentials to a new host.
                db.execute("UPDATE source_accounts SET status='endpoint_changed',checked_at=NULL WHERE provider=%s",(name,))
        else:
            size=new.get('batch_size')
            if type(size) is not int or not 1<=size<=definition['batch_max']:raise ValueError('批量大小超过已验证范围')
        db.execute('UPDATE data_source_configs SET settings=%s,updated_at=%s WHERE name=%s',(dump(new),time.time(),name))
    return next(r for r in report(db,cfg) if r['id']==name)

def collector_config(db,cfg,lane=None):
    settings=rows(db)
    overrides={}
    for name,key in [('apple_lookup','apple_lookup_batch_size'),('google_play','google_rpc_batch_size')]:
        if name=='google_play' and lane not in ('details','pool','icons'):continue
        size=settings.get(name,{}).get('batch_size')
        if type(size) is int and 1<=size<=CATALOG[name]['batch_max']:overrides[key]=size
    return cfg|overrides


def ads_credentials(db,cfg,name='apple_ads'):
    row=db.execute("SELECT secret FROM source_accounts WHERE provider=%s",(name,)).fetchone()
    if not row:
        return {'access_token':os.environ.get('APPLE_ADS_ACCESS_TOKEN',''),'ad_account_id':os.environ.get('APPLE_ADS_AD_ACCOUNT_ID','')} if name=='apple_ads' else {}
    from .data_sources import cipher
    try:return json.loads(cipher(cfg).decrypt(row['secret'].encode()))
    except Exception:raise ValueError('广告来源凭据密钥不可用') from None


def save_ads(db,cfg,value,only_missing=False,name='apple_ads'):
    needs_account=CATALOG[name].get('account_id_required',True)
    if set(value)-({'access_token','ad_account_id'} if needs_account else {'access_token'}):raise ValueError('未知广告来源参数')
    if 'access_token' in value and not isinstance(value['access_token'],str):raise ValueError('访问令牌须为文本')
    from .data_sources import cipher
    with db.named_lock('data-source-'+name):
        if only_missing and db.execute("SELECT 1 FROM source_accounts WHERE provider=%s",(name,)).fetchone():return
        old=ads_credentials(db,cfg,name)
        token=value.get('access_token') or old.get('access_token','');account=value.get('ad_account_id',old.get('ad_account_id',''))
        if needs_account:
            pattern = r'(?:t2|a2)_[A-Za-z0-9]+' if name=='ad_reddit' else r'[0-9]+'
            if not isinstance(account,str) or not re.fullmatch(pattern,account):raise ValueError('广告账户 ID 格式无效')
        if not isinstance(token,str) or not token or len(token)>8192 or any(ch.isspace() or ord(ch)<32 for ch in token):raise ValueError('访问令牌格式无效')
        with db.named_lock('data-sources-key'):
            encryption=cipher(cfg,create=not db.execute('SELECT 1 FROM source_accounts LIMIT 1').fetchone())
        credentials = {'access_token':token}
        if needs_account:credentials['ad_account_id']=account
        secret=encryption.encrypt(dump(credentials).encode()).decode()
        db.execute("INSERT INTO source_accounts(provider,secret,status,updated_at) VALUES(%s,%s,'saved',%s) ON DUPLICATE KEY UPDATE secret=VALUES(secret),status='saved',updated_at=VALUES(updated_at)",(name,secret,time.time()))
