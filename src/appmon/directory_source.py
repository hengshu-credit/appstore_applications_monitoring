"""安装指标 email-form adapter. CAPTCHA/SSO screens require user action."""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

def safe_url(url, hosts):
    p=urlsplit(url)
    if p.scheme!='https' or p.hostname not in hosts or p.username or p.password or p.port not in (None,443):
        raise ValueError('安装指标 登录表单指向了未授权域名')
    return url


def get(client,url):
    for _ in range(4):
        response=client.get(safe_url(url,client.source_profile['hosts']),timeout=(10,20),allow_redirects=False)
        if response.status_code not in (301,302,303,307,308):return response
        url=urljoin(url,response.headers.get('Location',''))
    raise ValueError('安装指标 重定向次数过多')


class LoginForms(HTMLParser):
    def __init__(self):super().__init__();self.forms=[];self.current=None
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='form':self.current={'action':attrs.get('action',''),'method':attrs.get('method','get').lower(),'inputs':[]}
        if tag=='input' and self.current is not None:self.current['inputs'].append(attrs)
    def handle_endtag(self,tag):
        if tag=='form' and self.current is not None:self.forms.append(self.current);self.current=None


def login_form(html,url,email,password,hosts):
    parser=LoginForms();parser.feed(html)
    candidates=[f for f in parser.forms if any(i.get('type','').lower()=='password' for i in f['inputs'])]
    if len(candidates)!=1:raise ValueError('安装指标 页面未提供唯一的邮箱密码表单')
    form=candidates[0]
    if form['method']!='post':raise ValueError('安装指标 登录必须使用POST')
    password_fields=[i for i in form['inputs'] if i.get('type','').lower()=='password' and i.get('name')]
    email_fields=[i for i in form['inputs'] if i.get('name') and (i.get('type','').lower()=='email' or i.get('name','').lower() in ('email','username'))]
    if len(password_fields)!=1 or len(email_fields)!=1:raise ValueError('安装指标 登录字段未识别')
    fields={i['name']:i.get('value','') for i in form['inputs'] if i.get('name') and i.get('type','').lower()=='hidden'}
    fields[email_fields[0]['name']]=email;fields[password_fields[0]['name']]=password
    return safe_url(urljoin(url,form['action']),hosts),fields


def logged_in(response):
    if response.status_code!=200:return False
    html=response.text
    return bool(re.search(r'href=[\"\'][^\"\']*(?:logout|signout)',html,re.I)) and not bool(re.search(r'type=[\"\']password',html,re.I))


def authorize(client,saved):
    origin=client.source_profile['origin'];hosts=client.source_profile['hosts']
    account=client.source_profile.get('account_path','/account')
    if saved.get('cookies') and logged_in(get(client,origin+account)):return 'authorized'
    from urllib.parse import urlencode
    login=get(client,origin+'/login?'+urlencode({'reason':'dashboard','continue':origin+account}))
    if login.status_code==429:return 'rate_limited'
    if login.status_code in (403,202):return 'action_required'
    if login.status_code!=200:return 'network_error'
    if re.search(r'(?:g-recaptcha|h-captcha|cf-turnstile)',login.text,re.I):return 'action_required'
    target,fields=login_form(login.text,login.url,saved['email'],saved['password'],hosts)
    client.cookies.clear_expired_cookies()
    page=urlsplit(safe_url(login.url,hosts))
    result=client.post(target,data=fields,headers={'Origin':page.scheme+'://'+page.netloc,'Referer':login.url},timeout=(10,20),allow_redirects=False)
    if result.status_code==429:return 'rate_limited'
    if result.status_code in (403,202):return 'action_required'
    if result.status_code in (400,401):return 'invalid_credentials'
    if result.status_code not in (200,302,303):return 'source_changed'
    return 'authorized' if logged_in(get(client,origin+account)) else 'action_required'


def metrics(html,app_id):
    from .public_sources import PageText
    import time
    parser=PageText();parser.feed(html);text=' '.join(' '.join(parser.parts).split())
    if app_id not in html:raise ValueError('安装指标页面未确认请求的应用')
    values={}
    total=re.search(r'\bInstalls\s+([\d.,]+(?:\s+(?:billion|million|thousand))?\+?)\b',text,re.I)
    if total:values['total_installs']=total.group(1)
    monthly=re.search(r'last 30 days,? (?:the app )?(?:was |has been )?downloaded ([\d.,]+(?:\s+(?:billion|million|thousand))?) times',text,re.I)
    if monthly:values['monthly_downloads_estimate']=monthly.group(1)
    if not values:raise ValueError('安装指标当前页面没有可用安装指标')
    return {'source':'安装指标','store':'google','app_id':app_id,'scope':'worldwide','country':None,
        'observed_at':time.time(),'values_display':values,
        'note':'安装指标全球公开安装指标，保留来源显示值；未返回的收入与国家下载量不补全。'}
