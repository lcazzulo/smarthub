import json
import unittest
import test_backend


class ManualTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_backend.BackendTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.online()
        self.live, self.http, self.client = self.fixture.live, self.fixture.http, self.fixture.client
        self.state = {'token': 'first', 'accepted_token': None, 'enabled': False, 'dry_run': False,
                      'openings': {room: 0 for room in self.live.rooms}, 'rooms': {}}
        self.report()

    def report(self, retained=False):
        self.live.receive('test/controller/manual/state', json.dumps(self.state), retained)

    def body(self):
        return {key: self.state[key] for key in ('token', 'enabled', 'openings')}

    def test_submit_is_nonretained_and_does_not_optimistically_change_state(self):
        body = self.body()
        body['enabled'] = True
        response = self.http.put('/api/v1/manual', json=body)
        self.assertEqual(response.status_code, 202)
        self.assertFalse(response.json['confirmed'])
        self.assertFalse(self.http.get('/api/v1/manual').json['state']['enabled'])
        args, kwargs = self.client.publish.call_args
        self.assertEqual(args[0], 'test/controller/manual/set')
        self.assertEqual(json.loads(args[1]), body)
        self.assertEqual(kwargs, {'qos': 0, 'retain': False})

    def test_stale_disconnect_retained_and_old_tokens_cannot_submit(self):
        body = self.body()
        body['token'] = 'old'
        self.assertEqual(self.http.put('/api/v1/manual', json=body).status_code, 503)
        self.fixture.now += 6
        self.assertFalse(self.http.get('/api/v1/manual').json['available'])
        self.assertEqual(self.http.put('/api/v1/manual', json=self.body()).status_code, 503)
        self.report(retained=True)
        self.assertFalse(self.http.get('/api/v1/manual').json['available'])
        self.report()
        self.live.connection(False)
        self.assertEqual(self.http.put('/api/v1/manual', json=self.body()).status_code, 503)
        self.client.publish.assert_not_called()

    def test_validation_and_publish_failures(self):
        self.state['enabled'] = True
        self.report()
        for invalid in (True, '20', -1, 101, None):
            body = self.body()
            body['openings'] = {**body['openings'], 'bedroom': invalid}
            self.assertEqual(self.http.put('/api/v1/manual', json=body).status_code, 400)
        body = self.body()
        body['openings'] = {**body['openings'], 'kitchen': 40}
        self.assertEqual(self.http.put('/api/v1/manual', json=body).status_code, 400)
        self.client.publish.assert_not_called()
        self.client.publish.return_value.rc = 4
        self.assertEqual(self.http.put('/api/v1/manual', json=self.body()).status_code, 503)

    def test_mode_transition_rejects_nonzero_openings_and_origin_restriction_applies(self):
        body = self.body()
        body['enabled'] = True
        body['openings'] = {**body['openings'], 'bedroom': 10}
        self.assertEqual(self.http.put('/api/v1/manual', json=body).status_code, 400)
        self.assertEqual(self.http.put('/api/v1/manual', json=self.body(),
                                      headers={'Origin': 'https://another.example'}).status_code, 403)
        self.client.publish.assert_not_called()
