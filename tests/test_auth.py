import concurrent.futures
import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from appmon import auth
from appmon.db import connect
from appmon.web import create_app
from test_monitor import MySQLTestCase


class AccountTest(MySQLTestCase):
    def client(self):
        return TestClient(create_app(self.cfg))

    def register(self, client, name='external-user'):
        result = client.post('/api/auth/register', json={'username': name, 'password': 'external-password', 'role': 'admin'})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def login(self, client, name='external-user', password='external-password'):
        result = client.post('/api/auth/login', json={'username': name, 'password': password})
        self.assertEqual(result.status_code, 200, result.text)
        client.headers['X-CSRF-Token'] = result.json()['csrf_token']
        return result.json()['user']

    def test_initialization_single_admin_persistence_and_no_token_bypass(self):
        client = self.client()
        self.assertTrue(client.get('/api/auth/config').json()['initialization_required'])
        self.assertEqual(client.post('/api/auth/register', json={'username':'early','password':'password-123'}).status_code, 403)
        self.login_admin(client)
        self.assertFalse(client.get('/api/auth/config').json()['initialization_required'])
        self.assertEqual(client.post('/api/auth/initialize', json={'username':'second','password':'password-123'}).status_code, 409)
        restarted = self.client(); restarted.cookies.update(client.cookies)
        self.assertEqual(restarted.get('/api/auth/me').status_code, 200)
        with patch.dict('os.environ', {'APPMON_API_TOKEN':'old-token'}):
            anonymous = self.client()
        self.assertEqual(anonymous.get('/api/apps', headers={'Authorization':'Bearer old-token'}).status_code, 401)
        self.assertEqual(anonymous.get('/api/exports/abc/download').status_code, 401)

    def test_simultaneous_initialization_has_exactly_one_winner(self):
        app = create_app(self.cfg)
        def attempt(index):
            with TestClient(app) as client:
                return client.post('/api/auth/initialize', json={'username':f'admin-{index}', 'password':'password-123'}).status_code
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(self.db.execute("SELECT count(*) n FROM auth_users WHERE role='admin'").fetchone()['n'], 1)

    def test_shared_chart_and_target_detail_obey_resource_permissions(self):
        from appmon.intelligence import add_observation_target
        admin = self.login_admin(self.client()); client = self.client()
        self.register(client); self.login(client)
        self.assertEqual(admin.put('/api/auth/admin/roles/user', json={'permissions':['featured:read']}).status_code, 200)
        target = add_observation_target(self.db, {'store':'apple','country':'us','language':'en','kind':'featured'})
        identifier = target['id']
        self.assertEqual(client.get('/api/targets?kind=featured').status_code, 200)
        self.assertEqual(client.get(f'/api/targets/{identifier}').status_code, 200)
        self.assertEqual(client.get('/api/targets?kind=chart').status_code, 403)
        self.assertEqual(client.get('/api/targets').status_code, 403)
        self.assertEqual(admin.put('/api/auth/admin/roles/user', json={'permissions':['globalcharts:read']}).status_code, 200)
        self.assertEqual(client.get('/api/charts/detail?store=apple&country=us').status_code, 200)
        self.assertEqual(client.get(f'/api/targets/{identifier}').status_code, 403)

    def test_registration_csrf_cookie_logout_and_direct_api_permissions(self):
        admin = self.login_admin(self.client()); client = self.client()
        self.register(client); user = self.login(client)
        self.assertEqual(user['role'], 'user')
        self.assertEqual(client.get('/api/apps').status_code, 200)
        self.assertEqual(client.get('/api/auth/admin').status_code, 403)
        self.assertEqual(client.get('/api/exports/abc/download').status_code, 403)
        self.assertEqual(client.post('/api/watchlist', json={}).status_code, 403)
        self.assertEqual(client.put('/api/auth/profile', json={}, headers={'X-CSRF-Token':''}).status_code, 403)
        self.assertEqual(client.post('/api/auth/logout', json={}, headers={'Origin':'https://evil.example'}).status_code, 403)
        self.assertEqual(client.post('/api/auth/logout', json={}).status_code, 200)
        self.assertEqual(client.get('/api/apps').status_code, 401)
        response = admin.post('/api/auth/login', json={'username':'test-admin','password':'test-password-1234'})
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        self.assertIn('SameSite=strict', response.headers['set-cookie'])

    def test_role_grants_live_expiry_override_and_disabled_user(self):
        admin = self.login_admin(self.client()); client = self.client()
        self.register(client); user = self.login(client)
        path = '/api/auth/admin/users/' + str(user['id'])
        self.assertEqual(admin.put('/api/auth/admin/roles/user', json={'permissions':['keywords:read']}).status_code, 200)
        self.assertEqual(client.get('/api/apps').status_code, 403)
        self.assertEqual(client.get('/api/keywords').status_code, 200)
        self.assertEqual(admin.put(path, json={'role':'member','member_until':time.time()+3600}).status_code, 200)
        self.assertEqual(client.get('/api/apps').status_code, 401)
        self.login(client)
        self.assertEqual(client.get('/api/apps').status_code, 200)
        self.db.execute('UPDATE auth_users SET member_until=%s WHERE id=%s', (time.time()-1, user['id']))
        self.assertEqual(client.get('/api/apps').status_code, 403)
        self.assertEqual(admin.put(path, json={'permissions':['apps:write']}).status_code, 200)
        updated = self.login(client)
        self.assertEqual(updated['permissions'], ['apps:read', 'apps:write'])
        self.assertEqual(client.get('/api/keywords').status_code, 403)
        self.assertEqual(admin.put(path, json={'enabled':False}).status_code, 200)
        self.assertEqual(client.get('/api/apps').status_code, 401)
        self.assertEqual(client.post('/api/auth/login', json={'username':'external-user','password':'external-password'}).status_code, 401)

    def test_recovery_finds_username_rotates_code_and_revokes_sessions(self):
        self.login_admin(self.client()); client = self.client()
        created = self.register(client); self.login(client)
        anonymous = self.client()
        payload = {'recovery_code':created['recovery_code'], 'password':'changed-password'}
        response = anonymous.post('/api/auth/recover', json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['username'], 'external-user')
        self.assertNotEqual(response.json()['recovery_code'], created['recovery_code'])
        self.assertEqual(client.get('/api/auth/me').status_code, 401)
        self.assertEqual(anonymous.post('/api/auth/recover', json=payload).status_code, 400)
        self.login(client, password='changed-password')
        stored = self.db.execute("SELECT * FROM auth_users WHERE username='external-user'").fetchone()
        self.assertNotIn('changed-password', str(stored))
        self.assertNotIn(created['recovery_code'], str(stored))

    def test_profile_change_and_admin_reset_protect_superadmin(self):
        admin = self.login_admin(self.client()); client = self.client()
        self.register(client); user = self.login(client)
        self.assertEqual(client.put('/api/auth/profile', json={'current_password':'wrong','username':'changed'}).status_code, 400)
        self.assertEqual(client.put('/api/auth/profile', json={'current_password':'external-password','username':'changed','password':'new-password-123'}).status_code, 200)
        self.assertEqual(client.get('/api/auth/me').status_code, 401)
        self.login(client, 'changed', 'new-password-123')
        result = admin.put(f"/api/auth/admin/users/{user['id']}", json={'password':'admin-reset-password'})
        self.assertIn('recovery_code', result.json())
        self.assertEqual(client.get('/api/auth/me').status_code, 401)
        self.login(client, 'changed', 'admin-reset-password')
        identifier = admin.get('/api/auth/me').json()['user']['id']
        self.assertEqual(admin.put(f'/api/auth/admin/users/{identifier}', json={'enabled':False}).status_code, 403)
        self.assertTrue(admin.get('/api/auth/admin/audit').json())

    def test_registration_switch_and_invalid_grants(self):
        admin = self.login_admin(self.client())
        self.assertEqual(admin.put('/api/auth/admin/settings', json={'registration_enabled':False}).status_code, 200)
        self.assertEqual(self.client().post('/api/auth/register', json={'username':'blocked','password':'password-123'}).status_code, 403)
        self.assertEqual(admin.put('/api/auth/admin/roles/user', json={'permissions':['admin']}).status_code, 400)
        self.assertEqual(admin.put('/api/auth/admin/roles/admin', json={'permissions':[]}).status_code, 400)

    def test_delegated_account_management_cannot_cross_admin_or_grant_boundary(self):
        admin = self.login_admin(self.client()); manager = self.client(); other = self.client()
        self.register(manager, 'manager'); principal = self.login(manager, 'manager')
        self.register(other, 'other-user'); target = self.login(other, 'other-user')
        path = f"/api/auth/admin/users/{target['id']}"
        self.assertEqual(manager.put(path, json={'username':'edited-user'}).status_code, 403)
        base = auth.DEFAULTS['user']
        admin.put(f"/api/auth/admin/users/{principal['id']}", json={'permissions':base+['administration:read']})
        self.login(manager, 'manager')
        rows = manager.get('/api/auth/admin').json()['users']
        self.assertTrue(all(u['role'] != 'admin' for u in rows))
        self.assertEqual(manager.put(path, json={'username':'edited-user'}).status_code, 403)
        admin.put(f"/api/auth/admin/users/{principal['id']}", json={'permissions':base+['administration:write']})
        self.login(manager, 'manager')
        self.assertEqual(manager.put(path, json={'username':'edited-user'}).status_code, 200)
        admin_id = admin.get('/api/auth/me').json()['user']['id']
        self.assertEqual(manager.put(f'/api/auth/admin/users/{admin_id}', json={'password':'malicious-reset'}).status_code, 403)
        for payload in ({'permissions':['exports:write']}, {'permissions':['administration:write']}, {'role':'admin'}, {'role':'member'}):
            self.assertIn(manager.put(path,json=payload).status_code, (400,403))
        self.assertEqual(manager.put(f"/api/auth/admin/users/{principal['id']}", json={'permissions':list(auth.PERMISSIONS)}).status_code,403)
        self.assertEqual(manager.put('/api/auth/admin/roles/user', json={'permissions':[]}).status_code,403)
        self.assertEqual(manager.get('/api/auth/admin/audit').status_code,403)
        unchanged = self.db.execute('SELECT * FROM auth_users WHERE id=%s',(target['id'],)).fetchone()
        self.assertEqual(unchanged['role'],'user')
        self.assertEqual(manager.put(path,json={'permissions':['apps:read']}).status_code,200)

    def test_own_editor_superadmin_update_and_recovery_rotation(self):
        admin = self.login_admin(self.client())
        identifier = admin.get('/api/auth/me').json()['user']['id']
        self.assertEqual(admin.post('/api/auth/recovery-code',json={'current_password':'wrong'}).status_code,400)
        result = admin.post('/api/auth/recovery-code',json={'current_password':'test-password-1234'})
        self.assertEqual(result.status_code,200)
        code = result.json()['recovery_code']
        rotated = admin.post('/api/auth/recovery-code',json={'current_password':'test-password-1234'}).json()['recovery_code']
        self.assertNotEqual(code,rotated)
        self.assertEqual(self.client().post('/api/auth/recover',json={'recovery_code':code,'password':'new-password-123'}).status_code,400)
        self.assertEqual(admin.put(f'/api/auth/admin/users/{identifier}',json={'current_password':'test-password-1234','username':'new-admin','password':'new-password-123'}).status_code,200)
        self.assertEqual(admin.get('/api/auth/me').status_code,401)
        self.login(admin,'new-admin','new-password-123')
        user = self.client(); self.register(user); info = self.login(user)
        self.assertEqual(user.get('/api/auth/admin').status_code,403)
        self.assertEqual([u['id'] for u in user.get('/api/auth/admin?own=true').json()['users']],[info['id']])

    def test_operator_recovery_without_credentials_revokes_old_secrets(self):
        import io
        from appmon.account_recovery import run
        admin = self.login_admin(self.client())
        previous = admin.post('/api/auth/recovery-code',json={'current_password':'test-password-1234'}).json()['recovery_code']
        with patch('appmon.account_recovery.sys.stdin.isatty',return_value=False):
            with self.assertRaisesRegex(ValueError,'交互式终端'):run(self.db)
        output = io.StringIO()
        with patch('appmon.account_recovery.sys.stdin.isatty',return_value=True), patch('appmon.account_recovery.sys.stdout',output), patch.object(output,'isatty',return_value=True), patch('builtins.input',side_effect=['RESET','recovered-admin']), patch('appmon.account_recovery.getpass.getpass',side_effect=['new-secret-password','new-secret-password']):
            run(self.db)
        self.assertNotIn('new-secret-password',output.getvalue())
        self.assertEqual(admin.get('/api/auth/me').status_code,401)
        self.assertEqual(self.client().post('/api/auth/recover',json={'recovery_code':previous,'password':'new-password-123'}).status_code,400)
        self.login(admin,'recovered-admin','new-secret-password')
        self.assertEqual(self.db.execute("SELECT count(*) n FROM auth_audit WHERE action='operator_recover_admin'").fetchone()['n'],1)

    def test_throttle_is_shared_between_instances_and_expired_sessions_fail(self):
        self.login_admin(self.client())
        clients = [self.client(), self.client()]
        for index in range(12):
            self.assertEqual(clients[index % 2].post('/api/auth/login', json={'username':'missing-user','password':'bad'}).status_code, 401)
        self.assertEqual(clients[0].post('/api/auth/login', json={'username':'missing-user','password':'bad'}).status_code, 429)
        admin = self.login_admin(self.client())
        self.db.execute('UPDATE auth_sessions SET expires_at=0')
        self.assertEqual(admin.get('/api/auth/me').status_code, 401)

    def test_all_existing_business_routes_have_explicit_permission_mapping(self):
        app = create_app(self.cfg)
        # FastAPI exposes flattened routing through OpenAPI in this version.
        paths = app.openapi()['paths']
        for path, methods in paths.items():
            if not path.startswith('/api/') or path.startswith('/api/auth/') or path == '/api/openapi.json':
                continue
            for method in methods:
                self.assertNotEqual(auth.requirements(path, method.upper()), ['admin'], (path, method))
