"""Public-web transport regressions. All sockets, DNS and TLS are mocked."""
import io
import os
import socket
import unittest
from unittest.mock import MagicMock, patch

from agent_web import public_get


def address(ip):
    return (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (ip, 443))


class PublicWebTransportTests(unittest.TestCase):
    def test_connection_uses_only_address_validated_before_dns_rebind(self):
        sock = MagicMock()
        sock.makefile.side_effect = lambda *a, **kw: io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok')
        context = MagicMock()
        context.wrap_socket.return_value = sock
        with patch.dict(os.environ, {}, clear=True), \
             patch('socket.getaddrinfo', side_effect=[[address('8.8.8.8')], [address('127.0.0.1')]]) as dns, \
             patch('socket.socket', return_value=sock), \
             patch('ssl._create_default_https_context', return_value=context):
            self.assertEqual(public_get('https://example.com/page'), ('ok', 'https://example.com/page'))
        self.assertEqual(dns.call_count, 1, 'DNS must not be resolved again after validation')
        sock.connect.assert_called_once_with(('8.8.8.8', 443))
        context.wrap_socket.assert_called_once_with(sock, server_hostname='example.com')

    def test_environment_proxy_is_not_used_for_public_web_reads(self):
        sock = MagicMock()
        sock.makefile.side_effect = lambda *a, **kw: io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok')
        context = MagicMock()
        context.wrap_socket.return_value = sock
        with patch.dict(os.environ, {'https_proxy': 'http://127.0.0.1:8888'}, clear=True), \
             patch('socket.getaddrinfo', return_value=[address('8.8.8.8')]) as dns, \
             patch('socket.socket', return_value=sock), \
             patch('ssl._create_default_https_context', return_value=context):
            self.assertEqual(public_get('https://example.com/page')[0], 'ok')
        self.assertEqual(dns.call_args_list[0].args[:2], ('example.com', 443))
        sock.connect.assert_called_once_with(('8.8.8.8', 443))
        self.assertFalse(any(b'CONNECT ' in call.args[0] for call in sock.sendall.call_args_list))

    def test_private_mixed_and_invalid_targets_never_open_socket(self):
        for url, addresses in [
            ('https://example.com', [address('127.0.0.1')]),
            ('https://example.com', [address('8.8.8.8'), address('10.0.0.1')]),
            ('https://example.com', []),
            ('http://example.com', [address('8.8.8.8')]),
            ('https://user:password@example.com', [address('8.8.8.8')]),
            ('https://example.com:444', [address('8.8.8.8')]),
        ]:
            with self.subTest(url=url, addresses=addresses), \
                 patch('socket.getaddrinfo', return_value=addresses), \
                 patch('socket.socket') as create_socket:
                with self.assertRaises(ValueError):
                    public_get(url)
                create_socket.assert_not_called()

    def test_private_redirect_is_blocked_before_second_connection(self):
        sock = MagicMock()
        stream = io.BytesIO(b'HTTP/1.1 302 Found\r\nLocation: https://private.example/secret\r\nContent-Length: 0\r\n\r\n')
        sock.makefile.return_value = stream
        context = MagicMock()
        context.wrap_socket.return_value = sock
        with patch('socket.getaddrinfo', side_effect=[[address('8.8.8.8')], [address('127.0.0.1')]]), \
             patch('socket.socket', return_value=sock) as create_socket, \
             patch('ssl._create_default_https_context', return_value=context):
            with self.assertRaisesRegex(ValueError, 'Local/private'):
                public_get('https://example.com/page')
        create_socket.assert_called_once()
        self.assertTrue(stream.closed)

    def test_relative_redirect_is_revalidated_and_keeps_correct_host(self):
        sockets = [MagicMock(), MagicMock()]
        sockets[0].makefile.return_value = io.BytesIO(b'HTTP/1.1 302 Found\r\nLocation: /final\r\nContent-Length: 0\r\n\r\n')
        sockets[1].makefile.return_value = io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok')
        context = MagicMock()
        context.wrap_socket.side_effect = lambda sock, **kwargs: sock
        with patch('socket.getaddrinfo', side_effect=[[address('8.8.8.8')], [address('1.1.1.1')]]) as dns, \
             patch('socket.socket', side_effect=sockets), \
             patch('ssl._create_default_https_context', return_value=context):
            self.assertEqual(public_get('https://example.com/page'), ('ok', 'https://example.com/final'))
        self.assertEqual(dns.call_count, 2)
        sockets[0].connect.assert_called_once_with(('8.8.8.8', 443))
        sockets[1].connect.assert_called_once_with(('1.1.1.1', 443))
        for sock in sockets:
            context.wrap_socket.assert_any_call(sock, server_hostname='example.com')
            self.assertIn(b'Host: example.com', sock.sendall.call_args.args[0])

    def test_ipv6_and_fallback_use_only_validated_addresses(self):
        ipv6 = (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('2606:4700:4700::1111', 443, 0, 0))
        failed, good = MagicMock(), MagicMock()
        failed.connect.side_effect = OSError('unreachable')
        good.makefile.return_value = io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok')
        context = MagicMock()
        context.wrap_socket.return_value = good
        with patch('socket.getaddrinfo', return_value=[address('8.8.8.8'), ipv6]) as dns, \
             patch('socket.socket', side_effect=[failed, good]), \
             patch('ssl._create_default_https_context', return_value=context):
            self.assertEqual(public_get('https://example.com/page')[0], 'ok')
        self.assertEqual(dns.call_count, 1)
        failed.close.assert_called_once()
        good.connect.assert_called_once_with(ipv6[4])

    def test_size_limit_and_tls_failure_are_not_bypassed(self):
        import ssl
        import urllib.error
        sock = MagicMock()
        sock.makefile.return_value = io.BytesIO(b'HTTP/1.1 200 OK\r\n\r\n' + b'x' * (2 * 1024**2 + 1))
        context = MagicMock()
        context.wrap_socket.return_value = sock
        with patch('socket.getaddrinfo', return_value=[address('8.8.8.8')]), \
             patch('socket.socket', return_value=sock), \
             patch('ssl._create_default_https_context', return_value=context):
            with self.assertRaisesRegex(ValueError, '2 MiB'):
                public_get('https://example.com/page')
            context.wrap_socket.side_effect = ssl.SSLCertVerificationError('bad certificate')
            with self.assertRaises(urllib.error.URLError):
                public_get('https://example.com/page')
