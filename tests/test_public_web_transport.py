"""Exercise real urllib HTTP framing with synthetic sockets; no network traffic."""
import io,json,socket,ssl,tempfile,unittest,urllib.error
from pathlib import Path
from unittest.mock import patch
import agent_web
import mcp_read


def address(ip,port=443):
    return (socket.AF_INET,socket.SOCK_STREAM,socket.IPPROTO_TCP,'',(ip,port))


class Socket:
    def __init__(self,responses):self.responses=list(responses);self.targets=[];self.sent=[];self.closed=False
    def settimeout(self,value):self.timeout=value
    def connect(self,value):self.targets.append(value)
    def sendall(self,value):self.sent.append(value)
    def makefile(self,*args,**kwargs):return io.BytesIO(self.responses.pop(0))
    def close(self):self.closed=True


OK=b'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: 2\r\n\r\nok'


class PinnedWebTests(unittest.TestCase):
    def test_dns_rebinding_cannot_change_the_connected_address_or_original_tls_host(self):
        stream=Socket([OK]);lookups=[];tls=[]
        def resolve(host,port,**kwargs):
            lookups.append(host)
            return [address('93.184.216.34' if len(lookups)==1 else '127.0.0.1')]
        def wrap(context,raw,**kwargs):
            tls.append((context.check_hostname,context.verify_mode,kwargs['server_hostname']));return raw
        with patch('agent_web.socket.getaddrinfo',side_effect=resolve),patch('agent_web.socket.socket',return_value=stream),patch(
                'urllib.request.getproxies',return_value={}),patch('ssl.SSLContext.wrap_socket',autospec=True,side_effect=wrap):
            text,url=agent_web.public_get('https://audit-rebind.example/page')
        self.assertEqual(text,'ok');self.assertEqual(lookups,['audit-rebind.example'])
        self.assertEqual(stream.targets,[('93.184.216.34',443)])
        self.assertEqual(tls,[(True,ssl.CERT_REQUIRED,'audit-rebind.example')])
        self.assertIn(b'Host: audit-rebind.example',b''.join(stream.sent))

    def test_local_http_proxy_connect_is_pinned_but_tls_and_origin_host_are_preserved(self):
        stream=Socket([b'HTTP/1.1 200 Connection established\r\n\r\n',OK]);tls=[];lookups=[]
        def resolve(host,port,**kwargs):
            lookups.append(host)
            return [address('127.0.0.1',7897)] if host=='localhost' else [address('93.184.216.34')]
        def wrap(context,raw,**kwargs):tls.append(kwargs['server_hostname']);return raw
        with patch('agent_web.socket.getaddrinfo',side_effect=resolve),patch('agent_web.socket.socket',return_value=stream),patch(
                'urllib.request.getproxies',return_value={'https':'http://fixture:password@localhost:7897'}),patch(
                'urllib.request.proxy_bypass',return_value=False),patch('ssl.SSLContext.wrap_socket',autospec=True,side_effect=wrap):
            self.assertEqual(agent_web.public_get('https://origin.example/page')[0],'ok')
        self.assertEqual(stream.targets,[('127.0.0.1',7897)])
        self.assertIn(b'CONNECT 93.184.216.34:443 ',stream.sent[0])
        self.assertIn(b'Proxy-Authorization:',stream.sent[0])
        self.assertNotIn(b'Proxy-Authorization:',stream.sent[1])
        self.assertIn(b'Host: origin.example',stream.sent[1]);self.assertEqual(tls,['origin.example'])
        self.assertEqual(lookups,['origin.example','localhost'])

    def test_redirect_is_validated_before_any_second_connection(self):
        redirect=b'HTTP/1.1 302 Found\r\nLocation: https://private.example/\r\nContent-Length: 0\r\n\r\n'
        stream=Socket([redirect]);lookups=[]
        def resolve(host,port,**kwargs):
            lookups.append(host);return [address('93.184.216.34' if len(lookups)==1 else '127.0.0.1')]
        with patch('agent_web.socket.getaddrinfo',side_effect=resolve),patch('agent_web.socket.socket',return_value=stream),patch(
                'urllib.request.getproxies',return_value={}),patch('ssl.SSLContext.wrap_socket',autospec=True,side_effect=lambda ctx,raw,**kw:raw):
            with self.assertRaises(ValueError):agent_web.public_get('https://public.example/')
        self.assertEqual(stream.targets,[('93.184.216.34',443)])
        self.assertEqual(lookups,['public.example','private.example'])

    def test_mixed_public_and_private_dns_answers_are_rejected(self):
        with patch('agent_web.socket.getaddrinfo',return_value=[address('93.184.216.34'),address('10.0.0.1')]),patch(
                'agent_web.socket.socket',side_effect=AssertionError('No socket should be opened')):
            with self.assertRaises(ValueError):agent_web.public_get('https://mixed.example/')

    def test_certificate_verification_failure_never_falls_back_to_plain_http(self):
        stream=Socket([])
        with patch('agent_web.socket.getaddrinfo',return_value=[address('93.184.216.34')]),patch('agent_web.socket.socket',return_value=stream),patch(
                'urllib.request.getproxies',return_value={}),patch('ssl.SSLContext.wrap_socket',side_effect=ssl.SSLCertVerificationError('synthetic cert failure')):
            with self.assertRaises(urllib.error.URLError):agent_web.public_get('https://certificate.example/')
        self.assertTrue(stream.closed);self.assertEqual(stream.sent,[])

    def test_legacy_mcp_setting_cannot_launch_an_unbounded_network_worker(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'configs').mkdir();(root/'configs/web-tools.local.json').write_text('{"reader":"mcp"}')
            with patch('agent_web.read_web_page',return_value={'ok':True,'url':'https://public.example/','text':'fixture'}),patch(
                    'subprocess.Popen',side_effect=AssertionError('External fetching must not be launched')):
                result=mcp_read.fetch(root,'https://public.example/')
            self.assertEqual(result['reader_effective'],'builtin_pinned_https');self.assertFalse(result['mcp_called'])
            self.assertTrue(result['safe_fallback']);self.assertEqual(result['tools_discovered'],[])


if __name__=='__main__':unittest.main()
