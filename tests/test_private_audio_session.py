"""Private audio and agent traces require the local owner's authenticated session."""
from pathlib import Path
from unittest.mock import patch
import json, tempfile, threading, unittest
from fastapi import FastAPI, Depends, Header, Request
from fastapi.testclient import TestClient
from local_session import register_local_sessions, MARKER
from agent_routes import register_agent_routes
from music_routes import register_music_routes


def fixture(root, port=18765, media=True):
    root.mkdir(parents=True, exist_ok=True)
    (root/'local_session.js').write_text('// local test helper')
    app=FastAPI();sessions=register_local_sessions(app,root,'synthetic-owner-token',port=port)
    def authorize(request:Request, authorization:str=Header(default='')):
        sessions.authorize(request,authorization)
    @app.get('/private',dependencies=[Depends(authorize)])
    def private():return {'ok':True}
    if media:
        register_agent_routes(app,root=root,authorize=authorize)
        music=register_music_routes(app,root=root,authorize=authorize,lock=threading.Lock(),
            unload=lambda:None,synthesize=lambda text:(b'',{}))
        (music.artifacts/('a'*32+'.wav')).write_bytes(b'RIFF synthetic fixture')
    client=TestClient(app,base_url='http://127.0.0.1:'+str(port),client=('127.0.0.1',50000))
    return sessions,client


class PrivateAudioSessionTests(unittest.TestCase):
    def test_management_session_can_preview_audio_without_opening_the_voice_page(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);sessions,client=fixture(root,18766,False)
            (root/'artifacts').mkdir();(root/'artifacts'/('b'*32+'.wav')).write_bytes(b'RIFF same installation')
            route='/local/media/artifacts/'+'b'*32
            with client:
                self.assertEqual(client.get(route).status_code,401)
                client.post('/local/session',headers={'X-Local-Client':MARKER})
                self.assertNotIn('whale_local_session_18765',client.cookies)
                self.assertEqual(client.get(route).content,b'RIFF same installation')
                self.assertEqual(client.get(route,headers={'Origin':'https://untrusted.example'}).status_code,401)

    def test_media_bridge_rejects_file_aliases_and_arbitrary_names(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);_,client=fixture(root,18766,False)
            (root/'artifacts').mkdir();secret=root/'private.txt';secret.write_text('private fixture')
            import os
            alias=root/'artifacts'/('c'*32+'.wav')
            os.link(secret,alias)
            with client:
                client.post('/local/session',headers={'X-Local-Client':MARKER})
                self.assertEqual(client.get('/local/media/artifacts/'+'c'*32).status_code,404)
                self.assertEqual(client.get('/local/media/artifacts/private.txt').status_code,404)
                self.assertEqual(client.get('/local/media/samples/private.txt').status_code,404)

    def test_actual_status_and_artifact_routes_require_authentication(self):
        with tempfile.TemporaryDirectory() as td:
            _,client=fixture(Path(td))
            with patch('agent_routes.deepseek_client.ready',return_value=False),patch(
                'agent_routes.deepseek_client.configuration',return_value={'model':'fixture'}),client:
                for route in ['/agent/status','/music/status','/artifacts/'+'a'*32]:
                    self.assertEqual(client.get(route).status_code,401,route)
                    self.assertEqual(client.get(route,headers={'Authorization':'Bearer synthetic-owner-token'}).status_code,200,route)
                self.assertEqual(client.post('/local/session',headers={'X-Local-Client':MARKER}).status_code,200)
                self.assertEqual(client.get('/agent/status').status_code,200)
                self.assertEqual(client.get('/artifacts/'+'a'*32).content,b'RIFF synthetic fixture')

    def test_foreign_origin_cannot_bootstrap_or_read_authenticated_content(self):
        with tempfile.TemporaryDirectory() as td:
            _,client=fixture(Path(td),media=False)
            with client:
                self.assertEqual(client.post('/local/session',headers={
                    'X-Local-Client':MARKER,'Origin':'https://untrusted.example'}).status_code,403)
                client.post('/local/session',headers={'X-Local-Client':MARKER})
                self.assertEqual(client.get('/private',headers={'Origin':'https://untrusted.example'}).status_code,401)
                self.assertEqual(client.get('/private',headers={'Sec-Fetch-Site':'cross-site'}).status_code,401)
                self.assertEqual(client.get('/private').status_code,200)

    def test_voice_and_management_ports_keep_independent_browser_cookies(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);voice,a=fixture(root/'voice',18765,False);management,b=fixture(root/'management',18766,False)
            self.assertNotEqual(voice.cookie,management.cookie)
            with a,b:
                a.post('/local/session',headers={'X-Local-Client':MARKER})
                b.cookies.update(a.cookies)
                self.assertEqual(b.get('/private').status_code,401)
                b.post('/local/session',headers={'X-Local-Client':MARKER})
                a.cookies.update(b.cookies)
                self.assertEqual(a.get('/private').status_code,200)
                self.assertEqual(b.get('/private').status_code,200)
                # Refreshing either service must not invalidate the other's cookie.
                a.post('/local/session',headers={'X-Local-Client':MARKER})
                b.cookies.update(a.cookies)
                self.assertEqual(b.get('/private').status_code,200)

    def test_installer_owned_instance_requires_its_private_bootstrap_cookie(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'data/setup').mkdir(parents=True)
            (root/'data/setup/installation.local.json').write_text('{"completed":true}')
            (root/'data/setup/browser-session.local.json').write_text(json.dumps({
                'cookie':'synthetic-bootstrap-session','expires':2000}))
            sessions,client=fixture(root,media=False);sessions.clock=lambda:1000
            with client:
                self.assertEqual(client.post('/local/session',headers={'X-Local-Client':MARKER}).status_code,401)
                client.cookies.set('whale_setup_session','synthetic-bootstrap-session')
                self.assertEqual(client.post('/local/session',headers={'X-Local-Client':MARKER}).status_code,200)
                self.assertEqual(client.get('/private').status_code,200)
                sessions.clock=lambda:2001
                self.assertEqual(client.get('/private').status_code,401)


if __name__=='__main__':unittest.main()
