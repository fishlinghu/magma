"""
Copyright (c) 2016-present, Facebook, Inc.
All rights reserved.

This source code is licensed under the BSD-style license found in the
LICENSE file in the root directory of this source tree. An additional grant
of patent rights can be found in the PATENTS file in the same directory.
"""

import asyncio
import datetime
from concurrent import futures
from unittest import TestCase
from unittest.mock import ANY, MagicMock, call, patch

import grpc
import magma.magmad.bootstrap_manager as bm
from cryptography import x509
from cryptography.exceptions import InternalError
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.asymmetric.utils import \
    encode_dss_signature
from google.protobuf.timestamp_pb2 import Timestamp
from orc8r.protos import bootstrapper_pb2_grpc
from orc8r.protos.bootstrapper_pb2 import Challenge, ChallengeKey
from orc8r.protos.certifier_pb2 import CSR, Certificate

# Allow access to protected variables for unit testing
# pylint: disable=protected-access

BM = 'magma.magmad.bootstrap_manager'


# https://stackoverflow.com/questions/32480108/mocking-async-call-in-python-3-5
def AsyncMock():
    coro = MagicMock(name="CoroutineResult")
    corofunc = MagicMock(
        name="CoroutineFunction",
        side_effect=asyncio.coroutine(coro),
    )
    corofunc.coro = coro
    return corofunc


class DummpyBootstrapperServer(bootstrapper_pb2_grpc.BootstrapperServicer):
    def __init__(self):
        pass

    def add_to_server(self, server):
        bootstrapper_pb2_grpc.add_BootstrapperServicer_to_server(self, server)

    def GetChallenge(self, request, context):
        challenge = Challenge(
            challenge=b'simple_challenge',
            key_type=ChallengeKey.ECHO
        )
        return challenge

    def RequestSign(self, request, context):
        return create_cert_message()


class BootstrapManagerTest(TestCase):
    @patch('magma.common.cert_utils.write_key')
    @patch('%s.BootstrapManager._bootstrap_check' % BM)
    @patch('%s.snowflake.snowflake' % BM)
    @patch('%s.load_service_config' % BM)
    # Pylint doesn't handle decorators correctly
    # pylint: disable=arguments-differ, unused-argument
    def setUp(self,
              load_service_config_mock,
              snowflake_mock,
              bootstrap_check_mock,
              write_key_mock):

        self.gateway_key_file = '__test_gw.key'
        self.gateway_cert_file = '__test_hw_cert'
        self.hw_id = 'hwid_test'

        load_service_config_mock.return_value = {
            'gateway_key': self.gateway_key_file,
            'gateway_cert': self.gateway_cert_file,
        }
        snowflake_mock.return_value = self.hw_id

        service = MagicMock()
        asyncio.set_event_loop(None)
        service.loop = asyncio.new_event_loop()
        service.config = {
            'bootstrap_config': {
                'challenge_key': '__test_challenge.key',
            },
        }

        bootstrap_success_cb = MagicMock()

        self.manager = bm.BootstrapManager(service, bootstrap_success_cb)
        self.manager._bootstrap_success_cb = bootstrap_success_cb
        self.manager.start_bootstrap_manager()
        write_key_mock.assert_has_calls(
            [call(ANY, service.config['bootstrap_config']['challenge_key'])])

        # Bind the rpc server to a free port
        self._rpc_server = grpc.server(
            futures.ThreadPoolExecutor(max_workers=10)
        )
        port = self._rpc_server.add_insecure_port('0.0.0.0:0')
        # Add the servicer
        self._servicer = DummpyBootstrapperServer()
        self._servicer.add_to_server(self._rpc_server)
        self._rpc_server.start()
        # Create a rpc stub
        self.channel = grpc.insecure_channel('0.0.0.0:{}'.format(port))

        self.manager.SHORT_BOOTSTRAP_RETRY_INTERVAL = datetime.timedelta(
                seconds=0)
        self.manager.LONG_BOOTSTRAP_RETRY_INTERVAL = datetime.timedelta(
                seconds=0)

        # For on_checkin_fail tests
        self.host = 'host'
        self.port = 'port'
        self.cert = 'cert'
        self.key = 'key'

    def tearDown(self):
        self._rpc_server.stop(None)

    @patch('%s.BootstrapManager._bootstrap_now' % BM)
    def test_bootstrap(self, _bootstrap_now_mock):
        # boostrapping, no interruption
        self.manager._state = bm.BootstrapState.BOOTSTRAPPING
        self.manager.bootstrap()
        _bootstrap_now_mock.assert_not_called()

        self.manager._state = bm.BootstrapState.SCHEDULED
        self.manager._scheduled_event = MagicMock()
        self.manager.bootstrap()
        _bootstrap_now_mock.assert_has_calls([call()])
        self.manager._scheduled_event.cancel.assert_has_calls([call()])

    @patch('magma.common.cert_utils.load_cert')
    @patch('%s.BootstrapManager._bootstrap_now' % BM)
    @patch('%s.BootstrapManager._schedule_periodic_bootstrap_check' % BM)
    def test__bootstrap_check(self,
                              schedule_bootstrap_check_mock,
                              bootstrap_now_mock,
                              load_cert_mock):
        # cannot load cert
        load_cert_mock.side_effect = IOError
        self.manager._bootstrap_check()
        load_cert_mock.assert_has_calls([call(self.gateway_cert_file)])
        bootstrap_now_mock.assert_has_calls([call()])

        # invalid not_before
        load_cert_mock.reset_mock()
        load_cert_mock.side_effect = None # clear IOError side effect
        not_before = datetime.datetime.utcnow() + datetime.timedelta(days=3)
        not_after = not_before + datetime.timedelta(days=3)
        load_cert_mock.return_value = create_cert(not_before, not_after)
        self.manager._bootstrap_check()
        bootstrap_now_mock.assert_has_calls([call()])

        # invalid not_after
        load_cert_mock.reset_mock()
        not_before = datetime.datetime.utcnow()
        not_after = not_before + datetime.timedelta(hours=1)
        load_cert_mock.return_value = create_cert(not_before, not_after)
        self.manager._bootstrap_check()
        bootstrap_now_mock.assert_has_calls([call()])

        # cert is present and valid,
        load_cert_mock.reset_mock()
        not_before = datetime.datetime.utcnow()
        not_after = not_before + datetime.timedelta(days=10)
        load_cert_mock.return_value = create_cert(not_before, not_after)
        self.manager._bootstrap_check()
        schedule_bootstrap_check_mock.assert_has_calls([call()])

    @patch('%s.BootstrapManager._schedule_periodic_bootstrap_check' % BM)
    @patch('%s.ServiceRegistry.get_bootstrap_rpc_channel' % BM)
    @patch('%s.cert_utils.write_cert' % BM)
    @patch('magma.common.cert_utils.write_key')
    def test__bootstrap_now(self,
                            write_key_mock,
                            write_cert_mock,
                            bootstrap_channel_mock,
                            schedule_mock):

        def fake_schedule():
            self.manager._loop.stop()

        bootstrap_channel_mock.return_value = self.channel
        schedule_mock.side_effect = fake_schedule

        self.manager._bootstrap_now()
        self.manager._loop.run_forever()
        write_key_mock.assert_has_calls(
            [call(ANY, self.manager._gateway_key_file)])
        write_cert_mock.assert_has_calls(
            [call(ANY, self.manager._gateway_cert_file)])
        self.assertIs(self.manager._state, bm.BootstrapState.BOOTSTRAPPING)
        self.manager._bootstrap_success_cb.assert_has_calls([call(True)])

    @patch('%s.BootstrapManager._retry_bootstrap' % BM)
    @patch('%s.ServiceRegistry.get_bootstrap_rpc_channel' % BM)
    def test__bootstrap_fail(self,
                             bootstrap_channel_mock,
                             retry_bootstrap_mock):
        # test fail to get channel
        bootstrap_channel_mock.side_effect = ValueError
        self.manager._bootstrap_now()
        retry_bootstrap_mock.assert_has_calls([call(hard_failure=False)])
        # because retry is mocked, state should still be bootstrapping
        self.assertIs(self.manager._state, bm.BootstrapState.BOOTSTRAPPING)

    @patch('%s.ec.generate_private_key' % BM)
    @patch('%s.BootstrapManager._retry_bootstrap' % BM)
    def test__get_challenge_done_pk_exception(self, retry_bootstrap_mock, generate_pk_mock):
        future = MagicMock()
        future.exception = lambda: None
        # Private key generation returns error
        generate_pk_mock.side_effect = InternalError("", 0)
        self.manager._get_challenge_done(future)
        retry_bootstrap_mock.assert_has_calls([call(hard_failure=True)])

    @patch('%s.BootstrapManager._retry_bootstrap' % BM)
    @patch('%s.BootstrapManager._request_sign' % BM)
    def test__get_challenge_done(self, request_sign_mock, retry_bootstrap_mock):
        future = MagicMock()

        # GetChallenge returns error
        self.manager._get_challenge_done(future)
        retry_bootstrap_mock.assert_has_calls([call(hard_failure=False)])

        # Fail to construct response
        retry_bootstrap_mock.reset_mock()
        future.exception = lambda: None
        self.manager._get_challenge_done(future)
        retry_bootstrap_mock.assert_has_calls([call(hard_failure=True)])

        # No error
        retry_bootstrap_mock.reset_mock()
        self.manager._loop = MagicMock()
        future.result = lambda: Challenge(
            challenge=b'simple_challenge',
            key_type=ChallengeKey.ECHO
        )
        self.manager._get_challenge_done(future)
        retry_bootstrap_mock.assert_not_called()
        request_sign_mock.assert_has_calls([call(ANY)])

    @patch('%s.BootstrapManager._retry_bootstrap' % BM)
    @patch('%s.ServiceRegistry.get_bootstrap_rpc_channel' % BM)
    def test__request_sign(self, bootstrap_channel_mock, retry_bootstrap_mock):
        challenge = Challenge(
            challenge=b'simple_challenge',
            key_type=ChallengeKey.ECHO
        )
        self.manager._gateway_key = ec.generate_private_key(
            ec.SECP384R1(), default_backend())
        csr = self.manager._create_csr()
        response = self.manager._construct_response(challenge, csr)

        # test fail to get channel
        bootstrap_channel_mock.side_effect = ValueError
        self.manager._request_sign(response)
        retry_bootstrap_mock.assert_has_calls([call(hard_failure=False)])

        # test no error
        retry_bootstrap_mock.reset_mock()
        bootstrap_channel_mock.reset_mock()
        bootstrap_channel_mock.side_effect = None
        bootstrap_channel_mock.return_value = self.channel
        self.manager._request_sign(response)
        retry_bootstrap_mock.assert_not_called()

    @patch('%s.cert_utils.write_cert' % BM)
    @patch('%s.cert_utils.write_key' % BM)
    @patch('%s.BootstrapManager._schedule_periodic_bootstrap_check' % BM)
    @patch('%s.BootstrapManager._retry_bootstrap' % BM)
    def test__request_sign_done(self,
                                retry_bootstrap_mock,
                                schedule_bootstrap_check_mock,
                                write_key_mock,
                                write_cert_mock):
        future = MagicMock()

        # RequestSign returns error
        self.manager._request_sign_done(future)
        retry_bootstrap_mock.assert_has_calls([call(hard_failure=False)])

        # certificate is invalid
        retry_bootstrap_mock.reset_mock()
        future.exception = lambda: None
        not_before = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
        invalid_cert = create_cert_message(not_before=not_before)
        future.result = lambda: invalid_cert
        self.manager._request_sign_done(future)
        retry_bootstrap_mock.assert_has_calls([call(hard_failure=True)])

        # certificate is valid
        retry_bootstrap_mock.reset_mock()
        valid_cert = create_cert_message()
        future.result = lambda: valid_cert
        self.manager._request_sign_done(future)
        self.manager._bootstrap_success_cb.assert_has_calls([call(True)])
        retry_bootstrap_mock.assert_not_called()
        write_key_mock.assert_has_calls(
            [call(ANY, self.manager._gateway_key_file)])
        write_cert_mock.assert_has_calls(
            [call(ANY, self.manager._gateway_cert_file)])

        schedule_bootstrap_check_mock.assert_has_calls([call()])

    def test__retry_bootstrap(self):
        self.manager._loop = MagicMock()
        self.manager.LONG_BOOTSTRAP_RETRY_INTERVAL = datetime.timedelta(
                seconds=1)
        self.manager.SHORT_BOOTSTRAP_RETRY_INTERVAL = datetime.timedelta(
                seconds=0)
        self.manager._state = bm.BootstrapState.BOOTSTRAPPING

        self.manager._retry_bootstrap(False)
        self.manager._loop.call_later.assert_has_calls(
            [call(0, self.manager._bootstrap_now)])
        self.assertIs(self.manager._state, bm.BootstrapState.SCHEDULED)

        self.manager._state = bm.BootstrapState.BOOTSTRAPPING
        self.manager._retry_bootstrap(True)
        self.manager._loop.call_later.assert_has_calls(
            [call(1, self.manager._bootstrap_now)])
        self.assertIs(self.manager._state, bm.BootstrapState.SCHEDULED)

    def test__schedule_periodic_bootstrap_check(self):
        self.manager._loop = MagicMock()
        self.manager._state = bm.BootstrapState.BOOTSTRAPPING
        self.manager._schedule_periodic_bootstrap_check()
        self.manager._loop.call_later.assert_has_calls(
            [call(self.manager.PERIODIC_BOOTSTRAP_CHECK_INTERVAL.total_seconds(),
                  self.manager._bootstrap_check)])
        self.assertIs(self.manager._state, bm.BootstrapState.SCHEDULED)

    def test__create_csr(self):
        self.manager._gateway_key = ec.generate_private_key(
            ec.SECP384R1(), default_backend())
        csr_msg = self.manager._create_csr()
        self.assertEqual(csr_msg.id.gateway.hardware_id, self.hw_id)

    @patch('magma.common.cert_utils.load_key')
    def test__construct_response(self, load_key_mock):
        ecdsa_key = ec.generate_private_key(ec.SECP384R1(), default_backend())

        key_types = {
            ChallengeKey.ECHO: None,
            ChallengeKey.SOFTWARE_ECDSA_SHA256: ecdsa_key,
        }
        for key_type, key in key_types.items():
            load_key_mock.return_value = key
            challenge = Challenge(key_type=key_type, challenge=b'challenge')
            response = self.manager._construct_response(challenge, CSR())
            self.assertEqual(response.hw_id.id, self.hw_id)
            self.assertEqual(response.challenge, challenge.challenge)

        challenge = Challenge(key_type=5, challenge=b'crap challenge')
        with self.assertRaises(
                bm.BootstrapError,
                msg='Unknown key type: %s' % challenge.key_type):
            self.manager._construct_response(challenge, CSR())

    @patch('magma.common.cert_utils.load_key')
    def test__ecdsa_sha256_response(self, load_key_mock):
        challenge = b'challenge'

        # success case
        private_key = ec.generate_private_key(ec.SECP384R1(), default_backend())
        load_key_mock.return_value = private_key
        r, s = self.manager._ecdsa_sha256_response(challenge)
        r = int.from_bytes(r, 'big')
        s = int.from_bytes(s, 'big')
        signature = encode_dss_signature(r, s)
        private_key.public_key().verify(
            signature, challenge, ec.ECDSA(hashes.SHA256()))

        # no key found
        load_key_mock.reset_mock()
        load_key_mock.side_effect = IOError
        with self.assertRaises(bm.BootstrapError):
            self.manager._ecdsa_sha256_response(challenge)

        # wrong type of key, e.g. rsa
        load_key_mock.reset_mock()
        load_key_mock.return_value = rsa.generate_private_key(
            65537, 2048, default_backend())
        with self.assertRaises(
                bm.BootstrapError,
                msg='Challenge key cannot be used for ECDSA signature'):
            self.manager._ecdsa_sha256_response(challenge)

    def test__is_valid_certificate(self):
        # not-yet-valid
        not_before = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
        cert = create_cert_message(not_before=not_before)
        is_valid = self.manager._is_valid_certificate(cert)
        self.assertFalse(is_valid)

        # expiring soon
        not_before = datetime.datetime.utcnow()
        not_after = not_before + datetime.timedelta(hours=1)
        cert = create_cert_message(not_before=not_before, not_after=not_after)
        is_valid = self.manager._is_valid_certificate(cert)
        self.assertFalse(is_valid)

        # correct
        cert = create_cert_message()
        is_valid = self.manager._is_valid_certificate(cert)
        self.assertTrue(is_valid)

    @patch('%s.ServiceRegistry.get_proxy_config' % BM)
    @patch('%s.cert_is_invalid' % BM, new_callable=AsyncMock)
    def test__on_checkin_fail(
        self,
        mock_cert_is_invalid,
        mock_get_proxy_config,
    ):
        mock_get_proxy_config.return_value = {
            'cloud_address': self.host,
            'cloud_port': self.port,
            'gateway_cert': self.cert,
            'gateway_key': self.key,
        }
        self.manager._bootstrap_now = MagicMock(name='_bootstrap_now')

        future = self.manager.on_checkin_fail(grpc.StatusCode.UNKNOWN)
        self.manager._loop.run_until_complete(future)

        mock_cert_is_invalid.assert_called_once_with(
            self.host, self.port, self.cert, self.key, self.manager._loop
        )
        self.assertEqual(self.manager._bootstrap_now.call_count, 1)

    @patch('%s.ServiceRegistry.get_proxy_config' % BM)
    @patch('%s.cert_is_invalid' % BM, new_callable=AsyncMock)
    def test__on_checkin_fail_cert_valid(
        self,
        mock_cert_is_invalid,
        mock_get_proxy_config,
    ):
        mock_get_proxy_config.return_value = {
            'cloud_address': self.host,
            'cloud_port': self.port,
            'gateway_cert': self.cert,
            'gateway_key': self.key,
        }
        mock_cert_is_invalid.coro.return_value = False

        self.manager._bootstrap_now = MagicMock(name='_bootstrap_now')

        future = self.manager.on_checkin_fail(grpc.StatusCode.UNKNOWN)
        self.manager._loop.run_until_complete(future)

        self.assertEqual(self.manager._bootstrap_now.call_count, 0)


def create_cert(not_before, not_after):
    key = rsa.generate_private_key(65537, 2048, default_backend())

    subject = issuer = x509.Name([
        x509.NameAttribute(x509.oid.NameOID.COUNTRY_NAME, u"US"),
        x509.NameAttribute(x509.oid.NameOID.STATE_OR_PROVINCE_NAME, u"CA"),
        x509.NameAttribute(x509.oid.NameOID.LOCALITY_NAME, u"San Francisco"),
        x509.NameAttribute(x509.oid.NameOID.ORGANIZATION_NAME, u"My Company"),
        x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, u"mysite.com"),
    ])

    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        not_before
    ).not_valid_after(
        not_after
    ).sign(key, hashes.SHA256(), default_backend())

    return cert


def create_cert_message(not_before=None, not_after=None):
    if not_before is None:
        not_before = datetime.datetime.utcnow()
    if not_after is None:
        not_after = not_before + datetime.timedelta(days=10)

    cert = create_cert(not_before, not_after)

    not_before_stamp = Timestamp()
    not_before_stamp.FromDatetime(not_before)

    not_after_stamp = Timestamp()
    not_after_stamp.FromDatetime(not_after)

    dummy_cert = Certificate(
        cert_der=cert.public_bytes(serialization.Encoding.DER),
        not_before=not_before_stamp,
        not_after=not_after_stamp,
    )
    return dummy_cert
