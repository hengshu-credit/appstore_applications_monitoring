"""Account authentication and deny-by-default module authorization."""
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import time

import pymysql
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

RESOURCES = {
    'overview': '监控概览', 'groups': '应用分组', 'events': '事件中心', 'availability': '上下架观察',
    'apps': '应用库', 'watchlist': '重点监控', 'charts': '榜单中心',
    'globalcharts': '全球排行榜', 'featured': '精品推荐', 'keywords': '关键词中心',
    'aso': 'ASO 工作台', 'compare': '竞品集合', 'reviews': '评论记录',
    'market': '市场分布', 'developers': '开发者', 'collection': '采集中心',
    'ads': '广告观察', 'alerts': '告警与日报', 'exports': '数据导出',
    'administration': '账户管理',
}
PERMISSIONS = {f'{resource}:{action}' for resource in RESOURCES for action in ('read', 'write')}
DEFAULTS = {
    'user': ['overview:read', 'apps:read', 'charts:read', 'globalcharts:read', 'market:read'],
    'member': [f'{resource}:read' for resource in RESOURCES if resource not in ('collection', 'administration')],
}
COOKIE = 'appmon_account'
GROUP_NAMES = {'user': '普通用户', 'member': '会员'}


def initialize(db):
    for table, column, definition in [('auth_roles', 'name', 'VARCHAR(64) UNIQUE'),
                                      ('auth_audit', 'ended_at', 'DOUBLE NULL')]:
        if not db.execute(f'SHOW COLUMNS FROM {table} LIKE %s', (column,)).fetchone():
            try:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
            except pymysql.OperationalError as error:
                if error.args[0] != 1060:  # Another service added the column first.
                    raise
    db.execute('INSERT IGNORE INTO auth_settings(id) VALUES(1)')
    for role, permissions in DEFAULTS.items():
        db.execute('INSERT IGNORE INTO auth_roles(role,permissions,name) VALUES(%s,%s,%s)',
                   (role, json.dumps(permissions), GROUP_NAMES[role]))
        db.execute('UPDATE auth_roles SET name=%s WHERE role=%s AND name IS NULL', (GROUP_NAMES[role], role))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password):
    if not isinstance(password, str) or not 10 <= len(password) <= 128:
        raise ValueError('密码长度须为 10–128 个字符')
    salt = secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 600_000).hex()
    return f'pbkdf2_sha256$600000${salt}${value}'


def password_matches(password, encoded):
    if not isinstance(password, str) or len(password) > 128:
        return False
    _, rounds, salt, expected = encoded.split('$')
    actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), int(rounds)).hex()
    return hmac.compare_digest(actual, expected)


# Same work for missing accounts, avoiding a fast username oracle.
DUMMY_PASSWORD = password_hash(secrets.token_urlsafe(32))


def username(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_\-.]{3,64}', value.strip()):
        raise ValueError('账号须为 3–64 位字母、数字、下划线、点或短横线')
    return value.strip().lower()


def grants(value):
    if not isinstance(value, list) or any(not isinstance(p, str) or p not in PERMISSIONS for p in value):
        raise ValueError('权限项无效')
    result = set(value)
    result.update(p.replace(':write', ':read') for p in value if p.endswith(':write'))
    return sorted(result)


def profile(db, row):
    role = row['role']
    groups = []
    if role == 'member' and row['member_until'] is not None and row['member_until'] <= time.time():
        role = 'user'
    if role == 'admin':
        permissions = sorted(PERMISSIONS)
    else:
        override = db.execute('SELECT permissions FROM auth_overrides WHERE user_id=%s', (row['id'],)).fetchone()
        groups = [g['role'] for g in db.execute(
            'SELECT role FROM auth_user_groups WHERE user_id=%s ORDER BY role', (row['id'],)).fetchall()] or [row['role']]
        effective = {'user' if g == 'member' and role == 'user' else g for g in groups}
        if override:
            permissions = json.loads(override['permissions'])
        else:
            rows = db.execute('SELECT permissions FROM auth_roles WHERE role IN %s', (tuple(effective),)).fetchall()
            permissions = sorted({p for group in rows for p in json.loads(group['permissions'])})
    return {k: row[k] for k in ('id', 'username', 'role', 'member_until', 'enabled', 'created_at')} | {
        'effective_role': role, 'permissions': permissions, 'group_ids': groups,
    }


def audit(db, actor, action, target=None):
    # Serialize each actor's stream in the same transaction as the successful action.
    key = actor or 0
    db.execute('INSERT INTO auth_audit_heads(actor_key) VALUES(%s) ON DUPLICATE KEY UPDATE actor_key=%s', (key, key))
    last = db.execute('''SELECT a.* FROM auth_audit_heads h JOIN auth_audit a ON a.id=h.last_id
                         WHERE h.actor_key=%s''', (key,)).fetchone()
    now = time.time()
    if last and last['action'] == action and last['target_id'] == target:
        db.execute('UPDATE auth_audit SET ended_at=GREATEST(ended_at,%s) WHERE id=%s', (now, last['id']))
        return
    entry = db.execute('INSERT INTO auth_audit(actor_id,target_id,action,created_at,ended_at) VALUES(%s,%s,%s,%s,%s)',
                       (actor, target, action, now, now)).lastrowid
    db.execute('UPDATE auth_audit_heads SET last_id=%s WHERE actor_key=%s', (entry, key))


def group_name(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 64:
        raise ValueError('用户组名称须为 1–64 个字符')
    name = value.strip()
    if name.casefold() in ('admin', '超级管理员', '后台超级管理员'):
        raise ValueError('超级管理员名称为系统保留')
    return name


def selected_groups(db, value):
    if not isinstance(value, list) or not value or any(not isinstance(g, str) or len(g) > 16 for g in value):
        raise ValueError('请选择至少一个有效用户组')
    groups = sorted(set(value))
    rows = db.execute('SELECT role FROM auth_roles WHERE role IN %s FOR SHARE', (tuple(groups),)).fetchall()
    if groups != sorted(r['role'] for r in rows):
        raise ValueError('所选用户组无效')
    return groups


def assign_groups(db, identifier, groups):
    db.execute('DELETE FROM auth_user_groups WHERE user_id=%s', (identifier,))
    db.executemany('INSERT INTO auth_user_groups(user_id,role) VALUES(%s,%s)', [(identifier, g) for g in groups])


def account_settings(role, enabled, until):
    if not isinstance(role, str) or role not in DEFAULTS or type(enabled) is not bool or until is not None and (
            type(until) not in (int, float) or not math.isfinite(until) or until < 0):
        raise ValueError('账户类型、状态或会员到期时间无效')
    return role, enabled, until if role == 'member' else None


def recover_administrator(db, new_username, new_password):
    """Local operator recovery only. Never expose this operation as an HTTP route."""
    encoded = password_hash(new_password)
    recovery = secrets.token_urlsafe(32)
    with db:
        row = db.execute("SELECT * FROM auth_users WHERE role='admin' FOR UPDATE").fetchone()
        if not row:
            raise ValueError('尚未创建超级管理员，请从首次启动页面注册')
        name = username(new_username) if new_username else row['username']
        try:
            db.execute('UPDATE auth_users SET username=%s,password_hash=%s,recovery_hash=%s,enabled=1 WHERE id=%s',
                       (name, encoded, digest(recovery), row['id']))
        except pymysql.IntegrityError:
            raise ValueError('该账号已被使用，请更换账号') from None
        db.execute('DELETE FROM auth_sessions WHERE user_id=%s', (row['id'],))
        for account in (row['username'], name):
            db.execute('DELETE FROM auth_limits WHERE bucket=%s', (digest('account:' + account),))
        audit(db, None, 'operator_recover_admin', row['id'])
    return {'username': name, 'recovery_code': recovery}


def throttle(db, request, identifier=''):
    now = time.time()
    address = request.client.host if request.client else 'unknown'
    # Shared across API processes; never trust forwarded client headers.
    for key, maximum in [('ip:' + address, 60), ('account:' + str(identifier).lower()[:128], 12)]:
        bucket = digest(key)
        with db:
            db.execute('DELETE FROM auth_limits WHERE expires_at<%s', (now,))
            db.execute('INSERT IGNORE INTO auth_limits VALUES(%s,0,%s)', (bucket, now + 900))
            row = db.execute('SELECT attempts FROM auth_limits WHERE bucket=%s FOR UPDATE', (bucket,)).fetchone()
            if row['attempts'] >= maximum:
                raise HTTPException(429, '尝试过于频繁，请在 15 分钟后重试')
            db.execute('UPDATE auth_limits SET attempts=attempts+1 WHERE bucket=%s', (bucket,))


def csrf(token):
    return digest('appmon-csrf:' + token)


def current(db, request):
    token = request.cookies.get(COOKIE, '')
    row = db.execute('''SELECT u.* FROM auth_sessions s JOIN auth_users u ON u.id=s.user_id
        WHERE s.token_hash=%s AND s.expires_at>%s AND u.enabled=1''', (digest(token), time.time())).fetchone()
    if not row:
        raise HTTPException(401, '请登录账户')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and not hmac.compare_digest(
            request.headers.get('x-csrf-token', ''), csrf(token)):
        raise HTTPException(403, '安全校验失败，请刷新页面后重试')
    return row


def issue_session(db, request, row):
    token = secrets.token_urlsafe(32)
    db.execute('DELETE FROM auth_sessions WHERE expires_at<=%s OR token_hash=%s',
               (time.time(), digest(request.cookies.get(COOKIE, ''))))
    db.execute('INSERT INTO auth_sessions VALUES(%s,%s,%s)', (digest(token), row['id'], time.time() + 28800))
    response = JSONResponse({'user': profile(db, row), 'csrf_token': csrf(token)})
    response.set_cookie(COOKIE, token, httponly=True, samesite='strict', path='/', max_age=28800,
                        secure=os.environ.get('APPMON_COOKIE_SECURE', '').lower() == 'true' or request.url.scheme == 'https')
    return response


def requirements(path, method, params=None):
    """All business endpoints pass this explicit resource map; unknown routes deny."""
    p = path.removeprefix('/api')
    if p == '/data-sources' or p.startswith('/data-sources/'):
        return ['data-sources:admin']  # explicit, non-delegable administrator capability
    action = 'read' if method in ('GET', 'HEAD') else 'write'
    if p in ('/catalog', '/categories/counts') or p.startswith('/icons/'):
        return []  # authenticated shared display support
    if p == '/openapi.json':
        return ['admin']
    if p.startswith('/control/'):
        resource = (params or {}).get('resource')
        return [f"{ {'watchlist':'watchlist','keywords':'keywords','rules':'alerts'}.get(resource, 'collection') }:write"]
    prefixes = [
        ('/charts/global', 'globalcharts'), ('/charts', 'charts'),
        ('/discovery/keywords', 'keywords'), ('/discovery/report', 'keywords'),
        ('/discovery/growth', 'charts'), ('/discovery', 'apps'),
        ('/research/collection', 'collection'), ('/research/plans', 'collection'),
        ('/research/developer', 'developers'), ('/research/markets', 'apps'),
        ('/research/annotations', 'events'), ('/research/events', 'events'),
        ('/research/aso', 'aso'), ('/suggestions', 'aso'),
        ('/research/reviews', 'reviews'), ('/research/comparisons', 'compare'),
        ('/overview', 'overview'), ('/pool', 'overview'), ('/daily', 'alerts'),
    ] + [('/' + key, key) for key in RESOURCES] + [('/targets', 'collection')]
    for prefix, resource in prefixes:
        if p == prefix or p.startswith(prefix + '/') or prefix in ('/research/developer', '/discovery/report') and p.startswith(prefix):
            if p in ('/compare', '/research/reviews/history', '/research/aso/check', '/discovery/keywords/frequency',
                     '/discovery/reports/preview', '/research/plans/preview', '/charts/global/preview'):
                action = 'read'
            result = [f'{resource}:{action}']
            if p.endswith('/collect') or p == '/charts/global/plan':
                result.append('collection:write')
            if p.startswith('/groups/') and p.endswith('/members') and action=='write':
                result.append('watchlist:write')
            if p.startswith('/groups/') and p.endswith('/monitoring/control'):
                result.append('collection:write')
            return result
    return ['admin']


def authorize(db, request):
    row = current(db, request)
    user = profile(db, row)
    needed = requirements(request.scope['route'].path, request.method, request.path_params)
    if request.method == 'GET' and request.url.path.startswith('/api/targets'):
        kind = request.query_params.get('kind')
        if 'target_id' in request.path_params:
            target = db.execute('SELECT kind FROM observation_targets WHERE id=%s',
                                (request.path_params['target_id'],)).fetchone()
            kind = target['kind'] if target else None
        if kind in ('chart', 'featured', 'reviews'):
            needed = [f"{ {'chart': 'charts', 'featured': 'featured', 'reviews': 'reviews'}[kind] }:read"]
            if 'collection:read' in user['permissions']:
                needed = []
    if request.method == 'GET' and request.url.path in ('/api/charts/detail', '/api/charts/view') and 'globalcharts:read' in user['permissions']:
        needed = []
    if row['role'] != 'admin' and ('data-sources:admin' in needed or any(p not in user['permissions'] for p in needed)):
        raise HTTPException(403, '当前账户没有访问此资源的权限，请联系管理员')
    request.state.user = user


def router(database):
    api = APIRouter(prefix='/api/auth')
    DB = Depends(database)

    def actor(request: Request, db=DB):
        return current(db, request)

    def admin(row=Depends(actor)):
        if row['role'] != 'admin':
            raise HTTPException(403, '仅超级管理员可管理账户和权限')
        return row

    @api.get('/config')
    def config(db=DB):
        settings = db.execute('SELECT * FROM auth_settings WHERE id=1').fetchone()
        return {'initialization_required': not bool(db.execute('SELECT id FROM auth_users LIMIT 1').fetchone()),
                'registration_enabled': bool(settings and settings['registration_enabled'])}

    def create(value, request, db, first):
        throttle(db, request, value.get('username', ''))
        name, encoded = username(value.get('username')), password_hash(value.get('password'))
        recovery = secrets.token_urlsafe(32)
        try:
            with db:
                settings = db.execute('SELECT * FROM auth_settings WHERE id=1 FOR UPDATE').fetchone()
                exists = bool(db.execute('SELECT id FROM auth_users LIMIT 1').fetchone())
                if first and exists:
                    raise HTTPException(409, '系统已初始化，请直接登录')
                if not first and (not exists or not settings['registration_enabled']):
                    raise HTTPException(403, '当前未开放注册')
                identifier = db.execute('''INSERT INTO auth_users(username,password_hash,recovery_hash,role,created_at)
                    VALUES(%s,%s,%s,%s,%s)''', (name, encoded, digest(recovery), 'admin' if first else 'user', time.time())).lastrowid
                audit(db, identifier, 'initialize' if first else 'register', identifier)
        except pymysql.IntegrityError:
            raise HTTPException(409, '该账号不可用，请更换账号') from None
        return {'username': name, 'recovery_code': recovery}

    @api.post('/initialize')
    def bootstrap(request: Request, value: dict = Body(...), db=DB):
        return create(value, request, db, True)

    @api.post('/register')
    def register(request: Request, value: dict = Body(...), db=DB):
        return create(value, request, db, False)

    @api.post('/login')
    def login(request: Request, value: dict = Body(...), db=DB):
        name = username(value.get('username'))
        throttle(db, request, name)
        with db:
            row = db.execute('SELECT * FROM auth_users WHERE username=%s FOR UPDATE', (name,)).fetchone()
            valid = password_matches(value.get('password'), row['password_hash'] if row else DUMMY_PASSWORD)
            if not valid or not row or not row['enabled']:
                raise HTTPException(401, '账号或密码不正确，或账户已停用')
            audit(db, row['id'], 'login', row['id'])
            return issue_session(db, request, row)

    @api.get('/me')
    def me(request: Request, row=Depends(actor), db=DB):
        return {'user': profile(db, row), 'csrf_token': csrf(request.cookies[COOKIE])}

    @api.post('/logout')
    def logout(request: Request, row=Depends(actor), db=DB):
        db.execute('DELETE FROM auth_sessions WHERE token_hash=%s', (digest(request.cookies[COOKIE]),))
        response = JSONResponse({'ok': True})
        response.delete_cookie(COOKIE, path='/')
        return response

    @api.post('/recover')
    def recover(request: Request, value: dict = Body(...), db=DB):
        code = value.get('recovery_code', '')
        if not isinstance(code, str) or len(code) > 256:
            raise ValueError('恢复码格式错误')
        throttle(db, request, digest(code))
        encoded = password_hash(value.get('password'))
        new_code = secrets.token_urlsafe(32)
        with db:
            row = db.execute('SELECT * FROM auth_users WHERE recovery_hash=%s FOR UPDATE', (digest(code),)).fetchone()
            if not row or not row['enabled']:
                raise HTTPException(400, '恢复码无效或账户已停用，请联系管理员')
            db.execute('UPDATE auth_users SET password_hash=%s,recovery_hash=%s WHERE id=%s', (encoded, digest(new_code), row['id']))
            db.execute('DELETE FROM auth_sessions WHERE user_id=%s', (row['id'],))
            audit(db, row['id'], 'recover', row['id'])
        return {'username': row['username'], 'recovery_code': new_code}

    @api.put('/profile')
    def update_profile(request: Request, value: dict = Body(...), row=Depends(actor), db=DB):
        throttle(db, request, row['username'])
        with db:
            fresh = db.execute('SELECT * FROM auth_users WHERE id=%s FOR UPDATE', (row['id'],)).fetchone()
            if not password_matches(value.get('current_password'), fresh['password_hash']):
                raise HTTPException(400, '当前密码不正确')
            name = username(value.get('username', row['username']))
            encoded = password_hash(value['password']) if value.get('password') else fresh['password_hash']
            try:
                db.execute('UPDATE auth_users SET username=%s,password_hash=%s WHERE id=%s', (name, encoded, row['id']))
            except pymysql.IntegrityError:
                raise HTTPException(409, '该账号不可用') from None
            db.execute('DELETE FROM auth_sessions WHERE user_id=%s', (row['id'],))
            audit(db, row['id'], 'profile', row['id'])
        return {'ok': True}

    @api.post('/recovery-code')
    def rotate_recovery(request: Request, value: dict = Body(...), row=Depends(actor), db=DB):
        throttle(db, request, row['username'])
        recovery = secrets.token_urlsafe(32)
        with db:
            fresh = db.execute('SELECT * FROM auth_users WHERE id=%s FOR UPDATE', (row['id'],)).fetchone()
            if not password_matches(value.get('current_password'), fresh['password_hash']):
                raise HTTPException(400, '当前密码不正确')
            db.execute('UPDATE auth_users SET recovery_hash=%s WHERE id=%s', (digest(recovery), row['id']))
            audit(db, row['id'], 'rotate_recovery_code', row['id'])
        return {'username': row['username'], 'recovery_code': recovery}

    @api.get('/admin')
    def management(q: str = Query('', max_length=64), offset: int = Query(0, ge=0), own: bool = False,
                   row=Depends(actor), db=DB):
        principal = profile(db, row)
        is_admin = row['role'] == 'admin'
        if not own and not is_admin and 'administration:read' not in principal['permissions']:
            raise HTTPException(403, '没有账户管理查看权限')
        scope = 'id=%s' if own else "1=1" if is_admin else "role!='admin'"
        args = [row['id']] if own else []
        scope += ' AND username LIKE %s'; args.append('%' + q + '%')
        users = db.execute('SELECT * FROM auth_users WHERE ' + scope + ' ORDER BY id LIMIT 100 OFFSET %s', (*args, offset)).fetchall()
        total = db.execute('SELECT count(*) n FROM auth_users WHERE ' + scope, args).fetchone()['n']
        items = []
        for user in users:
            override = db.execute('SELECT permissions FROM auth_overrides WHERE user_id=%s', (user['id'],)).fetchone()
            items.append(profile(db, user) | {'override': json.loads(override['permissions']) if override else None})
        groups = db.execute('SELECT role,name,permissions FROM auth_roles ORDER BY name').fetchall()
        return {'users': items, 'total': total, 'resources': RESOURCES,
                'can_manage': is_admin or 'administration:write' in principal['permissions'],
                'is_admin': is_admin, 'own': own,
                'roles': {g['role']: json.loads(g['permissions']) for g in groups},
                'group_names': {g['role']: g['name'] for g in groups},
                'registration_enabled': bool(db.execute('SELECT registration_enabled FROM auth_settings WHERE id=1').fetchone()['registration_enabled'])}

    @api.put('/admin/settings')
    def settings(value: dict = Body(...), row=Depends(admin), db=DB):
        enabled = value.get('registration_enabled')
        if type(enabled) is not bool:
            raise ValueError('注册开关无效')
        with db:
            db.execute('UPDATE auth_settings SET registration_enabled=%s WHERE id=1', (enabled,))
            audit(db, row['id'], 'registration_settings')
        return {'ok': True}

    @api.post('/admin/roles')
    def create_role(value: dict = Body(...), row=Depends(admin), db=DB):
        role = secrets.token_hex(8)
        name, permissions = group_name(value.get('name')), grants(value.get('permissions'))
        with db:
            try:
                db.execute('INSERT INTO auth_roles(role,name,permissions) VALUES(%s,%s,%s)',
                           (role, name, json.dumps(permissions)))
            except pymysql.IntegrityError:
                raise HTTPException(409, '用户组名称已存在') from None
            audit(db, row['id'], 'create_role_' + role)
        return {'role': role, 'name': name, 'permissions': permissions}

    @api.put('/admin/roles/{role}')
    def update_role(role: str, value: dict = Body(...), row=Depends(admin), db=DB):
        permissions = grants(value.get('permissions'))
        with db:
            group = db.execute('SELECT name FROM auth_roles WHERE role=%s FOR UPDATE', (role,)).fetchone()
            if not group:
                raise ValueError('无效用户组')
            name = group_name(value.get('name', group['name']))
            try:
                db.execute('UPDATE auth_roles SET permissions=%s,name=%s WHERE role=%s', (json.dumps(permissions), name, role))
            except pymysql.IntegrityError:
                raise HTTPException(409, '用户组名称已存在') from None
            audit(db, row['id'], 'role_' + role)
        return {'ok': True, 'role': role, 'name': name, 'permissions': permissions}

    @api.post('/admin/users')
    def create_user(value: dict = Body(...), row=Depends(admin), db=DB):
        name, encoded = username(value.get('username')), password_hash(value.get('password'))
        recovery = secrets.token_urlsafe(32)
        with db:
            groups = selected_groups(db, value.get('group_ids'))
            role, enabled, until = account_settings('member' if 'member' in groups else 'user',
                                                     value.get('enabled', True), value.get('member_until'))
            try:
                identifier = db.execute('''INSERT INTO auth_users(username,password_hash,recovery_hash,role,
                    enabled,member_until,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)''',
                    (name, encoded, digest(recovery), role, enabled, until, time.time())).lastrowid
            except pymysql.IntegrityError:
                raise HTTPException(409, '该账号不可用') from None
            assign_groups(db, identifier, groups)
            if value.get('permissions') is not None:
                db.execute('INSERT INTO auth_overrides VALUES(%s,%s)', (identifier, json.dumps(grants(value['permissions']))))
            audit(db, row['id'], 'create_user', identifier)
        return {'id': identifier, 'username': name, 'recovery_code': recovery}

    @api.put('/admin/users/{identifier}')
    def update_user(identifier: int, request: Request, value: dict = Body(...), row=Depends(actor), db=DB):
        is_admin, own = row['role'] == 'admin', identifier == row['id']
        if own:
            forbidden = set(value) & {'role', 'group_ids', 'enabled', 'member_until', 'permissions'}
            if forbidden:
                raise HTTPException(403, '不能修改自己的身份、状态或权限')
            # All own credential edits use the same current-password validation.
            return update_profile(request, value, row, db)
        principal = profile(db, row)
        if not is_admin and 'administration:write' not in principal['permissions']:
            raise HTTPException(403, '没有账户管理操作权限')
        with db:
            target = db.execute('SELECT * FROM auth_users WHERE id=%s FOR UPDATE', (identifier,)).fetchone()
            if not target:
                raise HTTPException(404, '账户不存在')
            if target['role'] == 'admin':
                raise HTTPException(403, '非超管不能修改超级管理员')
            previous = profile(db, target)
            if not is_admin and not set(previous['permissions']) <= set(principal['permissions']):
                raise HTTPException(403, '不能管理权限高于自己的账户')
            role, enabled = value.get('role', target['role']), value.get('enabled', bool(target['enabled']))
            until = value.get('member_until', target['member_until'])
            if 'group_ids' in value:
                groups = selected_groups(db, value['group_ids'])
                if not is_admin and set(groups) != set(previous['group_ids']):
                    raise HTTPException(403, '只有超管可以分配用户组')
                role = 'member' if 'member' in groups else 'user'
                assign_groups(db, identifier, groups)
            elif role != target['role']:
                if not is_admin and previous['group_ids'] != [target['role']]:
                    raise HTTPException(403, '只有超管可以分配用户组')
                db.execute('DELETE FROM auth_user_groups WHERE user_id=%s', (identifier,))
            role, enabled, until = account_settings(role, enabled, until)
            name = username(value.get('username', target['username']))
            try:
                db.execute('UPDATE auth_users SET username=%s,role=%s,enabled=%s,member_until=%s WHERE id=%s',
                           (name, role, enabled, until, identifier))
            except pymysql.IntegrityError:
                raise HTTPException(409, '该账号不可用') from None
            if 'permissions' in value:
                if value['permissions'] is None:
                    db.execute('DELETE FROM auth_overrides WHERE user_id=%s', (identifier,))
                else:
                    permissions = json.dumps(grants(value['permissions']))
                    db.execute('REPLACE INTO auth_overrides VALUES(%s,%s)', (identifier, permissions))
            if not is_admin:
                updated = db.execute('SELECT * FROM auth_users WHERE id=%s', (identifier,)).fetchone()
                effective = set(profile(db, updated)['permissions'])
                # Check both now and after membership expires; role inheritance
                # must not become an indirect escalation when a member lapses.
                future = set(profile(db, updated | {'member_until': 0})['permissions']) if role == 'member' else effective
                if not (effective | future) <= set(principal['permissions']):
                    raise HTTPException(403, '只能分配自己已拥有的权限')
                if (effective | future) - set(previous['permissions']) & {'administration:read', 'administration:write'}:
                    raise HTTPException(403, '只有超管可以授予账户管理权限')
            recovery = None
            if value.get('password'):
                recovery = secrets.token_urlsafe(32)
                db.execute('UPDATE auth_users SET password_hash=%s,recovery_hash=%s WHERE id=%s',
                           (password_hash(value['password']), digest(recovery), identifier))
            db.execute('DELETE FROM auth_sessions WHERE user_id=%s', (identifier,))
            audit(db, row['id'], 'update_user', identifier)
        return {'ok': True, **({'recovery_code': recovery} if recovery else {})}

    @api.get('/admin/audit')
    def audit_log(row=Depends(admin), db=DB):
        return db.execute('''SELECT a.id,a.action,a.actor_id,a.target_id,a.created_at,
            COALESCE(a.ended_at,a.created_at) ended_at,u.username actor,t.username target FROM auth_audit a
            LEFT JOIN auth_users u ON u.id=a.actor_id LEFT JOIN auth_users t ON t.id=a.target_id
            ORDER BY COALESCE(a.ended_at,a.created_at) DESC,a.id DESC LIMIT 200''').fetchall()

    return api
