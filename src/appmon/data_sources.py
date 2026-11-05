"""Admin-managed source credentials and bounded authenticated requests."""
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit
import requests
from cryptography.fernet import Fernet, InvalidToken
from .db import dump

ORIGIN='https://appgoblin.info'
PROVIDERS=('appgoblin','appbrain','appfigures')
def provider_name(provider):
    if provider not in PROVIDERS:raise ValueError('未知数据源')
    return provider
def cookie_allowed(provider,domain):
    if provider=='appfigures':return False
    domain=domain.lstrip('.')
    return domain=='appgoblin.info' if provider=='appgoblin' else domain in ('appbrain.com','www.appbrain.com','developers.appbrain.com')
MESSAGES={'not_configured':'未配置账户','saved':'已保存，等待授权','authorized':'授权有效',
    'api_token_required':'账密已保存；API 采集需填写 Personal Access Token',
    'invalid_token':'API Token 无效或已过期','access_denied':'当前账户无 API 权限或额度不足',
    'invalid_credentials':'登录失败，请检查邮箱和密码','action_required':'需要在来源网站完成邮箱、验证码或二次验证',
    'rate_limited':'来源限制请求，请稍后重试','network_error':'来源连接失败，稍后可重试',
    'source_changed':'来源响应不符合预期，请检查登录方式','key_unavailable':'凭据密钥不可用，请恢复原密钥后重试'}
CATALOG=[
    {'id':'appgoblin','name':'AppGoblin','access':'公开页面 + 免费账户','url':ORIGIN+'/free-app-datasets',
     'capabilities':'全球安装指标、月收入/MAU估算、免费元数据下载','limits':'免费数据未确认提供分国家下载与收入；账户授权不等于付费API权限'},
    {'id':'appbrain','name':'AppBrain','access':'公开页面 + 免费账户','url':'https://www.appbrain.com/dev_dashboard',
     'capabilities':'Android累计安装与页面可见的近月下载指标','limits':'支持邮箱密码表单授权；本机匿名请求403，需配置后实测。验证码/SSO需要人工处理'},
    {'id':'store_public','name':'Google Play / Apple公开接口','access':'免登录','url':'https://apps.apple.com',
     'capabilities':'各国榜单、应用资料、搜索结果；Google累计安装','limits':'已接入观星采集器；不是分国家下载量或收入'},
    {'id':'appfigures','name':'Appfigures','access':'试用账户 / API Token / CSV 导出','url':'https://app.appfigures.com/account/units',
     'capabilities':'应用元数据、国家分类目录、榜单、历史排名、下载/收入估算、评论；支持断点归档与CSV数据集',
     'limits':'14天网页试用不等于第三方数据API权限；实际范围取决于套餐、Public Data API和额度。API已停用账密Basic Auth'},
    {'id':'google_trends','name':'Google Trends RSS','access':'免登录','url':'https://trends.google.com/trending',
     'capabilities':'各国网页搜索热点','limits':'已实现RSS采集；不是应用商店热搜，连通性因网络而异'},
]


def cipher(cfg,create=False):
    path=Path(cfg['data_dir'])/'secrets'/'data-sources.key'
    if not path.exists():
        if not create:raise ValueError('凭据密钥缺失，请恢复原密钥')
        path.parent.mkdir(parents=True,exist_ok=True)
        try:
            fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'wb') as stream:stream.write(Fernet.generate_key())
        except FileExistsError:pass
    return Fernet(path.read_bytes())


def status(db):
    result={'catalog':CATALOG}
    for provider in PROVIDERS:
        row=db.execute('SELECT provider,status,checked_at,retry_after,updated_at FROM source_accounts WHERE provider=%s',(provider,)).fetchone()
        result[provider]=dict(row or {'provider':provider,'status':'not_configured'})|{'configured':row is not None,
            'message':MESSAGES.get(row['status'] if row else 'not_configured','来源暂不可用')}
    return result


def save(db,cfg,value,provider="appgoblin"):
    provider_name(provider)
    email=value.get('email');password=value.get('password')
    token=value.get('access_token','') if provider=='appfigures' else ''
    token_only=provider=='appfigures' and token and not email and not password
    if not token_only and (not isinstance(email,str) or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) or len(email)>254):raise ValueError('请输入有效的 来源账户邮箱')
    if not token_only and (not isinstance(password,str) or not 1<=len(password)<=1024):raise ValueError('请输入 来源账户密码')
    if not isinstance(token,str) or len(token)>4096 or any(c.isspace() or ord(c)<32 for c in token):raise ValueError('API Token 格式无效')
    with db.named_lock('data-source-'+provider):
        with db.named_lock('data-sources-key'):
            has_secrets=bool(db.execute('SELECT 1 FROM source_accounts LIMIT 1').fetchone())
            encryption=cipher(cfg,create=not has_secrets)
        saved={'email':email,'password':password,'cookies':[]}
        if provider=='appfigures':saved['access_token']=token
        secret=encryption.encrypt(dump(saved).encode()).decode()
        db.execute("INSERT INTO source_accounts(provider,secret,status,updated_at) VALUES(%s,%s,'saved',%s) AS incoming ON DUPLICATE KEY UPDATE secret=incoming.secret,status='saved',checked_at=NULL,retry_after=0,updated_at=incoming.updated_at",(provider,secret,time.time()))
    return authorize(db,cfg,provider)


def session(cfg):
    client=requests.Session();client.trust_env=False
    if cfg.get('tls_maximum_version')=='1.2':
        from .stores import TLS12Adapter
        client.mount('https://',TLS12Adapter())
    proxy=cfg.get('proxy') or os.environ.get('HTTPS_PROXY')
    if proxy:client.proxies={'http':proxy,'https':proxy}
    return client


def request(client,method,path,**kwargs):
    # Never follow a redirect carrying credentials or source cookies.
    return client.request(method,ORIGIN+path,timeout=(10,20),allow_redirects=False,**kwargs)


def authorized_user(response):
    if response.status_code!=200:return False
    try:
        user=response.json().get('user')
        return isinstance(user,dict) and user.get('emailVerified') is True and bool(user.get('username'))
    except (ValueError,AttributeError):return False


def authorize(db,cfg,provider="appgoblin"):
    provider_name(provider)
    try:return _authorize(db,cfg,provider)
    except RuntimeError:
        result=status(db)
        result[provider].update(status='authorizing',message='该来源正在授权，请稍后检查状态')
        return result


def _authorize(db,cfg,provider):
    provider_name(provider)
    with db.named_lock('data-source-'+provider):
        row=db.execute("SELECT * FROM source_accounts WHERE provider=%s",(provider,)).fetchone()
        if not row:return status(db)
        if row['retry_after']>time.time():return status(db)
        try:
            encryption=cipher(cfg);saved=json.loads(encryption.decrypt(row['secret'].encode()))
        except (InvalidToken,ValueError,OSError):
            db.execute("UPDATE source_accounts SET status='key_unavailable' WHERE provider=%s",(provider,))
            return status(db)
        outcome='network_error';client=session(cfg)
        try:
            for cookie in saved.get('cookies',[]):
                if cookie_allowed(provider,cookie.get('domain','')):
                    client.cookies.set(cookie['name'],cookie['value'],domain=cookie['domain'],path=cookie.get('path','/'),secure=True)
            if provider=='appfigures':
                from .appfigures_source import authorize as figures_authorize
                outcome=figures_authorize(db,client,saved)
            elif provider=='appbrain':
                from .appbrain_source import authorize as brain_authorize
                outcome=brain_authorize(client,saved)
            elif saved.get('cookies') and authorized_user(request(client,'GET','/auth/me')):outcome='authorized'
            else:
                client.cookies.clear()
                result=request(client,'POST','/auth/login',data={'email':saved['email'],'password':saved['password'],'redirectTo':'/free-app-datasets'},
                    headers={'Origin':ORIGIN,'Referer':ORIGIN+'/auth/login'})
                location=urlsplit(result.headers.get('Location','')).path
                if result.status_code==429:outcome='rate_limited'
                elif result.status_code in (400,401):outcome='invalid_credentials'
                elif result.status_code==403 or location.startswith(('/auth/2fa','/auth/verify-email')):outcome='action_required'
                elif result.status_code in (200,302,303) and authorized_user(request(client,'GET','/auth/me')):outcome='authorized'
                else:outcome='source_changed'
            saved['cookies']=[{'name':c.name,'value':c.value,'domain':c.domain,'path':c.path} for c in client.cookies if cookie_allowed(provider,c.domain)] if outcome=='authorized' else []
        except requests.RequestException:outcome='network_error'
        except ValueError:outcome='source_changed'
        finally:client.close()
        retry=0 if outcome=='authorized' else time.time()+(3600 if outcome in ('invalid_credentials','action_required') else 300)
        db.execute("UPDATE source_accounts SET secret=%s,status=%s,checked_at=%s,retry_after=%s WHERE provider=%s",
            (encryption.encrypt(dump(saved).encode()).decode(),outcome,time.time(),retry,provider))
    return status(db)


def account_session(db,cfg,provider="appgoblin"):
    provider_name(provider)
    state=authorize(db,cfg,provider)[provider]
    if state['status']!='authorized':raise ValueError(state['message'])
    row=db.execute("SELECT secret FROM source_accounts WHERE provider=%s",(provider,)).fetchone()
    saved=json.loads(cipher(cfg).decrypt(row['secret'].encode()));client=session(cfg)
    if provider=='appfigures':
        from .appfigures_source import configure
        configure(client,saved)
    for c in saved['cookies']:client.cookies.set(c['name'],c['value'],domain=c['domain'],path=c['path'],secure=True)
    return client


def dataset_catalog(db,cfg):
    with account_session(db,cfg) as client:
        response=request(client,'GET','/free-app-datasets')
        if response.status_code!=200:raise ValueError('数据目录暂不可用或账户权限不足')
        # Metadata only; no guessed download URLs or API token creation.
        files=sorted(set(re.findall(r'appgoblin_[A-Za-z0-9_.-]+\.(?:tsv\.xz|csv)',response.text)))
        return {'files':files,'url':ORIGIN+'/free-app-datasets',
            'note':'已通过账户会话读取目录。下载范围由免费账户权限决定；此处不代表文件已导入。'}


def brain_metrics(db,cfg,store,app_id):
    from . import appbrain_source
    import hashlib
    if store!='google':raise ValueError('AppBrain当前适配器仅支持Android应用')
    client=account_session(db,cfg,'appbrain') if status(db)['appbrain']['configured'] else session(cfg)
    with client:
        response=appbrain_source.get(client,appbrain_source.ORIGIN+'/app/'+app_id)
        if response.status_code!=200:raise ValueError('AppBrain页面不可读取，请检查授权或在来源网站完成人工验证')
        result=appbrain_source.metrics(response.text,app_id)
        result['source_url']=response.url;result['raw_sha256']=hashlib.sha256(response.content).hexdigest()
    output=Path(cfg['data_dir'])/'public-sources';output.mkdir(parents=True,exist_ok=True)
    (output/('appbrain-google-'+app_id+'.json')).write_text(dump(result),encoding='utf-8')
    return result


def app_metrics(db,cfg,store,app_id,provider='auto'):
    from .public_sources import collect
    from .ingest import valid_id
    if not isinstance(app_id,str) or not valid_id(store,app_id):raise ValueError('应用ID无效')
    if provider not in ('auto',*PROVIDERS):raise ValueError('未知数据源')
    if provider=='appfigures':raise ValueError('请在 Appfigures 试用期采集面板建立计划并查看来源归档')
    if provider=='appbrain':
        try:return brain_metrics(db,cfg,store,app_id)
        except requests.RequestException:raise ValueError('AppBrain连接失败，请稍后重试') from None
    failures=[]
    if status(db)['appgoblin']['configured']:
        try:
            with account_session(db,cfg) as client:
                return collect(Path(cfg['data_dir'])/'public-sources',store=store,app_id=app_id,client=client)
        except (ValueError,requests.RequestException):failures.append('AppGoblin账户访问未成功')
    try:
        return collect(Path(cfg['data_dir'])/'public-sources',store=store,app_id=app_id,proxy=cfg.get('proxy') or os.environ.get('HTTPS_PROXY'))
    except (ValueError,requests.RequestException):failures.append('AppGoblin公开页未成功')
    if provider=='appgoblin':raise ValueError('；'.join(failures))
    if store=='google' and status(db)['appbrain']['configured']:
        try:return brain_metrics(db,cfg,store,app_id)
        except (ValueError,requests.RequestException):failures.append('AppBrain账户访问未成功')
    if store=='google':
        row=db.execute("SELECT payload,last_success AS observed_at FROM listings WHERE store='google' AND app_id=%s AND last_success IS NOT NULL ORDER BY last_success DESC LIMIT 1",(app_id,)).fetchone()
        payload=json.loads(row['payload']) if row and row['payload'] else {}
        values={k:str(payload[k]) for k in ('installs_reported','installs') if payload.get(k) is not None}
        if values:return {'source':'Google Play（观星最近已采集快照）','store':store,'app_id':app_id,'scope':'worldwide','country':None,
            'observed_at':row['observed_at'],'values_display':values,'source_url':'https://play.google.com/store/apps/details?id='+app_id,
            'note':'；'.join(failures)+'。已回退到官方累计安装观察，时间为原采集时间；收入与MAU缺失，不作推算。'}
    raise ValueError('；'.join(failures)+'；没有可用的同口径备用数据，请稍后重试或补充官方采集。')
