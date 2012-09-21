#!/usr/bin/python
# -*- coding: utf-8 -*-
# testrunner timeout: 300
from __future__ import with_statement
import sys
import re
import traceback
import greentest
import socket
from time import time
import gevent
import gevent.socket as gevent_socket
from util import log

RAISE_TOO_SLOW = False
# also test: '<broadcast>'

resolver = gevent.get_hub().resolver
log('Resolver: %s', resolver)


if getattr(resolver, 'pool', None) is not None:
    resolver.pool.size = 1


assert gevent_socket.gaierror is socket.gaierror
assert gevent_socket.error is socket.error

VERBOSE = sys.argv.count('-v') + 2 * sys.argv.count('-vv')
VERBOSE = 2
PASS = True
LOGFILE = sys.stderr


def log(s, *args, **kwargs):
    newline = kwargs.pop('newline', True)
    assert not kwargs, kwargs
    if not VERBOSE:
        return
    try:
        s = s % args
    except Exception:
        traceback.print_exc()
        s = '%s %r' % (s, args)
    if newline:
        s += '\n'
    LOGFILE.write(s)


def _run(function, *args):
    try:
        result = function(*args)
        assert not isinstance(result, BaseException), repr(result)
        return result
    except Exception:
        return sys.exc_info()[1]


def log_fcall(function, args):
    args = repr(args)
    if args.endswith(',)'):
        args = args[:-2] + ')'
    log('\n%7s.%s%s',
        function.__module__.replace('gevent.socket', 'gevent'),
        function.__name__,
        args,
        newline=False)


def log_fresult(result, seconds):
    if isinstance(result, Exception):
        log(' -> raised %r (%.3f)', result, seconds * 1000.0)
    else:
        log(' -> returned %r (%.3f)', result, seconds * 1000.0)


def run(function, *args):
    if VERBOSE >= 2:
        log_fcall(function, args)
    delta = time()
    result = _run(function, *args)
    delta = time() - delta
    if VERBOSE >= 2:
        log_fresult(result, delta)
    return result, delta


def log_call(result, time, function, *args):
    log_fcall(function, args)
    log_fresult(result, time)


google_host_re = re.compile('^arn[a-z0-9-]+.1e100.net$')


def compare_ipv6(a, b):
    """
    >>> compare_ipv6('2a00:1450:400f:801::1010', '2a00:1450:400f:800::1011')
    True
    >>> compare_ipv6('2a00:1450:400f:801::1010', '2aXX:1450:400f:900::1011')
    False
    """
    if a.count(':') == 5 and b.count(':') == 5:
        return a.rsplit(':')[:-3] == b.rsplit(':')[:-3]
    if google_host_re.match(a) and google_host_re.match(b):
        return True
    return a == b


def relaxed_is_equal(a, b):
    """
    >>> relaxed_is_equal([(10, 1, 6, '', ('2a00:1450:400f:801::1010', 80, 0, 0))], [(10, 1, 6, '', ('2a00:1450:400f:800::1011', 80, 0, 0))])
    True
    >>> relaxed_is_equal([1, '2'], (1, '2'))
    False
    >>> relaxed_is_equal([1, '2'], [1, '2'])
    True
    """
    if type(a) is not type(b):
        return False
    if isinstance(a, basestring):
        return compare_ipv6(a, b)
    if hasattr(a, '__iter__'):
        if len(a) != len(b):
            return False
        return all(relaxed_is_equal(x, y) for (x, y) in zip(a, b))
    return a == b


def add(klass, hostname, name=None):

    call = callable(hostname)

    if name is None:
        if call:
            name = hostname.__name__
        else:
            name = re.sub('[^\w]+', '_', repr(hostname))
        assert name, repr(hostname)

    def test1(self):
        x = hostname() if call else hostname
        self._test('getaddrinfo', x, 'http')
    test1.__name__ = 'test_%s_getaddrinfo' % name
    setattr(klass, test1.__name__, test1)

    def test2(self):
        x = hostname() if call else hostname
        ipaddr = self._test('gethostbyname', x)
        if not isinstance(ipaddr, Exception):
            self._test('gethostbyaddr', ipaddr)
    test2.__name__ = 'test_%s_gethostbyname' % name
    setattr(klass, test2.__name__, test2)

    def test3(self):
        x = hostname() if call else hostname
        self._test('gethostbyname_ex', x)
    test3.__name__ = 'test_%s_gethostbyname_ex' % name
    setattr(klass, test3.__name__, test3)

    def test4(self):
        x = hostname() if call else hostname
        self._test('gethostbyaddr', x)
    test4.__name__ = 'test_%s_gethostbyaddr' % name
    setattr(klass, test4.__name__, test4)

    def test5(self):
        x = hostname() if call else hostname
        self._test('getnameinfo', (x, 80), 0)
    test5.__name__ = 'test_%s_getnameinfo' % name
    setattr(klass, test5.__name__, test5)


class TooSlow(AssertionError):
    pass


class TestCase(greentest.TestCase):

    __timeout__ = 60
    switch_expected = None

    def _test_once(self, func, *args):
        gevent_func = getattr(gevent_socket, func)
        real_func = getattr(socket, func)
        real_result, time_real = run(real_func, *args)
        result, time_gevent = run(gevent_func, *args)
        if VERBOSE == 1 and repr(result) != repr(real_result):
            # slightly less verbose mode: only print the results that are different
            log_call(result, time_gevent, gevent_func, *args)
            log_call(real_result, time_real, real_func, *args)
            log('')
        elif VERBOSE >= 2:
            log('')
        self.assertEqualResults(real_result, result, func)
        if isinstance(real_result, Exception):
            if isinstance(result, Exception):
                # built-in socket module is faster at raising exceptions
                allowed = 2.
            else:
                # built-in socket module raised an error, gevent made a real query
                allowed = 100000.
        else:
            allowed = 1.2
        if time_gevent / allowed > time_real and (time_gevent + time_real) > 0.0005:
            # QQQ use clock() on windows
            times = None
            if not time_real:
                if time_gevent:
                    times = time_gevent / 0.001
                else:
                    times = 1
            if times is None:
                times = time_gevent / time_real
            params = (func, args, times, time_gevent * 1000.0, time_real * 1000.0)
            raise TooSlow('gevent_socket.%s%s is %.1f times slower (%.3fms versus %.3fms)' % params)
        return result

    def _test(self, func, *args):
        try:
            return self._test_once(func, *args)
        except TooSlow, ex:
            if RAISE_TOO_SLOW:
                raise
            else:
                if not VERBOSE:
                    log('')
                log('WARNING: %s', ex)

    def assertEqualResults(self, real_result, gevent_result, func):
        errors = [socket.gaierror, socket.herror, TypeError]
        if type(real_result) in errors and type(gevent_result) in errors:
            if type(real_result) is not type(gevent_result):
                log('WARNING: error type mismatch: %r (gevent) != %r (stdlib)', gevent_result, real_result)
            return
        real_result_repr = repr(real_result)
        gevent_result_repr = repr(gevent_result)
        if real_result_repr == gevent_result_repr:
            return
        if relaxed_is_equal(gevent_result, real_result):
            return
        raise AssertionError('%r != %r' % (gevent_result, real_result))


class TestTypeError(TestCase):
    pass

add(TestTypeError, None)
add(TestTypeError, 25)


class TestHostname(TestCase):
    pass

add(TestHostname, socket.gethostname)


class TestLocalhost(TestCase):
    # certain tests in test_patched_socket.py only work if getaddrinfo('localhost') does not switch
    # (e.g. NetworkConnectionAttributesTest.testSourceAddress)
    pass
    #switch_expected = False

add(TestLocalhost, 'localhost')
add(TestLocalhost, 'ip6-localhost')


class TestNonexistent(TestCase):
    pass

add(TestNonexistent, 'nonexistentxxxyyy')


class Test1234(TestCase):
    pass

add(Test1234, '1.2.3.4')


class Test127001(TestCase):
    pass

add(Test127001, '127.0.0.1')


# class TestBroadcast(TestCase):
#     switch_expected = False
#
# add(TestBroadcast, '<broadcast>')


class TestEtcHosts(TestCase):
    pass

try:
    etc_hosts = open('/etc/hosts').read()
except IOError:
    etc_hosts = ''

for ip, host in re.findall(r'^\s*(\d+\.\d+\.\d+\.\d+)\s+([^\s]+)', etc_hosts, re.M)[:10]:
    add(TestEtcHosts, host)
    add(TestEtcHosts, ip)
    del host, ip


class TestGeventOrg(TestCase):
    pass

add(TestGeventOrg, 'gevent.org')


class TestFamily(TestCase):

    @classmethod
    def getresult(cls):
        if not hasattr(cls, '_result'):
            cls._result = getattr(socket, 'getaddrinfo')('gevent.org', None)
        return cls._result

    def assert_error(self, error, function, *args):
        try:
            result = function(*args)
            raise AssertionError('%s: Expected to raise %s, instead returned %r' % (function, error, result))
        except Exception, ex:
            if isinstance(error, basestring):
                repr_error = error
            else:
                repr_error = repr(error)
                if type(ex) is not type(error):
                    raise
            if repr(ex) == repr_error:
                return
            raise

    def test_inet(self):
        self.assertEqual(gevent_socket.getaddrinfo('gevent.org', None, socket.AF_INET), self.getresult())

    def test_unspec(self):
        self.assertEqual(gevent_socket.getaddrinfo('gevent.org', None, socket.AF_UNSPEC), self.getresult())

    def test_badvalue(self):
        self._test('getaddrinfo', 'gevent.org', None, 255)
        self._test('getaddrinfo', 'gevent.org', None, 255000)
        self._test('getaddrinfo', 'gevent.org', None, -1)

    def test_badtype(self):
        self._test('getaddrinfo', 'gevent.org', 'x')


class Test_getaddrinfo(TestCase):

    def _test_getaddrinfo(self, *args):
        self._test('getaddrinfo', *args)

    def test_80(self):
        self._test_getaddrinfo('gevent.org', 80)

    def test_int_string(self):
        self._test_getaddrinfo('gevent.org', '80')

    def test_0(self):
        self._test_getaddrinfo('gevent.org', 0)

    def test_http(self):
        self._test_getaddrinfo('gevent.org', 'http')

    def test_notexistent_tld(self):
        self._test_getaddrinfo('myhost.mytld', 53)

    def test_notexistent_dot_com(self):
        self._test_getaddrinfo('sdfsdfgu5e66098032453245wfdggd.com', 80)

    def test1(self):
        return self._test_getaddrinfo('gevent.org', 52, socket.AF_UNSPEC, socket.SOCK_STREAM, 0, 0)

    def test2(self):
        return self._test_getaddrinfo('gevent.org', 53, socket.AF_INET, socket.SOCK_DGRAM, 17)

    def test3(self):
        return self._test_getaddrinfo('google.com', 'http', socket.AF_INET6)


class TestInternational(TestCase):
    pass

add(TestInternational, u'президент.рф', 'russian')
add(TestInternational, u'президент.рф'.encode('idna'), 'idna')


class TestInterrupted_gethostbyname(greentest.GenericWaitTestCase):

    def wait(self, timeout):
        with gevent.Timeout(timeout, False):
            for index in xrange(1000000):
                try:
                    gevent_socket.gethostbyname('www.x%s.com' % index)
                except socket.error:
                    pass
            raise AssertionError('Timeout was not raised')


# class TestInterrupted_getaddrinfo(greentest.GenericWaitTestCase):
#
#     def wait(self, timeout):
#         with gevent.Timeout(timeout, False):
#             for index in range(1000):
#                 try:
#                     gevent_socket.getaddrinfo('www.a%s.com' % index, 'http')
#                 except socket.gaierror:
#                     pass


class Test6(TestCase):
    pass

    # host that only has AAAA record
    host = 'aaaa.test-ipv6.com'

    def test_empty(self):
        self._test('getaddrinfo', self.host, 'http')

    def test_inet(self):
        self._test('getaddrinfo', self.host, None, socket.AF_INET)

    def test_inet6(self):
        self._test('getaddrinfo', self.host, None, socket.AF_INET6)

    def test_unspec(self):
        self._test('getaddrinfo', self.host, None, socket.AF_UNSPEC)


class Test6_google(Test6):
    host = 'ipv6.google.com'


class Test6_ds(Test6):
    # host that has both A and AAAA records
    host = 'ds.test-ipv6.com'


add(Test6, Test6.host)
add(Test6_google, Test6_google.host)
add(Test6_ds, Test6_ds.host)


class TestBadName(TestCase):
    pass

add(TestBadName, 'xxxxxxxxxxxx')


class TestBadIP(TestCase):
    pass

add(TestBadIP, '1.2.3.400')


class Test_getnameinfo_127001(TestCase):

    def test(self):
        assert gevent_socket.getnameinfo is not socket.getnameinfo
        self._test('getnameinfo', ('127.0.0.1', 80), 0)

    def test_DGRAM(self):
        self._test('getnameinfo', ('127.0.0.1', 779), 0)
        self._test('getnameinfo', ('127.0.0.1', 779), socket.NI_DGRAM)

    def test_NOFQDN(self):
        # I get ('localhost', 'www') with _socket but ('localhost.localdomain', 'www') with gevent.socket
        self._test('getnameinfo', ('127.0.0.1', 80), socket.NI_NOFQDN)

    def test_NAMEREQD(self):
        self._test('getnameinfo', ('127.0.0.1', 80), socket.NI_NAMEREQD)


class Test_getnameinfo_geventorg(TestCase):

    def test_NUMERICHOST(self):
        self._test('getnameinfo', ('gevent.org', 80), 0)
        self._test('getnameinfo', ('gevent.org', 80), socket.NI_NUMERICHOST)

    def test_NUMERICSERV(self):
        self._test('getnameinfo', ('gevent.org', 80), socket.NI_NUMERICSERV)

    def test_domain1(self):
        self._test('getnameinfo', ('gevent.org', 80), 0)

    def test_domain2(self):
        self._test('getnameinfo', ('www.gevent.org', 80), 0)

    def test_port_zero(self):
        self._test('getnameinfo', ('www.gevent.org', 0), 0)


class Test_getnameinfo_fail(TestCase):

    def test_port_string(self):
        self._test('getnameinfo', ('www.gevent.org', 'http'), 0)

    def test_bad_flags(self):
        self._test('getnameinfo', ('127.0.0.1', 80), 55555555)


class TestInvalidPort(TestCase):

    def test1(self):
        try:
            self._test('getnameinfo', ('www.gevent.org', -1), 0)
        except AssertionError, ex:
            # XXX to fix
            log('ERROR: %s', ex)

    def test2(self):
        self._test('getnameinfo', ('www.gevent.org', None), 0)

    def test3(self):
        self._test('getnameinfo', ('www.gevent.org', 'x'), 0)

    def test4(self):
        self._test('getnameinfo', ('www.gevent.org', 65536), 0)


if __name__ == '__main__':
    greentest.main()
