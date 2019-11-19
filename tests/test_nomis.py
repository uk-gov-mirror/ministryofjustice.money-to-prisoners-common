import datetime
import json
from unittest import mock

from django.conf import settings
from django.core import cache as django_cache
from django.test import SimpleTestCase, override_settings
from requests.exceptions import ConnectionError, HTTPError
import responses

from mtp_common import nomis
from mtp_common.auth import urljoin
from mtp_common.test_utils import local_memory_cache, silence_logger


def _build_elite_nomis_api_url(path):
    return urljoin(settings.NOMIS_ELITE_BASE_URL, '/elite2api/api/v1', path, trailing_slash=False)


class RequestRetryTestCase(SimpleTestCase):
    """
    Tests related to the request_retry function.
    """

    def test_retries_successfully_after_exception(self):
        """
        Test that a successful request is automatically made after a failed one.
        The failed request triggers a ConnectionError.
        """
        url = 'https://example.com'
        successful_content = b'some content'

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                url,
                body=ConnectionError(),
            )
            rsps.add(
                responses.GET,
                url,
                body=successful_content,
                status=200,
            )

            response = nomis.request_retry('get', url, retries=1)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, successful_content)

    def test_retries_successfully_after_erroneous_status_code(self):
        """
        Test that a successful request is automatically made after a failed one.
        The failed request has a status_code in the retry_on_status.
        """
        url = 'https://example.com'
        successful_content = b'some content'

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                url,
                status=500,
            )
            rsps.add(
                responses.GET,
                url,
                body=successful_content,
                status=200,
            )

            response = nomis.request_retry('get', url, retries=1)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, successful_content)

    def test_retries_successfully_after_specific_erroneous_status_code(self):
        """
        Test that a successful request is automatically made after a failed one.
        The failed request has a status_code in the passed-in retry_on_status.
        """
        url = 'https://example.com'
        successful_content = b'some content'

        retry = nomis.Retry(1, retry_on_status=(423, ))
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                url,
                status=423,
            )
            rsps.add(
                responses.GET,
                url,
                body=successful_content,
                status=200,
            )

            response = nomis.request_retry('get', url, retries=retry)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, successful_content)

    def test_max_retries_exceeded_triggers_exception(self):
        """
        Test that the exception triggered by the last retry is propagated after retrying `retries` times.
        """
        url = 'https://example.com'
        retries = 2

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                url,
                body=ConnectionError(),
            )

            with self.assertRaises(ConnectionError):
                nomis.request_retry('get', url, retries=retries)
            self.assertEqual(len(rsps.calls), retries+1)

    def test_max_retries_exceeded_returns_status_code(self):
        """
        Test that the status_code returned by the last retry is propagated after retrying `retries` times.
        """
        url = 'https://example.com'
        retries = 2

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                url,
                status=500,
            )

            response = nomis.request_retry('get', url, retries=retries)
            self.assertEqual(response.status_code, 500)
            self.assertEqual(len(rsps.calls), retries+1)

    def test_shouldnt_retry(self):
        """
        Test that if retries = 0, the function doesn't retry following a failed request.
        """
        url = 'https://example.com'

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                url,
                status=500,
            )

            response = nomis.request_retry('get', url, retries=0)
            self.assertEqual(response.status_code, 500)
            self.assertEqual(len(rsps.calls), 1)

    def test_shouldnt_retry_if_status_code_ignored(self):
        """
        Test that the function doesn't retry if the status code of the first attempt is not
        in the retry_on_status list even when `retries` > 0.
        """
        url = 'https://example.com'

        retry = nomis.Retry(1, retry_on_status=(401, ))
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                url,
                status=500,
            )

            response = nomis.request_retry('get', url, retries=retry)
            self.assertEqual(response.status_code, 500)
            self.assertEqual(len(rsps.calls), 1)


@mock.patch.object(nomis, 'connector', nomis.LegacyNomisConnector())
class LegacyNomisApiTestCase(SimpleTestCase):
    """
    TODO: Remove once all apps move to NOMIS Elite2
    """
    @override_settings(NOMIS_API_CLIENT_TOKEN='abc.abc.abc')
    def test_client_token_taken_from_settings(self):
        self.assertEqual(nomis.connector.get_client_token(), settings.NOMIS_API_CLIENT_TOKEN)
        self.assertTrue(nomis.can_access_nomis())

    @override_settings(NOMIS_API_CLIENT_TOKEN='')
    def test_cannot_access_nomis_without_token(self):
        self.assertFalse(nomis.can_access_nomis())

    def test_get_account_balances(self):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                urljoin(settings.NOMIS_API_BASE_URL, '/prison/BMI/offenders/A1471AE/accounts/'),
                json={
                    'cash': 500,
                    'savings': 0,
                    'spends': 25,
                },
                status=200,
            )

            balances = nomis.get_account_balances('BMI', 'A1471AE')
            self.assertEqual(balances, {'cash': 500, 'savings': 0, 'spends': 25})

    def test_retry_on_connection_error(self):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                urljoin(settings.NOMIS_API_BASE_URL, '/prison/BMI/offenders/A1471AE/accounts/'),
                body=ConnectionError()
            )
            rsps.add(
                responses.GET,
                urljoin(settings.NOMIS_API_BASE_URL, '/prison/BMI/offenders/A1471AE/accounts/'),
                json={
                    'cash': 500,
                    'savings': 0,
                    'spends': 25,
                },
                status=200,
            )

            balances = nomis.get_account_balances('BMI', 'A1471AE', retries=1)
            self.assertEqual(balances, {'cash': 500, 'savings': 0, 'spends': 25})

    def assertHousingFormatStructure(self, nomis_mocked_response, expected_location_dict):  # noqa: N802
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                urljoin(settings.NOMIS_API_BASE_URL, '/offenders/A1401AE/location/'),
                json=nomis_mocked_response
            )
            actual_location_dict = nomis.get_location('A1401AE')
        self.assertEqual(actual_location_dict, expected_location_dict)

    def test_housing_location_no_housing(self):
        self.assertHousingFormatStructure(
            {
                'establishment': {
                    'code': 'BXI',
                    'desc': 'BRIXTON (HMP)'
                }
            },
            {'nomis_id': 'BXI', 'name': 'BRIXTON (HMP)'}
        )

    def test_housing_location_dict_housing(self):
        self.assertHousingFormatStructure(
            {
                'establishment': {
                    'code': 'BXI',
                    'desc': 'BRIXTON (HMP)'
                },
                'housing_location': {
                    'description': 'BXI-H-2-001',
                    'levels': [
                        {'type': 'Wing', 'value': 'H'},
                        {'type': 'Landing', 'value': '2'},
                        {'type': 'Cell', 'value': '001'},
                    ]
                }
            },
            {
                'nomis_id': 'BXI',
                'name': 'BRIXTON (HMP)',
                'housing_location': {
                    'description': 'BXI-H-2-001',
                    'levels': [
                        {'type': 'Wing', 'value': 'H'},
                        {'type': 'Landing', 'value': '2'},
                        {'type': 'Cell', 'value': '001'},
                    ]
                }
            }
        )

    def test_housing_location_level_variations(self):
        self.assertHousingFormatStructure(
            {
                'establishment': {'code': 'HEI', 'desc': 'HMP HEWELL'},
                'housing_location': {
                    'description': 'HEI-1-1-A-001',
                    'levels': [
                        {'type': 'Block', 'value': '1'},
                        {'type': 'Tier', 'value': '1'},
                        {'type': 'Spur', 'value': 'A'},
                        {'type': 'Cell', 'value': '001'},
                    ]
                }
            },
            {
                'nomis_id': 'HEI', 'name': 'HMP HEWELL',
                'housing_location': {
                    'description': 'HEI-1-1-A-001',
                    'levels': [
                        {'type': 'Block', 'value': '1'},
                        {'type': 'Tier', 'value': '1'},
                        {'type': 'Spur', 'value': 'A'},
                        {'type': 'Cell', 'value': '001'},
                    ]
                }
            }
        )
        self.assertHousingFormatStructure(
            {
                'establishment': {'code': 'BZI', 'desc': 'BRONZEFIELD (HMP)'},
                'housing_location': {
                    'description': 'BZI-A-A-001',
                    'levels': [
                        {'type': 'Block', 'value': 'A'},
                        {'type': 'Landing', 'value': 'A'},
                        {'type': 'Cell', 'value': '001'},
                    ]
                }
            },
            {
                'nomis_id': 'BZI', 'name': 'BRONZEFIELD (HMP)',
                'housing_location': {
                    'description': 'BZI-A-A-001',
                    'levels': [
                        {'type': 'Block', 'value': 'A'},
                        {'type': 'Landing', 'value': 'A'},
                        {'type': 'Cell', 'value': '001'},
                    ]
                }
            }
        )

    def test_housing_location_absent_levels(self):
        self.assertHousingFormatStructure(
            {
                'establishment': {'code': 'WWI', 'desc': 'WANDSWORTH (HMP)'},
                'housing_location': {
                    'description': 'WWI-COURT',
                }
            },
            {
                'nomis_id': 'WWI', 'name': 'WANDSWORTH (HMP)',
                'housing_location': {
                    'description': 'WWI-COURT',
                    'levels': []
                }
            }
        )

    def test_housing_location_absent_description(self):
        self.assertHousingFormatStructure(
            {
                'establishment': {'code': 'HEI', 'desc': 'HMP HEWELL'},
                'housing_location': {
                    'levels': [
                        {'type': 'Block', 'value': '1'},
                        {'type': 'Tier', 'value': '1'},
                        {'type': 'Spur', 'value': 'A'},
                        {'type': 'Cell', 'value': '001'},
                    ]
                }
            },
            {
                'nomis_id': 'HEI', 'name': 'HMP HEWELL',
                'housing_location': {
                    'description': 'HEI-1-1-A-001',
                    'levels': [
                        {'type': 'Block', 'value': '1'},
                        {'type': 'Tier', 'value': '1'},
                        {'type': 'Spur', 'value': 'A'},
                        {'type': 'Cell', 'value': '001'},
                    ]
                }
            }
        )


@mock.patch.object(nomis, 'connector', nomis.EliteNomisConnector())
class EliteTestCaseMixin:
    """
    Mixin related to NOMIS logic using Elite2 Auth and API.
    """

    def _mock_successful_auth_request(self, rsps, token='my-token'):
        rsps.add(
            responses.POST,
            urljoin(
                settings.NOMIS_ELITE_BASE_URL,
                '/auth/oauth/token?grant_type=client_credentials',
                trailing_slash=False,
            ),
            json={
                'access_token': token,
                'expires_in': 3600,
            },
            status=200,
        )


class EliteNomisApiTestCase(EliteTestCaseMixin, SimpleTestCase):
    """
    Tests related to generic NOMIS Elite2 auth and API.
    """

    def test_cannot_access_nomis_if_key_not_set(self):
        """
        Test that can_access_nomis returns False if any of the required keys is not set.
        """
        required_keys = (
            'NOMIS_ELITE_CLIENT_ID',
            'NOMIS_ELITE_CLIENT_SECRET',
            'NOMIS_ELITE_BASE_URL',
        )
        for key in required_keys:
            with override_settings(**{key: ''}):
                self.assertFalse(nomis.can_access_nomis())

    @local_memory_cache()
    def test_token_cached(self):
        """
        Test that the token is cached when making a NOMIS call.
        """
        self.assertEqual(
            django_cache.cache.get(nomis.EliteNomisConnector.TOKEN_CACHE_KEY),
            None,
        )

        with responses.RequestsMock() as rsps:
            self._mock_successful_auth_request(rsps, token='my-token')
            rsps.add(
                responses.GET,
                _build_elite_nomis_api_url('/some/path'),
                json={},
                status=200,
            )

            nomis.connector.get('/some/path')

        self.assertEqual(
            django_cache.cache.get(nomis.EliteNomisConnector.TOKEN_CACHE_KEY),
            'my-token',
        )

    @local_memory_cache()
    def test_gets_token_from_cache(self):
        """
        Test that any cached token is used when making a NOMIS call.
        """
        django_cache.cache.set(
            nomis.EliteNomisConnector.TOKEN_CACHE_KEY,
            'some-token',
        )

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                _build_elite_nomis_api_url('/some/path'),
                json={},
                status=200,
            )

            nomis.connector.get('/some/path')

        self.assertEqual(
            django_cache.cache.get(nomis.EliteNomisConnector.TOKEN_CACHE_KEY),
            'some-token',
        )

    @local_memory_cache()
    def test_retries_after_401_response(self):
        """
        Test that if a request returns 401, the logic invalidates the cached token
        and retries again to make sure it wasn't because of the cached token.
        This happens even when the caller passes in retries == 0.
        """
        django_cache.cache.set(
            nomis.EliteNomisConnector.TOKEN_CACHE_KEY,
            'invalid-token',
        )

        path = '/some/path'
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                _build_elite_nomis_api_url(path),
                status=401,
            )
            self._mock_successful_auth_request(rsps, token='my-token')
            rsps.add(
                responses.GET,
                _build_elite_nomis_api_url(path),
                json={},
                status=200,
            )

            with silence_logger('mtp'):
                nomis.connector.get(path, retries=0)

        self.assertEqual(
            django_cache.cache.get(nomis.EliteNomisConnector.TOKEN_CACHE_KEY),
            'my-token',
        )

    @local_memory_cache()
    def test_doesnt_retry_more_than_once_after_401_response(self):
        """
        Test that if a request returns 401, the logic invalidates the cached token
        and retries again to make sure it wasn't because of the cached token
        but it doesn't invalidate the token again if the subsequent call is still in error.
        """
        django_cache.cache.set(
            nomis.EliteNomisConnector.TOKEN_CACHE_KEY,
            'invalid-token',
        )

        path = '/some/path'
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                _build_elite_nomis_api_url(path),
                status=401,
            )
            self._mock_successful_auth_request(rsps, token='my-token')
            rsps.add(
                responses.GET,
                _build_elite_nomis_api_url(path),
                status=401,
            )

            with silence_logger('mtp'):
                with self.assertRaises(HTTPError):
                    nomis.connector.get(path, retries=0)
            self.assertEqual(len(rsps.calls), 3)

        self.assertEqual(
            django_cache.cache.get(nomis.EliteNomisConnector.TOKEN_CACHE_KEY),
            'my-token',
        )


class GetAccountBalancesTestCase(EliteTestCaseMixin, SimpleTestCase):
    """
    Tests related to the get_account_balances function.
    """

    def test_call(self):
        """
        Test that the function connects to NOMIS and gets the expected data.
        """
        actual_balances = {
            'cash': 500,
            'savings': 0,
            'spends': 25,
        }
        with responses.RequestsMock() as rsps:
            self._mock_successful_auth_request(rsps)
            rsps.add(
                responses.GET,
                _build_elite_nomis_api_url('/prison/BMI/offenders/A1471AE/accounts'),
                json=actual_balances,
                status=200,
            )

            balances = nomis.get_account_balances('BMI', 'A1471AE')

        self.assertEqual(balances, actual_balances)


class GetTransactionHistoryTestCase(EliteTestCaseMixin, SimpleTestCase):
    """
    Tests related to the get_transaction_history function.
    """
    TRANSACTIONS_RESPONSE = {
        'transactions': [
            {
                'id': '204564839-3',
                'type': {
                    'code': 'c',
                    'desc': 'some description'
                },
                'description': 'Transfer In Regular from caseload PVR',
                'amount': 12345,
                'date': '2016-10-21',
            },
        ],
    }

    def test_date_converted_to_string(self):
        """
        Test that the the date param is converted to string and passed in as query param.
        """
        url = _build_elite_nomis_api_url('/prison/BMI/offenders/A1471AE/accounts/spends/transactions')
        from_date = datetime.date(year=2019, month=10, day=30)

        with responses.RequestsMock() as rsps:
            self._mock_successful_auth_request(rsps)
            rsps.add(
                responses.GET,
                f'{url}?from_date=2019-10-30',
                json=self.TRANSACTIONS_RESPONSE,
                status=200,
            )

            transactions = nomis.get_transaction_history('BMI', 'A1471AE', 'spends', from_date)

        self.assertEqual(transactions, self.TRANSACTIONS_RESPONSE)

    def test_string_date_passed_through(self):
        """
        Test that the string date param is kept untouched when passed in as query param.
        """
        url = _build_elite_nomis_api_url('/prison/BMI/offenders/A1471AE/accounts/spends/transactions')
        from_date = '2019-09-09'

        with responses.RequestsMock() as rsps:
            self._mock_successful_auth_request(rsps)
            rsps.add(
                responses.GET,
                f'{url}?from_date={from_date}',
                json=self.TRANSACTIONS_RESPONSE,
                status=200,
            )

            transactions = nomis.get_transaction_history('BMI', 'A1471AE', 'spends', from_date)

        self.assertEqual(transactions, self.TRANSACTIONS_RESPONSE)

    def test_date_of_invalid_type_ignored(self):
        """
        Test that if the date param of the function is not of type string or date,
        its value is ignored and not passed in as query param.
        """
        url = _build_elite_nomis_api_url('/prison/BMI/offenders/A1471AE/accounts/spends/transactions')
        from_date = 1

        with responses.RequestsMock() as rsps:
            self._mock_successful_auth_request(rsps)
            rsps.add(
                responses.GET,
                url,
                match_querystring=True,
                json=self.TRANSACTIONS_RESPONSE,
                status=200,
            )

            transactions = nomis.get_transaction_history('BMI', 'A1471AE', 'spends', from_date)

        self.assertEqual(transactions, self.TRANSACTIONS_RESPONSE)


class CreateTransactionTestCase(EliteTestCaseMixin, SimpleTestCase):
    """
    Tests related to the create_transaction function.
    """

    def test_call(self):
        """
        Test that the function connects to NOMIS and gets the expected data.
        """
        actual_transaction_data = {
            'id': '6179604-1',
        }
        with responses.RequestsMock() as rsps:
            self._mock_successful_auth_request(rsps)
            rsps.add(
                responses.POST,
                _build_elite_nomis_api_url('/prison/BWI/offenders/A1471AE/transactions'),
                json=actual_transaction_data,
                status=200,
            )

            transactions = nomis.create_transaction('BWI', 'A1471AE', 1634, 'CL123212', 'Canteen Purchase', 'CANT')

            self.assertEqual(transactions, actual_transaction_data)

            self.assertEqual(
                json.loads(rsps.calls[-1].request.body.decode()),
                {
                    'type': 'CANT',
                    'description': 'Canteen Purchase',
                    'amount': 1634,
                    'client_transaction_id': 'CL123212',
                    'client_unique_ref': 'CL123212',
                },
            )


class GetPhotographDataTestCase(EliteTestCaseMixin, SimpleTestCase):
    """
    Tests related to the get_photograph_data function.
    """

    def test_call(self):
        """
        Test that the function connects to NOMIS and gets the expected data.
        """
        with responses.RequestsMock() as rsps:
            self._mock_successful_auth_request(rsps)
            rsps.add(
                responses.GET,
                _build_elite_nomis_api_url('/offenders/A1471AE/image'),
                json={
                    'image': 'some-image',
                },
                status=200,
            )

            photo_data = nomis.get_photograph_data('A1471AE')

        self.assertEqual(photo_data, 'some-image')

    def test_returns_none_if_nomis_does_not_include_image(self):
        """
        Test that the function returns None if the NOMIS response doesn't include any image.
        """
        with responses.RequestsMock() as rsps:
            self._mock_successful_auth_request(rsps)
            rsps.add(
                responses.GET,
                _build_elite_nomis_api_url('/offenders/A1471AE/image'),
                json={},
                status=200,
            )

            photo_data = nomis.get_photograph_data('A1471AE')

        self.assertEqual(photo_data, None)


class GetLocationTestCase(EliteTestCaseMixin, SimpleTestCase):
    """
    Tests related to the get_location function.
    """

    def _test_get_location_scenario(self, nomis_mocked_response, expected_location_dict):
        with responses.RequestsMock() as rsps:
            self._mock_successful_auth_request(rsps)
            rsps.add(
                responses.GET,
                _build_elite_nomis_api_url('/offenders/A1401AE/location'),
                json=nomis_mocked_response,
            )
            actual_location_dict = nomis.get_location('A1401AE')
        self.assertEqual(actual_location_dict, expected_location_dict)

    def test_housing_location_no_housing(self):
        """
        Test that if the NOMIS response doesn't include 'housing_location', the returned
        value doesn't include that either.
        """
        self._test_get_location_scenario(
            {
                'establishment': {
                    'code': 'BXI',
                    'desc': 'BRIXTON (HMP)'
                }
            },
            {'nomis_id': 'BXI', 'name': 'BRIXTON (HMP)'}
        )

    def test_housing_location_dict_housing(self):
        """
        Test that if the NOMIS response includes 'housing_location', the returned value includes that as well.
        """
        self._test_get_location_scenario(
            {
                'establishment': {
                    'code': 'BXI',
                    'desc': 'BRIXTON (HMP)'
                },
                'housing_location': {
                    'description': 'BXI-H-2-001',
                    'levels': [
                        {'type': 'Wing', 'value': 'H'},
                        {'type': 'Landing', 'value': '2'},
                        {'type': 'Cell', 'value': '001'},
                    ]
                }
            },
            {
                'nomis_id': 'BXI',
                'name': 'BRIXTON (HMP)',
                'housing_location': {
                    'description': 'BXI-H-2-001',
                    'levels': [
                        {'type': 'Wing', 'value': 'H'},
                        {'type': 'Landing', 'value': '2'},
                        {'type': 'Cell', 'value': '001'},
                    ]
                }
            }
        )

    def test_housing_location_absent_levels(self):
        """
        Test that if the NOMIS response doesn't include housing_location.levels,
        the returned value uses [] instead.
        """
        self._test_get_location_scenario(
            {
                'establishment': {'code': 'WWI', 'desc': 'WANDSWORTH (HMP)'},
                'housing_location': {
                    'description': 'WWI-COURT',
                }
            },
            {
                'nomis_id': 'WWI', 'name': 'WANDSWORTH (HMP)',
                'housing_location': {
                    'description': 'WWI-COURT',
                    'levels': [],
                }
            }
        )

    def test_housing_location_absent_description(self):
        """
        Test that if the NOMIS response doesn't include housing_location.description,
        the returned value uses the value from establishment.desc instead.
        """
        self._test_get_location_scenario(
            {
                'establishment': {'code': 'HEI', 'desc': 'HMP HEWELL'},
                'housing_location': {
                    'levels': [
                        {'type': 'Block', 'value': '1'},
                        {'type': 'Tier', 'value': '1'},
                        {'type': 'Spur', 'value': 'A'},
                        {'type': 'Cell', 'value': '001'},
                    ]
                }
            },
            {
                'nomis_id': 'HEI', 'name': 'HMP HEWELL',
                'housing_location': {
                    'description': 'HEI-1-1-A-001',
                    'levels': [
                        {'type': 'Block', 'value': '1'},
                        {'type': 'Tier', 'value': '1'},
                        {'type': 'Spur', 'value': 'A'},
                        {'type': 'Cell', 'value': '001'},
                    ]
                }
            }
        )

    def test_returns_none_if_nomis_does_not_include_establishment(self):
        """
        Test that the function returns None if the NOMIS response doesn't include any establishment.
        """
        with responses.RequestsMock() as rsps:
            self._mock_successful_auth_request(rsps)
            rsps.add(
                responses.GET,
                _build_elite_nomis_api_url('/offenders/A1401AE/location'),
                json={},
                status=200,
            )

            actual_location_dict = nomis.get_location('A1401AE')

        self.assertEqual(actual_location_dict, None)
