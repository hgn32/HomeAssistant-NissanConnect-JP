# Based on work by @mitchellrj and @Tobiaswk
# Portions re-licensed from Apache License, Version 2.0 with permission

import collections
import datetime
import json
import os
import logging
from typing import List
import requests
import time
from oauthlib.common import generate_nonce
from oauthlib.oauth2 import TokenExpiredError
from requests_oauthlib import OAuth2Session
from urllib.parse import urlparse, parse_qs
from .kamereon_const import *

_LOGGER = logging.getLogger(__name__)

_registry = {
    USERS: {},
    VEHICLES: {},
    CATEGORIES: {},
    NOTIFICATION_RULES: {},
    NOTIFICATION_TYPES: {},
    NOTIFICATION_CATEGORIES: {},
}

NotificationType = collections.namedtuple('NotificationType', ['key', 'title', 'message', 'category'])
NotificationCategory = collections.namedtuple('Category', ['key', 'title'])

class Notification:

    @property
    def vehicle(self):
        return _registry[VEHICLES][self.vin]

    @property
    def user_id(self):
        return self.vehicle.user_id

    @property
    def session(self):
        return self.vehicle.session

    def __init__(self, data, language, vin):
        self.language = language
        self.vin = vin
        self.id = data['notificationId']
        self.title = data['messageTitle']
        self.subtitle = data['messageSubtitle']
        self.description = data['messageDescription']
        self.category = NotificationCategoryKey(data['categoryKey'])
        self.rule_key = NotificationRuleKey(data['ruleKey'])
        self.notification_key = NotificationTypeKey(data['notificationKey'])
        self.priority = NotificationPriority(data['priority'])
        self.state = NotificationStatus(data['status'])
        t = datetime.datetime.strptime(data['timestamp'].split('.')[0], '%Y-%m-%dT%H:%M:%S')
        if '.' in data['timestamp']:
            fraction = data['timestamp'][20:-1]
            t = t.replace(microsecond=int(fraction) * 10**(6-len(fraction)))
        self.time = t
        # List of {'name': 'N', 'type': 'T', 'value': 'V'}
        self.data = data['data']
        # future use maybe? empty dict
        self.metadata = data['metadata']

    def __str__(self):
        # title is kinda useless, subtitle has better content
        return '{}: {}'.format(self.time, self.subtitle)

    def fetch_details(self, language: Language=None):
        if language is None:
            language = self.language
        resp = self._get(
            '{}v2/notifications/users/{}/vehicles/{}/notifications/{}'.format(
                self.session.settings['notifications_base_url'],
                self.user_id, self.vin, self.id
            ),
            params={'langCode': language.value}
        )
        return resp


class KamereonSession:

    tenant = None
    copy_realm = None
    unique_id = None

    def __init__(self, region, unique_id=None):
        self.region = region
        self.settings = SETTINGS_MAP[self.tenant][region]
        session = requests.session()
        self.session = session
        self._oauth = None
        self._user_id = None
        self.unique_id = unique_id
        # ugly hack
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

    def login(self, username=None, password=None):
        if self.region == 'JP':
            return self._login_jp(username, password)
        return self._login_oauth(username, password)

    def _login_jp(self, username=None, password=None):
        """JP: BFF のログインAPIを試し、駄目なら KAuth に直接ログインする。"""
        if username is not None and password is not None:
            self._username = username
            self._password = password
        else:
            username = self._username
            password = self._password

        self.session = requests.session()

        problems = []
        try:
            token = self._login_jp_bff(username, password)
            _LOGGER.debug("Logged in via BFF login endpoint")
        except Exception as bff_error:  # noqa: BLE001
            problems.append('BFF: {}'.format(bff_error))
            _LOGGER.warning(
                "BFF login failed (%s) - falling back to direct KAuth login", bff_error)
            try:
                token = self._login_jp_kauth(username, password)
                _LOGGER.info("Logged in via direct KAuth login")
            except Exception as kauth_error:  # noqa: BLE001
                problems.append('KAuth: {}'.format(kauth_error))
                raise RuntimeError(' | '.join(problems)) from kauth_error

        self._oauth = requests.session()
        self._oauth.headers.update({'Authorization': 'Bearer ' + token})

    def _login_jp_bff(self, username, password):
        """NissanConnect (NCX) アプリと同じ、BFF 経由のログイン。"""
        auth_url = '{}nissan/account/v1/login'.format(
            self.settings['user_base_url'])
        response = self.session.post(
            auth_url,
            json={
                'data': {
                    'type': 'token',
                    'attributes': {
                        'username': username,
                        'password': password,
                    }
                }
            },
            headers={'x-app-id': self.settings.get(
                'app_id', 'jp.co.nissan.nissanconnect.ncx')},
            timeout=45,
        )
        body = response.json()
        if 'errors' in body:
            error = body['errors'][0]
            raise RuntimeError(error.get('detail', str(error)))
        return body['data']['attributes']['access_token']

    def _login_jp_kauth(self, username, password):
        """KAuth (ForgeRock) に直接ログインしてアクセストークンを得る。"""
        base_url = self.settings['auth_base_url']
        realm = self.settings['realm']
        session = requests.session()

        auth_url = '{}json/realms/root/realms/{}/authenticate'.format(
            base_url, realm)
        headers = {
            'Accept-Api-Version': API_VERSION,
            'X-Username': 'anonymous',
            'X-Password': 'anonymous',
            'Accept': 'application/json',
        }
        response = session.post(auth_url, headers=headers, timeout=30)
        response.raise_for_status()
        challenge = response.json()

        for callback in challenge.get('callbacks', []):
            if callback['type'] == 'NameCallback':
                callback['input'][0]['value'] = username
            elif callback['type'] == 'PasswordCallback':
                callback['input'][0]['value'] = password

        post_headers = dict(headers)
        post_headers['Content-Type'] = 'application/json'
        response = session.post(
            auth_url, headers=post_headers, data=json.dumps(challenge), timeout=30)
        if response.status_code == 401:
            raise RuntimeError('Invalid credentials')
        response.raise_for_status()
        auth_data = response.json()
        if 'tokenId' not in auth_data:
            raise RuntimeError(
                'Unexpected KAuth response: {}'.format(response.text[:200]))

        oauth_realm = auth_data.get('realm') or '/{}'.format(realm)

        response = session.get(
            '{}oauth2{}/authorize'.format(base_url, oauth_realm),
            params={
                'client_id': self.settings['client_id'],
                'redirect_uri': self.settings['redirect_uri'],
                'response_type': 'code',
                'scope': self.settings['scope'],
                'nonce': generate_nonce(),
            },
            allow_redirects=False,
            timeout=30,
        )
        location = response.headers.get('location', '')
        code = self._extract_auth_code(location)
        if not code:
            raise RuntimeError('No authorization code returned (http {}, location {})'.format(
                response.status_code, location[:200]))

        payload = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': self.settings['redirect_uri'],
            'client_id': self.settings['client_id'],
        }
        client_secret = self.settings.get('client_secret')
        # クライアント認証方式が不明なため post -> basic -> なし の順に試す
        attempts = []
        if client_secret:
            attempts.append(('client_secret_post', dict(
                payload, client_secret=client_secret), None))
            attempts.append(('client_secret_basic', payload,
                             (self.settings['client_id'], client_secret)))
        attempts.append(('none', payload, None))

        token_url = '{}oauth2{}/access_token'.format(base_url, oauth_realm)
        last_error = None
        for method, data, auth in attempts:
            response = session.post(token_url, data=data, auth=auth, timeout=30)
            if response.status_code == 200:
                token = response.json().get('access_token')
                if token:
                    _LOGGER.debug(
                        "KAuth token obtained (client auth: %s)", method)
                    return token
            last_error = '{} -> {} {}'.format(
                method, response.status_code, response.text[:200])
            _LOGGER.debug("KAuth token request failed: %s", last_error)

        raise RuntimeError('Token request failed ({})'.format(last_error))

    @staticmethod
    def _extract_auth_code(location):
        if not location:
            return None
        parsed = urlparse(location)
        params = parse_qs(parsed.query)
        if not params.get('code'):
            params = parse_qs(parsed.fragment)
        codes = params.get('code')
        return codes[0] if codes else None

    def _login_oauth(self, username=None, password=None):
        if username is not None and password is not None:
            # Cache credentials
            self._username = username
            self._password = password
        else:
            # Use cached credentials
            username = self._username
            password = self._password
        
        # Reset session
        self.session = requests.session()

        # grab an auth ID to use as part of the username/password login request,
        # then move to the regular OAuth2 process
        auth_url = '{}json/realms/root/realms/{}/authenticate'.format(
            self.settings['auth_base_url'],
            self.settings['realm'],
        )
        resp = self.session.post(
            auth_url,
            headers={
                'Accept-Api-Version': API_VERSION,
                'X-Username': 'anonymous',
                'X-Password': 'anonymous',
                'Accept': 'application/json',
            })
        next_body = resp.json()

        # insert the username, and password
        for c in next_body['callbacks']:
            input_type = c['type']
            if input_type == 'NameCallback':
                c['input'][0]['value'] = username
            elif input_type == 'PasswordCallback':
                c['input'][0]['value'] = password

        resp = self.session.post(
            auth_url,
            headers={
                'Accept-Api-Version': API_VERSION,
                'X-Username': 'anonymous',
                'X-Password': 'anonymous',
                'Accept': 'application/json',
                'Content-Type': 'application/json',
            },
            data=json.dumps(next_body))

        oauth_data = resp.json()

        if 'realm' not in oauth_data:
            _LOGGER.error("Invalid credentials provided: %s", resp.text)
            raise RuntimeError("Invalid credentials")
        
        oauth_authorize_url = '{}oauth2{}/authorize'.format(
            self.settings['auth_base_url'],
            oauth_data['realm']
            )
        nonce = generate_nonce()
        resp = self.session.get(
            oauth_authorize_url,
            params={
                'client_id': self.settings['client_id'],
                'redirect_uri': self.settings['redirect_uri'],
                'response_type': 'code',
                'scope': self.settings['scope'],
                'nonce': nonce,
            },
            allow_redirects=False)
        oauth_authorize_url = resp.headers['location']

        oauth_token_url = '{}oauth2{}/access_token'.format(
            self.settings['auth_base_url'],
            oauth_data['realm']
            )
        self._oauth = OAuth2Session(
            client_id=self.settings['client_id'],
            redirect_uri=self.settings['redirect_uri'],
            scope=self.settings['scope'])
        self._oauth._client.nonce = nonce
        self._oauth.fetch_token(
            oauth_token_url,
            authorization_response=oauth_authorize_url,
            client_secret=self.settings['client_secret'],
            include_client_id=True)

    @property
    def oauth(self):
        if self._oauth is None:
            raise RuntimeError('No access token set, you need to log in first.')
        return self._oauth

    @property
    def user_id(self):
        if not self._user_id:
            resp = self.oauth.get(
                '{}v1/users/current'.format(self.settings['user_adapter_base_url'])
            )
            self._user_id = resp.json()['userId']
            _registry[USERS][self._user_id] = self
        return self._user_id

    def fetch_vehicles(self):
        if self.region == 'JP':
            return self._fetch_vehicles_jp()
        resp = self.oauth.get(
            '{}v5/users/{}/cars'.format(self.settings['user_base_url'], self.user_id)
        )
        vehicles = []
        for vehicle_data in resp.json()['data']:
            vehicle = Vehicle(vehicle_data, self.user_id)
            vehicles.append(vehicle)
            _registry[VEHICLES][vehicle.vin] = vehicle
        return vehicles


    def _fetch_vehicles_jp(self):
        """JP の BFF は EU と別系統 (token-info + car details) を使う。"""
        resp = self.oauth.get(
            '{}nissan/account/v1/token-info'.format(self.settings['user_base_url'])
        )
        vehicles = []
        for vehicle_baseinfo in resp.json()['data']['attributes']['vehicles']:
            vehicle_data = self.oauth.get(
                '{}nissan/config/v1/cars/{}/details'.format(
                    self.settings['user_base_url'], vehicle_baseinfo['vin'])
            ).json()
            vehicle_data['services'] = vehicle_baseinfo.get('services', [])
            vehicle_data['appConfig'] = self._fetch_app_config(vehicle_baseinfo['vin'])
            vehicle = Vehicle(vehicle_data, self.user_id)
            vehicles.append(vehicle)
            _registry[VEHICLES][vehicle.vin] = vehicle
        return vehicles

    def _fetch_app_config(self, vin):
        """アプリ自身の機能可用性マップ。取れなければ空を返す。"""
        try:
            body = self.oauth.get(
                '{}nissan/config/v1/cars/{}/features'.format(
                    self.settings['user_base_url'], vin)
            ).json()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not fetch the app feature map: %s", err)
            return {}
        if 'errors' in body:
            _LOGGER.warning("Could not fetch the app feature map: %s", body['errors'])
            return {}
        return body.get('data', {}).get('attributes', {}) or {}


class NCISession(KamereonSession):

    tenant = 'nissan'
    copy_realm = 'P_NCB'


class Vehicle:

    def __repr__(self):
        return '<{} {}>'.format(self.__class__.__name__, self.vin)

    def __str__(self):
        return self.vin

    @property
    def session(self):
        return _registry[USERS][self.user_id]

    def __init__(self, data, user_id):
        self.user_id = user_id
        self.vin = data['vin'].upper()
        # JP (nissan/config/v1/cars/{vin}/details) は model が dict で返る。
        # EU (v5/users/{id}/cars) は modelName などがトップレベルにある。
        model = data.get('model')
        jp_payload = isinstance(model, dict)
        # アプリの機能可用性マップ (JP のみ)。これがあるなら
        # services の推定よりこちらを優先する。
        self.app_config = data.get('appConfig') or {}
        self.features = []

        # Try to parse every feature, but dont fail if we dont recognise one
        for u in data.get('services', []):
            if isinstance(u, dict):
                if u.get('activationState') != "ACTIVATED":
                    continue
                feature_id = str(u['id'])
            else:
                # JP の token-info は id の配列で返る
                feature_id = str(u)
            try:
                feature = Feature(feature_id)
                if feature not in self.features:
                    self.features.append(feature)
            except ValueError:
                _LOGGER.debug(f"Unknown feature {feature_id}")
                pass

        _LOGGER.debug("Active features: %s", self.features)

        color = data.get('color')
        self.can_generation = data.get('canGeneration')
        self.color = color.get('nameG2B') if isinstance(color, dict) else color
        self.energy = data.get('energy')
        self.vehicle_gateway = data.get('carGateway')
        self.battery_code = data.get('batteryCode')
        self.engine_type = data.get('engineType')
        self.first_registration_date = data.get('firstRegistrationDate')
        self.ice_or_ev = data.get('iceEvFlag')
        if jp_payload:
            self.model_name = model.get('displayName')
            self.model_code = model.get('code')
            self.model_year = model.get('year')
            self.nickname = model.get('name')
            self.phase = None
            self.picture_url = data.get('bfpImageUrl')
            self.privacy_mode = None
        else:
            self.model_name = data.get('modelName')
            self.model_code = data.get('modelCode')
            self.model_year = data.get('modelYear')
            self.nickname = data.get('nickname')
            self.phase = data.get('phase')
            self.picture_url = data.get('pictureURL')
            self.privacy_mode = data.get('privacyMode')
        self.registration_number = data.get('registrationNumber')
        self.battery_supported = True
        self.battery_capacity = None
        self.battery_level = None
        self.battery_temperature = None
        self.battery_bar_level = None
        self.instantaneous_power = None
        self.charging_speed = None
        self.charge_time_required_to_full = {
            ChargingSpeed.FAST: None,
            ChargingSpeed.NORMAL: None,
            ChargingSpeed.SLOW: None,
            ChargingSpeed.ADAPTIVE: None
        }
        self.range_hvac_off = None
        self.range_hvac_on = None
        self.charging = ChargingStatus.NOT_CHARGING
        self.plugged_in = PluggedStatus.NOT_PLUGGED
        self.plugged_in_time = None
        self.unplugged_time = None
        self.battery_status_last_updated = None
        self.location = None
        self.location_last_updated = None
        self.combustion_fuel_unit_cost = None
        self.electricity_unit_cost = None
        self.external_temperature = None
        self.internal_temperature = None
        self.hvac_status = None
        # JP: hvac-status が返す遠隔エンジン始動の状態 (意味は未解明)
        self.remote_engine_status = None
        self.next_hvac_start_date = None
        self.next_target_temperature = None
        self.hvac_status_last_updated = None
        self.door_status = {
            Door.FRONT_LEFT: None,
            Door.FRONT_RIGHT: None,
            Door.REAR_LEFT: None,
            Door.REAR_RIGHT: None,
            Door.HATCH: None,
            Door.HOOD: None
        }
        self.lock_status = None
        self.lock_status_last_updated = None
        self.malfunction_lamps = {}
        self.maintenance = {}
        self.health_status_last_updated = None
        self.eco_score = None
        self.fuel_autonomy = None
        self.fuel_consumption = None
        self.fuel_economy = None
        self.fuel_level = None
        self.fuel_low_warning = None
        self.fuel_quantity = None
        self.mileage = None
        self.total_mileage = None

    def app_available(self, *path):
        """機能可用性マップの available を返す。マップが無ければ None。"""
        if not self.app_config:
            return None
        node = self.app_config
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return None
            node = node[key]
        if isinstance(node, dict):
            return node.get('available')
        return None

    def available_health_lamps(self):
        """この車で対応している警告灯のキーを返す。"""
        if self.app_config:
            return [key for key in HEALTH_LAMPS
                    if self.app_available('healthStatus', key)]
        # マップが無い場合は実際に返ってきたものを使う
        return [key for key, payload_key in HEALTH_LAMPS.items()
                if payload_key in self.malfunction_lamps]

    def _request(self, method, url, headers=None, params=None, data=None, max_retries=3):
        for attempt in range(max_retries):
            try:
                if method == 'GET':
                    resp = self.session.oauth.get(url, headers=headers, params=params)
                elif method == 'POST':
                    resp = self.session.oauth.post(url, data=data, headers=headers)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

                # Check for token expiration
                if resp.status_code == 401:
                    raise TokenExpiredError()
                
                # Successful request
                return resp

            except TokenExpiredError:
                _LOGGER.debug("Token expired. Refreshing session and retrying.")
                self.session.login()
            except Exception as e:
                _LOGGER.debug(f"Request failed on attempt {attempt + 1} of {max_retries}: {e}")
                if attempt == max_retries - 1:  # Exhausted retries
                    raise
                time.sleep(2 ** attempt)  # Exponential backoff on retry

        raise RuntimeError("Max retries reached, but the request could not be completed.")

    def _get(self, url, headers=None, params=None):
        return self._request('GET', url, headers=headers, params=params)

    def _post(self, url, data=None, headers=None):
        return self._request('POST', url, headers=headers, data=data)

    def refresh(self):
        self.refresh_location()
        self.refresh_battery_status()

    def fetch_all(self):
        self.fetch_cockpit()
        self.fetch_location()
        self.fetch_battery_status()
        self.fetch_hvac_status()
        self.fetch_lock_status()
        self.fetch_health_status()

    def refresh_location(self):
        if Feature.MY_CAR_FINDER not in self.features:
            return
        
        resp = self._post(
            '{}v1/cars/{}/actions/refresh-location'.format(self.session.settings['car_adapter_base_url'], self.vin),
            data=json.dumps({
                'data': {'type': 'RefreshLocation'}
            }),
            headers={'Content-Type': 'application/vnd.api+json'}
        )
        body = resp.json()
        if 'errors' in body:
            raise ValueError(body['errors'])
        return body

    def fetch_location(self):
        if Feature.MY_CAR_FINDER not in self.features:
            return
        
        resp = self._get(
            '{}v1/cars/{}/location'.format(self.session.settings['car_adapter_base_url'], self.vin),
            headers={'Content-Type': 'application/vnd.api+json'}
        )
        body = resp.json()
        if 'errors' in body:
            raise ValueError(body['errors'])
        location_data = body['data']['attributes']
        self.location = (location_data['gpsLatitude'], location_data['gpsLongitude'])
        self.location_last_updated = datetime.datetime.fromisoformat(location_data['lastUpdateTime'].replace('Z','+00:00'))

    def refresh_lock_status(self):
        resp = self._post(
            '{}v1/cars/{}/actions/refresh-lock-status'.format(self.session.settings['car_adapter_base_url'], self.vin),
            data=json.dumps({
                'data': {'type': 'RefreshLockStatus'}
            }),
            headers={'Content-Type': 'application/vnd.api+json'}
        )
        body = resp.json()
        if 'errors' in body:
            raise ValueError(body['errors'])
        return body

    def fetch_lock_status(self):
        if Feature.LOCK_STATUS_CHECK not in self.features:
            return
        resp = self._get(
            '{}v1/cars/{}/lock-status'.format(self.session.settings['car_adapter_base_url'], self.vin),
            headers={'Content-Type': 'application/vnd.api+json'}
        )
        body = resp.json()
        if 'errors' in body:
            raise ValueError(body['errors'])
        lock_data = body['data']['attributes']
        self.door_status[Door.FRONT_LEFT] = LockStatus(lock_data.get('doorStatusFrontLeft', LockStatus.CLOSED))
        self.door_status[Door.FRONT_RIGHT] = LockStatus(lock_data.get('doorStatusFrontRight', LockStatus.CLOSED))
        self.door_status[Door.REAR_LEFT] = LockStatus(lock_data.get('doorStatusRearLeft', LockStatus.CLOSED))
        self.door_status[Door.REAR_RIGHT] = LockStatus(lock_data.get('doorStatusRearRight', LockStatus.CLOSED))
        self.door_status[Door.HATCH] = LockStatus(lock_data.get('hatchStatus', LockStatus.CLOSED))
        if 'engineHoodStatus' in lock_data:
            self.door_status[Door.HOOD] = LockStatus(lock_data['engineHoodStatus'])
        self.lock_status = LockStatus(lock_data.get('lockStatus', LockStatus.LOCKED))
        self.lock_status_last_updated = datetime.datetime.fromisoformat(lock_data['lastUpdateTime'].replace('Z','+00:00'))

    def refresh_hvac_status(self):
        resp = self._post(
            '{}v1/cars/{}/actions/refresh-hvac-status'.format(self.session.settings['car_adapter_base_url'], self.vin),
            data=json.dumps({
                'data': {'type': 'RefreshHvacStatus'}
            }),
            headers={'Content-Type': 'application/vnd.api+json'}
        )
        body = resp.json()
        if 'errors' in body:
            raise ValueError(body['errors'])
        return body

    def initiate_srp(self):
        (salt, verifier) = SRP.enroll(self.user_id, self.vin)
        resp = self._post(
            '{}v1/cars/{}/actions/srp-initiates'.format(self.session.settings['car_adapter_base_url'], self.vin),
            data=json.dumps({
                "data": {
                    "type": "SrpInitiates",
                    "attributes": {
                        "s": salt,
                        "i": self.user_id,
                        "v": verifier
                    }
                }
            }),
            headers={'Content-Type': 'application/vnd.api+json'}
        )
        body = resp.json()
        if 'errors' in body:
            raise ValueError(body['errors'])
        return body

    def validate_srp(self):
        a = SRP.generate_a()
        resp = self._post(
            '{}v1/cars/{}/actions/srp-sets'.format(self.session.settings['car_adapter_base_url'], self.vin),
            data=json.dumps({
                "data": {
                    "type": "SrpSets",
                    "attributes": {
                        "i": self.user_id,
                        "a": a
                    }
                }
            }),
            headers={'Content-Type': 'application/vnd.api+json'}
        )
        body = resp.json()
        if 'errors' in body:
            raise ValueError(body['errors'])
        return body

    """
    Other vehicle controls to implement / investigate:
        DataReset
        DeleteCurfewRestrictions
        CreateCurfewRestrictions
        CreateSpeedRestrictions
        SrpInitiates
        DeleteAreaRestrictions
        SrpDelete
        SrpSets
        OpenClose
        EngineStart
        LockUnlock
        CreateAreaRestrictions
        DeleteSpeedRestrictions
    """

    def control_charging(self, action: str, srp: str=None):
        assert action in ('stop', 'start')
        if action == 'start' and Feature.CHARGING_START not in self.features:
            return
        if action == 'stop' and Feature.CHARGING_STOP not in self.features:
            return
        attributes = {
            'action': action,
        }
        if srp is not None:
            attributes['srp'] = srp
        resp = self._post(
            '{}v1/cars/{}/actions/charging-start'.format(self.session.settings['car_adapter_base_url'], self.vin),
            data=json.dumps({
                'data': {
                    'type': 'ChargingStart',
                    'attributes': attributes
                }
            }),
            headers={'Content-Type': 'application/vnd.api+json'}
        )
        body = resp.json()
        if 'errors' in body:
            raise ValueError(body['errors'])
        return body

    def control_horn_lights(self, action: str, target: str, duration: int=5, srp: str=None):
        if Feature.HORN_AND_LIGHTS not in self.features:
            return
        assert target in ('horn_lights', 'lights', 'horn')
        assert action in ('stop', 'start', 'double_start')
        attributes = {
            'action': action,
            'duration': duration,
            'target': target,
        }
        if srp is not None:
            attributes['srp'] = srp
        resp = self._post(
            '{}v1/cars/{}/actions/horn-lights'.format(self.session.settings['car_adapter_base_url'], self.vin),
            data=json.dumps({
                'data': {
                    'type': 'HornLights',
                    'attributes': attributes
                }
            }),
            headers={'Content-Type': 'application/vnd.api+json'}
        )
        body = resp.json()
        if 'errors' in body:
            raise ValueError(body['errors'])
        return body

    def set_hvac_status(self, action: HVACAction, target_temperature: int=21, start: datetime.datetime=None, srp: str=None):
        if Feature.CLIMATE_ON_OFF not in self.features:
            return

        if target_temperature < 16 or target_temperature > 26:
            raise ValueError('Temperature must be between 16 & 26 degrees')

        attributes = {
            'action': action.value
        }
        if action == HVACAction.START:
            attributes['targetTemperature'] = target_temperature
        if start is not None:
            attributes['startDateTime'] = start.isoformat(timespec='seconds')
        if srp is not None:
            attributes['srp'] = srp

        resp = self._post(
            '{}v1/cars/{}/actions/hvac-start'.format(self.session.settings['car_adapter_base_url'], self.vin),
            data=json.dumps({
                'data': {
                    'type': 'HvacStart',
                    'attributes': attributes
                }
            }),
            headers={'Content-Type': 'application/vnd.api+json'}
        )
        body = resp.json()
        if 'errors' in body:
            raise ValueError(body['errors'])
        return body

    def lock_unlock(self, srp: str, action: str, group: LockableDoorGroup=None):
        if Feature.APP_DOOR_LOCKING not in self.features:
            return
        assert action in ('lock', 'unlock')
        if group is None:
            group = LockableDoorGroup.DOORS_AND_HATCH
        resp = self._post(
            '{}v1/cars/{}/actions/lock-unlock"'.format(self.session.settings['car_adapter_base_url'], self.vin),
            data=json.dumps({
                'data': {
                    'type': 'LockUnlock',
                    'attributes': {
                        'lock': action,
                        'doorType': group.value,
                        'srp': srp
                    }
                }
            }),
            headers={'Content-Type': 'application/vnd.api+json'}
        )
        body = resp.json()
        if 'errors' in body:
            raise ValueError(body['errors'])
        return body

    def lock(self, srp: str, group: LockableDoorGroup=None):
        return self.lock_unlock(srp, 'lock', group)

    def unlock(self, srp: str, group: LockableDoorGroup=None):
        return self.lock_unlock(srp, 'unlock', group)

    def fetch_hvac_status(self):
        # JP の ICE 車はエアコン系の feature を持たないが、遠隔エンジン始動が
        # あれば hvac-status は remoteEngineStatus を返す
        if (Feature.INTERIOR_TEMP_SETTINGS not in self.features
                and Feature.TEMPERATURE not in self.features
                and Feature.REMOTE_ENGINE_START not in self.features):
            return
        
        resp = self._get(
            '{}v1/cars/{}/hvac-status'.format(self.session.settings['car_adapter_base_url'], self.vin),
            headers={'Content-Type': 'application/vnd.api+json'}
        )
        body = resp.json()
        if 'errors' in body:
            raise ValueError(body['errors'])
        hvac_data = body['data']['attributes']
        self.external_temperature = hvac_data.get('externalTemperature')
        self.internal_temperature = hvac_data.get('internalTemperature')
        self.next_target_temperature = hvac_data.get('nextTargetTemperature')
        if 'hvacStatus' in hvac_data:
            self.hvac_status = hvac_data['hvacStatus'] == "on"
        if 'remoteEngineStatus' in hvac_data:
            self.remote_engine_status = hvac_data['remoteEngineStatus']
            _LOGGER.debug("Remote engine status: %s", self.remote_engine_status)
        if 'nextHvacStartDate' in hvac_data:
            self.next_hvac_start_date = datetime.datetime.fromisoformat(hvac_data['nextHvacStartDate'].replace('Z','+00:00'))
        if 'lastUpdateTime' in hvac_data:
            self.hvac_status_last_updated = datetime.datetime.fromisoformat(hvac_data['lastUpdateTime'].replace('Z','+00:00'))

    def refresh_battery_status(self):
        resp = self._post(
            '{}v1/cars/{}/actions/refresh-battery-status'.format(self.session.settings['car_adapter_base_url'], self.vin),
            data=json.dumps({
                'data': {'type': 'RefreshBatteryStatus'}
            }),
            headers={'Content-Type': 'application/vnd.api+json'}
        )
        body = resp.json()
        if 'errors' in body:
            raise ValueError(body['errors'])
        return body

    def fetch_battery_status(self):
        if not self.battery_supported:
            return
        self.fetch_battery_status_leaf()
        if self.model_name == "Ariya":
            self.fetch_battery_status_ariya()

    def fetch_battery_status_leaf(self):
        """The battery-status endpoint isn't just for EV's. ICE Nissans publish the range under this!
           There is no obvious feature to qualify this, so we just suck it and see."""
        resp = self._get(
            '{}v1/cars/{}/battery-status'.format(self.session.settings['car_adapter_base_url'], self.vin),
            headers={'Content-Type': 'application/vnd.api+json'}
        )
        body = resp.json()
        if 'errors' in body and Feature.BATTERY_STATUS in self.features:
            raise ValueError(body['errors'])

        if not 'data' in body or not 'attributes' in body['data']:
            # この車はバッテリ情報を返さないので以後問い合わせない
            self.battery_supported = False
            return

        battery_data = body['data']['attributes']
        self.battery_capacity = battery_data.get('batteryCapacity')  # kWh
        self.battery_level = battery_data.get('batteryLevel')  # %
        self.battery_temperature = battery_data.get('batteryTemperature')  # Fahrenheit?
        # same meaning as battery level, different scale. 240 = 100%
        self.battery_bar_level = battery_data.get('batteryBarLevel')
        self.instantaneous_power = battery_data.get('instantaneousPower')  # kW
        self.charging_speed = ChargingSpeed(battery_data.get('chargePower'))
        self.charge_time_required_to_full = {
            ChargingSpeed.FAST: battery_data.get('timeRequiredToFullFast'),
            ChargingSpeed.NORMAL: battery_data.get('timeRequiredToFullNormal'),
            ChargingSpeed.SLOW: battery_data.get('timeRequiredToFullSlow'),
            ChargingSpeed.ADAPTIVE: None
        }
        self.range_hvac_off = battery_data.get('rangeHvacOff')
        self.range_hvac_on = battery_data.get('rangeHvacOn')
        
        # For ICE vehicles, we should get the range at least. If not, dont bother again
        if self.range_hvac_on is None and Feature.BATTERY_STATUS not in self.features:
            self.battery_supported = False
            return

        self.charging = ChargingStatus(battery_data.get('chargeStatus', 0))
        self.plugged_in = PluggedStatus(battery_data.get('plugStatus', 0))
        if 'vehiclePlugTimestamp' in battery_data:
            self.plugged_in_time = datetime.datetime.fromisoformat(battery_data['vehiclePlugTimestamp'].replace('Z','+00:00'))
        if 'vehicleUnplugTimestamp' in battery_data:
            self.unplugged_time = datetime.datetime.fromisoformat(battery_data['vehicleUnplugTimestamp'].replace('Z','+00:00'))
        if 'lastUpdateTime' in battery_data:
            self.battery_status_last_updated = datetime.datetime.fromisoformat(battery_data['lastUpdateTime'].replace('Z','+00:00'))

    def fetch_battery_status_ariya(self):
        resp = self._get(
            '{}v3/cars/{}/battery-status?canGen={}'.format(self.session.settings['user_base_url'], self.vin, self.can_generation),
            headers={'Content-Type': 'application/vnd.api+json'}
        )
        body = resp.json()
        if 'errors' in body and Feature.BATTERY_STATUS in self.features:
            raise ValueError(body['errors'])

        if not 'data' in body or not 'attributes' in body['data']:
            self.battery_supported = False

        battery_data = body['data']['attributes']
        
        self.range_hvac_off = None
        self.range_hvac_on = battery_data.get('batteryAutonomy') or self.range_hvac_on

        self.charging_speed = ChargingSpeed(None)
        self.charge_time_required_to_full = {
            ChargingSpeed.FAST: None,
            ChargingSpeed.NORMAL: None,
            ChargingSpeed.SLOW: None,
            ChargingSpeed.ADAPTIVE: battery_data.get('chargingRemainingTime') or self.charge_time_required_to_full[ChargingSpeed.NORMAL]
        }

        self.plugged_in = PluggedStatus(battery_data.get('plugStatus', 0))
                
        if 'vehiclePlugTimestamp' in battery_data:
            self.plugged_in_time = datetime.datetime.fromisoformat(battery_data['vehiclePlugTimestamp'].replace('Z','+00:00'))
        if 'vehicleUnplugTimestamp' in battery_data:
            self.unplugged_time = datetime.datetime.fromisoformat(battery_data['vehicleUnplugTimestamp'].replace('Z','+00:00'))
        if 'lastUpdateTime' in battery_data:
            self.battery_status_last_updated = datetime.datetime.fromisoformat(battery_data['lastUpdateTime'].replace('Z','+00:00'))

    def set_energy_unit_cost(self, cost):
        resp = self._post(
            '{}v1/cars/{}/energy-unit-cost'.format(self.session.settings['car_adapter_base_url'], self.vin),
            data=json.dumps({
                'data': {
                    'type': {}
                }
            })
        )
        body = resp.json()
        if 'errors' in body:
            raise ValueError(body['errors'])

    def _format_trip_date(self, value: datetime.date):
        """JP の trip-history は YYYYMMDD、EU は YYYY-MM-DD を取る。"""
        if self.session.region == 'JP':
            return value.isoformat().replace('-', '')
        return value.isoformat()

    def fetch_trip_histories(self, period: Period=None, start: datetime.date=None, end: datetime.date=None):
        if period is None:
            period = Period.DAILY
        if start is None and end is None and period == Period.MONTHLY:
            end = datetime.datetime.utcnow().date()
            start = end.replace(day=1)
        elif start is None:
            start = datetime.datetime.utcnow().date()
        if end is None:
            end = start
        resp = self._get(
            '{}v1/cars/{}/trip-history'.format(self.session.settings['car_adapter_base_url'], self.vin),
            params={
                'type': period.value,
                'start': self._format_trip_date(start),
                'end': self._format_trip_date(end)
            }
        )
        body = resp.json()
        if 'errors' in body:
            raise ValueError(body['errors'])
        return [TripSummary(s, self.vin) for s in body['data']['attributes']['summaries']]

    def fetch_notifications(
            self,
            language: Language=None,
            category_key: NotificationCategoryKey=None,
            status: NotificationStatus=None,
            start: datetime.datetime=None,
            end: datetime.datetime=None,
            # offset
            from_: int=1,
            # limit
            to: int=20,
            order: Order=None
            ):

        if language is None:
            language = Language.EN
        params = {
            'realm': self.session.copy_realm,
            'langCode': language.value,
        }
        if category_key is not None:
            params['categoryKey'] = category_key.value
        if status is not None:
            params['status'] = status.value
        if start is not None:
            params['start'] = start.isoformat(timespec='seconds')
            if start.tzinfo is None:
                # Assume UTC
                params['start'] += 'Z'
        if end is not None:
            params['end'] = start.isoformat(timespec='seconds')
            if end.tzinfo is None:
                # Assume UTC
                params['end'] += 'Z'
        resp = self._get(
            '{}v2/notifications/users/{}/vehicles/{}'.format(self.session.settings['notifications_base_url'], self.user_id, self.vin),
            params=params
        )
        body = resp.json()
        if 'errors' in body:
            raise ValueError(body['errors'])
        return [Notification(m, language, self.vin) for m in body['data']['attributes']['messages']]

    def mark_notifications(self, messages: List[Notification]):
        """Take a list of notifications and set their status remotely
        to the one held locally (read / unread)."""

        resp = self._post(
            '{}v2/notifications/users/{}/vehicles/{}'.format(self.session.settings['notifications_base_url'], self.user_id, self.vin),
            data=json.dumps([
                {'notificationId': m.id, 'status': m.status.value}
                for m in messages
            ])
        )
        body = resp.json()
        if 'errors' in body:
            raise ValueError(body['errors'])
        return body

    def fetch_notification_settings(self, language: Language=None):
        if language is None:
            language = Language.EN
        params = {
            'langCode': language.value,
        }
        resp = self._get(
            '{}v1/rules/settings/users/{}/vehicles/{}'.format(self.session.settings['notifications_base_url'], self.user_id, self.vin),
            params=params
        )
        body = resp.json()
        if 'errors' in body:
            raise ValueError(body['errors'])
        return [
            NotificationRule(r, language, self.vin)
            for r in body['settings']
        ]

    def update_notification_settings(self):
        # TODO
        pass

    def fetch_health_status(self):
        """警告灯とメンテナンス情報。"""
        if self.app_config:
            if not any(self.app_available('healthStatus', key) for key in HEALTH_LAMPS):
                return
        elif Feature.VEHICLE_HEALTH_REPORT not in self.features:
            return

        resp = self._get(
            '{}v1/cars/{}/health-status'.format(self.session.settings['car_adapter_base_url'], self.vin),
            headers={'Content-Type': 'application/vnd.api+json'}
        )
        body = resp.json()
        if 'errors' in body:
            _LOGGER.warning(body['errors'])
            return
        health_data = body['data']['attributes']
        self.malfunction_lamps = health_data.get('malfunctionIndicatorLamps', {})
        self.maintenance = health_data.get('maintenance', {})
        if 'lastUpdateTime' in health_data:
            self.health_status_last_updated = datetime.datetime.fromisoformat(health_data['lastUpdateTime'].replace('Z','+00:00'))

    def fetch_cockpit(self):
        resp = self._get(
            "{}v1/cars/{}/cockpit".format(self.session.settings['car_adapter_base_url'], self.vin)
        )
        body = resp.json()
        # 全ての車種が cockpit に対応しているわけではないので、
        # ここで失敗してもセットアップ全体は落とさない
        if 'errors' in body:
            _LOGGER.warning(body['errors'])
            return

        cockpit_data = body['data']['attributes']
        self.eco_score = cockpit_data.get('ecoScore')
        self.fuel_autonomy = cockpit_data.get('fuelAutonomy')
        self.fuel_consumption = cockpit_data.get('fuelConsumption')
        self.fuel_economy = cockpit_data.get('fuelEconomy')
        self.fuel_level = cockpit_data.get('fuelLevel')
        if 'fuelLowWarning' in cockpit_data:
            self.fuel_low_warning = bool(cockpit_data.get('fuelLowWarning', False))
        self.fuel_quantity = cockpit_data.get('fuelQuantity')  # litres
        self.mileage = cockpit_data.get('mileage')
        self.total_mileage = cockpit_data.get('totalMileage')


class TripSummary:

    def __init__(self, data, vin):
        self.vin = vin
        self.trip_count = data['tripsNumber']
        self.total_distance = data['distance']  # km
        self.total_duration = data['duration']  # minutes
        self.first_trip_start = datetime.datetime.fromisoformat(data['firstTripStart'].replace('Z','+00:00'))
        self.last_trip_end = datetime.datetime.fromisoformat(data['lastTripEnd'].replace('Z','+00:00'))
        self.consumed_fuel = data['consumedFuel']  # litres
        self.consumed_electricity = data['consumedElectricity']  # W
        self.saved_electricity = data['savedElectricity']  # W
        if 'day' in data:
            self.start = self.end = datetime.date(int(data['day'][:4]), int(data['day'][4:6]), int(data['day'][6:]))
        elif 'month' in data:
            start_year = int(data['month'][:4])
            start_month = int(data['month'][4:])
            end_month = start_month + 1
            end_year = start_year
            if end_month > 12:
                end_month = 1
                end_year = end_year + 1
            self.start = datetime.date(start_year, start_month, 1)
            self.end = datetime.date(end_year, end_month, 1) - datetime.timedelta(days=1)
        elif 'year' in data:
            self.start = datetime.date(int(data['year']), 1, 1)
            self.end = datetime.date(int(data['year']) + 1, 1, 1) - datetime.timedelta(days=1)

    def __str__(self):
        return '{} trips covering {} kilometres over {} minutes using {} litres fuel and {} kilowatt-hours electricity'.format(
            self.trip_count, self.total_distance, self.total_duration, self.consumed_fuel, self.consumed_electricity
        )


class NotificationRule:

    def __init__(self, data, language, vin):
        self.vin = vin
        self.language = language
        self.key = NotificationRuleKey(data['ruleKey'])
        self.title = data['ruleTitle']
        self.description = data['ruleDescription']
        self.priority = NotificationPriority(data['priority'])
        self.status = NotificationRuleStatus(data['status'])
        self.channels = [
            NotificationChannelType(c['channelType'])
            for c in data['channels']
        ]
        self.category = NotificationCategory(NotificationCategoryKey(data['categoryKey']), data['categoryTitle'])
        self.notification_type = None
        if 'notificationKey' in data:
            self.notification_type = NotificationType(
                NotificationTypeKey(data['notificationKey']),
                data['notificationTitle'],
                data['notificationMessage'],
                self.category,
                )
    
    def __str__(self):
        return '{}: {} ({})'.format(
            self.title or self.key,
            self.status.value,
            ', '.join(c.value for c in self.channels)
        )


class SRP:

    @classmethod
    def enroll(cls, user_id, vin):
        salt, verifier = '0'*20, 'ABCDEFGH'*64
        # salt = 20 hex chars, verifier = 512 hex chars
        return (salt, verifier)

    @classmethod
    def generate_a(cls):
        # 512 hex chars
        return ''

    @classmethod
    def generate_proof(cls, salt, b, user_id, confirm_code, order):
        """Required for remote lock / unlock."""
        # order = '<VIN>/<PERMISSIONS>'
        # where PERMISSIONS is one of:
        # * "BCI/Block"
        # * "BCI/Unblock"
        # * "RC/Delayed"
        # * "RC/Start"
        # * "RC/Stop"
        # * "RES/DoubleStart"
        # * "RES/Start"
        # * "RES/Stop"
        # * "RHL/Start/HornOnly"
        # * "RHL/Start/HornLight"
        # * "RHL/Start/LightOnly"
        # * "RHL/Stop"
        # * "RLU/Lock"
        # * "RLU/Unlock"
        # * "RPC_ICE/Start"
        # * "RPC_ICE/Stop"
        # * "RPU_CCS/Disable"
        # * "RPU_CCS/Enable"
        # * "RPU_SVTB/Disable"
        # * "RPU_SVTB/Enable"
        pass
