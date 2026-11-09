import json
import tempfile
import unittest
from pathlib import Path

from appmon import public_keywords as pk
from appmon.catalog import APPLE_COUNTRIES
from appmon.locales import PREFERENCES
from test_monitor import OceanBaseTestCase


class PublicKeywordTest(unittest.TestCase):
    def test_every_country_has_native_query_seeds_and_apple_scope(self):
        jobs = pk.make_jobs(list(PREFERENCES['countries']))
        self.assertEqual({j['country'] for j in jobs}, set(PREFERENCES['countries']))
        self.assertEqual({j['country'] for j in jobs if j['kind'] == 'apple'}, APPLE_COUNTRIES)
        self.assertIn('q=%E0%B8', next(j['url'] for j in jobs if j['country'] == 'th' and j['kind'] == 'suggest'))
        with self.assertRaises(ValueError):
            pk.make_jobs(['zz'])

    def job(self):
        return pk.make_jobs(['us'], ['suggest'], 1)[0]

    def test_suggestions_require_response_evidence_and_do_not_invent_volume(self):
        rows = pk.parse_observation(self.job(), json.dumps(['loan', ['loan app', 'Sloane Square', 'weather', 'loan app']]))
        self.assertEqual(len(rows), 2)  # same observation, two explicitly identified candidate stores
        self.assertEqual({r['keyword'] for r in rows}, {'loan app'})
        self.assertTrue(all(r['search_volume'] is None and r['geography'] == 'requested_gl_not_verified' for r in rows))
        self.assertEqual(pk.parse_observation(self.job(), '["loan",[]]'), [])
        with self.assertRaises(ValueError):
            pk.parse_observation(self.job(), '["different country seed",["loan"]]')

    def test_apple_storefront_category_and_description(self):
        job = pk.make_jobs(['th'], ['apple'])[0]
        entry = {'im:name': {'label': 'Example: สินเชื่อ'}, 'summary': {'label': 'สินเชื่อส่วนบุคคล'},
                 'category': {'attributes': {'im:id': '6015'}},
                 'id': {'label': 'https://apps.apple.com/th/app/id123', 'attributes': {'im:id': '123'}}}
        payload = {'feed': {'id': {'label': job['url']}, 'entry': [entry]}}
        rows = pk.parse_observation(job, json.dumps(payload))
        self.assertIn('สินเชื่อส่วนบุคคล', {r['keyword'] for r in rows})
        self.assertTrue(all(r['store'] == 'apple' and r['country'] == 'th' and r['app_id'] == '123' for r in rows))
        payload['feed']['id']['label'] = job['url'].replace('/th/', '/us/')
        with self.assertRaises(ValueError):
            pk.parse_observation(job, json.dumps(payload))

    def test_visible_page_ignores_script_and_preserves_country(self):
        job = dict(country='mx', language='es', kind='website', url='https://example.org/mx', phrases=['crédito rápido'])
        rows = pk.parse_observation(job, '<script>instant loan</script><style>cash advance</style><h1>Crédito rápido</h1>')
        self.assertIn('crédito rápido', {r['keyword'] for r in rows})
        self.assertNotIn('instant loan', {r['keyword'] for r in rows})
        self.assertNotIn('cash advance', {r['keyword'] for r in rows})
        self.assertTrue(all(r['country'] == 'mx' and r['language'] == 'es' for r in rows))

    def test_format_controls_are_removed_from_terms_and_evidence(self):
        self.assertEqual(pk.normalized('Uno\u00ad\u200e'), 'Uno')
        rows = pk.parse_observation(self.job(), json.dumps(['loan', ['loan\u200e app', 'loan\u0000']]))
        self.assertEqual({r['keyword'] for r in rows}, {'loan app'})
        # Python re.I conflates dotted/dotless I, unlike the provenance normalizer.
        self.assertFalse(pk.matches('kredi', 'KREDı'))
        self.assertIn('kredi̇', pk.extract_terms('KREDİ', 'tr', title=True))

    def test_loan_page_with_captcha_form_is_not_a_challenge_page(self):
        self.assertFalse(pk.challenge_page('<title>Personal loans</title><script src="recaptcha.js"></script>', 'https://example.org/loans'))
        self.assertTrue(pk.challenge_page('<title>Just a moment...</title>', 'https://example.org/loans'))
        self.assertTrue(pk.challenge_page('<title>Consent</title>', 'https://consent.google.com/'))

    def test_resume_errors_checksum_and_plan_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            jobs = pk.make_jobs(['us', 'th'], ['suggest'], 1)
            calls = []

            def fetch(job):
                calls.append(job['country'])
                if job['country'] == 'th':
                    raise ValueError('unavailable')
                body = json.dumps([job['query'], ['loan app']])
                return body.encode(), body, job['url']

            result = pk.collect(jobs, folder, fetcher=fetch)
            self.assertEqual(result['statuses'], {'ok': 1, 'error': 1})
            self.assertEqual(result['countries_requested'], 2)
            self.assertEqual(result['countries_with_candidates'], 1)
            again = pk.collect(jobs, folder, fetcher=fetch)
            self.assertEqual(result['sha256'], again['sha256'])
            self.assertEqual(len(calls), 2)
            pk.collect(jobs, folder, fetcher=fetch, retry_errors=True)
            self.assertEqual(len(calls), 3)
            self.assertEqual(pk.reparse(folder)['sha256'], result['sha256'])
            with self.assertRaises(ValueError):
                pk.collect(jobs[:1], folder, fetcher=fetch)
            (folder / 'keywords.jsonl').write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                pk.import_release(None, folder)


class PublicKeywordImportTest(OceanBaseTestCase):
    def test_import_is_idempotent_market_specific_and_does_not_create_jobs(self):
        folder = self.folder / 'release'
        jobs = pk.make_jobs(['us', 'th'], ['suggest'], 1)

        def fetch(job):
            body = json.dumps([job['query'], [job['query'] + ' app']])
            return body.encode(), body, job['url']

        pk.collect(jobs, folder, fetcher=fetch)
        result = pk.import_release(self.db, folder)
        self.assertEqual(result['release_terms'], 4)
        pk.import_release(self.db, folder)
        rows = self.db.execute('SELECT * FROM keyword_candidates').fetchall()
        self.assertEqual(len(rows), 4)
        self.assertEqual({r['country'] for r in rows}, {'us', 'th'})
        self.assertTrue(all('非搜索量' in r['source'] for r in rows))
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM keyword_targets').fetchone()['n'], 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM keyword_reports').fetchone()['n'], 0)
