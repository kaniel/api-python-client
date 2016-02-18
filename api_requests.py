# -*- coding: utf-8 -*-
# created by restran on 2016/02/18

from __future__ import unicode_literals

import traceback
import logging
import json
import sys
import types
import base64
import hashlib
import random
import time
import hmac
from hashlib import sha256
from urlparse import urlparse, urlunparse

import requests
from Crypto import Random
from Crypto.Cipher import AES

logger = logging.getLogger(__name__)

PY2 = sys.version_info[0] == 2
PY3 = sys.version_info[0] == 3

if PY3:
    string_types = str,
    integer_types = int,
    class_types = type,
    text_type = str
    binary_type = bytes

    MAXSIZE = sys.maxsize
else:
    string_types = basestring,
    integer_types = (int, long)
    class_types = (type, types.ClassType)
    text_type = unicode
    binary_type = str

# API 网关处理未通过时, 返回的状态码
GATEWAY_ERROR_STATUS_CODE = 600
# 签名的过期时间
SIGNATURE_EXPIRE_SECONDS = 3600


class AESCipher(object):
    """
    http://stackoverflow.com/questions/12524994/encrypt-decrypt-using-pycrypto-aes-256
    """

    def __init__(self, key):
        self.bs = 32
        self.key = hashlib.sha256(key.encode()).digest()

    def encrypt(self, raw):
        raw = self._pad(raw)
        iv = Random.new().read(AES.block_size)
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        return base64.b64encode(iv + cipher.encrypt(raw))

    def decrypt(self, enc):
        enc = base64.b64decode(enc)
        iv = enc[:AES.block_size]
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        plain = self._unpad(cipher.decrypt(enc[AES.block_size:]))
        try:
            # 如果是字节流, 比如图片, 无法用 utf-8 编码解码成 unicode 的字符串
            return plain.decode('utf-8')
        except Exception as e:
            logger.warning(e)
            return plain

    def _pad(self, s):
        return s + (self.bs - len(s) % self.bs) * chr(self.bs - len(s) % self.bs)

    @staticmethod
    def _unpad(s):
        return s[:-ord(s[len(s) - 1:])]


def utf8(value):
    """Get the UTF8-encoded version of a value."""
    if not isinstance(value, binary_type) and not isinstance(value, text_type):
        value = binary_type(value)
    if isinstance(value, text_type):
        return value.encode('utf-8')
    else:
        return value


class RequestObject(object):
    """
    请求的数据对象的封装
    """

    def __init__(self, method=None, uri=None, headers=None, body=None, host=None):
        self.method = method
        self.uri = uri
        self.headers = headers
        self.body = body
        self.host = host


class APIRequest(object):
    # TODO 重新整理代码, 将签名和加解密部分分离出来

    def __init__(self, access_key, secret_key, api_server,
                 endpoint, version='', encrypt_type='raw', *args, **kwargs):
        self.access_key = access_key
        self.secret_key = secret_key
        self.api_server = api_server
        self.endpoint = endpoint
        self.version = version
        self.encrypt_type = encrypt_type
        self.request_data = RequestObject()

        self.gateway_error_status_code = kwargs.get(
            'gateway_error_status_code', GATEWAY_ERROR_STATUS_CODE)
        self.signature_expire_seconds = kwargs.get(
            'signature_expire_seconds', SIGNATURE_EXPIRE_SECONDS)

    @staticmethod
    def parse_uri(url):
        url_parsed = urlparse(url)
        uri = urlunparse(('', '', url_parsed.path, url_parsed.params,
                          url_parsed.query, url_parsed.fragment))

        return uri

    def get_auth_headers(self):
        headers = {
            'X-Api-Timestamp': text_type(int(time.time())),
            'X-Api-Nonce': text_type(random.random()),
            'X-Api-Access-Key': text_type(self.access_key),
            'X-Api-Encrypt-Type': text_type(self.encrypt_type)
        }

        return headers

    def get_real_url(self, uri):
        url = '/'.join([self.api_server.strip(), self.endpoint.strip().strip('/'),
                        self.version.strip().strip('/')]) + uri.strip()
        return url

    def encrypt_data(self):
        # if self.encrypt_type == 'raw':
        #     raise ValueError

        aes_cipher = AESCipher(self.secret_key)
        headers_str = json.dumps(self.request_data.headers)
        # 加密 Headers 和 url
        self.request_data.headers = {
            'Content-Type': 'application/octet-stream',
            'X-Api-Encrypted-Headers': aes_cipher.encrypt(utf8(headers_str)),
            'X-Api-Encrypted-Uri': aes_cipher.encrypt(utf8(self.request_data.uri))
        }
        self.request_data.uri = '/?_t=%d&_nonce=%s' % \
                                (int(time.time()), text_type(random.random()))

        # 设置一个新的 url
        url = self.api_server.strip() + self.request_data.uri

        if self.request_data.body is not None and len(self.request_data.body) > 0:
            self.request_data.body = aes_cipher.encrypt(utf8(self.request_data.body))
            logger.debug(self.request_data.body)
        return url

    def decrypt_data(self, body):
        try:
            aes_cipher = AESCipher(self.secret_key)
            if body and len(body) > 0:
                logger.debug('解密 body')
                logger.debug(body.encode('hex'))
                body = aes_cipher.decrypt(utf8(body))
                # logger.debug(body.decode('hex'))
        except Exception as e:
            logger.error('解密数据出错')
            logger.error(e)
            logger.error(traceback.format_exc())
            return None

        return body

    def get(self, uri, headers=None):
        url = self.get_real_url(uri)
        logger.debug(url)
        self.request_data.host = urlparse(url).netloc
        self.request_data.uri = self.parse_uri(url)
        self.request_data.method = 'GET'
        if headers is None:
            self.request_data.headers = {}
        else:
            # headers 是字典
            self.request_data.headers = headers
        self.request_data.headers['Accept'] = 'application/json; charset=utf-8'
        self.request_data.body = ''

        if self.encrypt_type == 'aes':
            url = self.encrypt_data()

        self.request_data.headers.update(self.get_auth_headers())
        signature = self.signature_request()
        self.request_data.headers['X-Api-Signature'] = signature

        r = requests.get(url, headers=self.request_data.headers)
        logger.debug(r.status_code)
        if r.status_code != self.gateway_error_status_code:
            is_valid = self.check_response(r)
            if not is_valid:
                logger.debug('返回结果签名不正确')

        r_encrypt_type = r.headers.get('x-api-encrypt-type', 'raw')
        if r_encrypt_type == 'aes':
            r._content = self.decrypt_data(r.content)

        return r

    def post(self, uri, headers=None, body=None):
        url = self.get_real_url(uri)
        logger.debug(url)
        self.request_data.host = urlparse(url).netloc
        self.request_data.uri = self.parse_uri(url)
        self.request_data.method = 'POST'
        if headers is None:
            self.request_data.headers = {}
        else:
            # headers 是字典
            self.request_data.headers = headers

        self.request_data.headers['Accept'] = 'application/json; charset=utf-8'
        self.request_data.body = body

        if self.encrypt_type == 'aes':
            url = self.encrypt_data()
        self.request_data.headers.update(self.get_auth_headers())
        logger.debug(self.request_data.headers)
        signature = self.signature_request()
        self.request_data.headers['X-Api-Signature'] = signature
        r = requests.post(url, headers=self.request_data.headers,
                          data=utf8(self.request_data.body))

        logger.debug(url)
        logger.debug(self.request_data.headers)
        # logger.debug(self.request_data.body)

        if r.status_code != self.gateway_error_status_code:
            is_valid = self.check_response(r)
            if not is_valid:
                logger.debug('返回结果签名不正确')

        r_encrypt_type = r.headers.get('x-api-encrypt-type', 'raw')
        if r_encrypt_type == 'aes':
            r._content = self.decrypt_data(r.content)

        return r

    def sign_string(self, string_to_sign):
        new_hmac = hmac.new(utf8(self.secret_key), digestmod=sha256)
        new_hmac.update(utf8(string_to_sign))
        return new_hmac.digest().encode("base64").rstrip('\n')

    def headers_to_sign(self):
        """
        Select the headers from the request that need to be included
        in the StringToSign.
        """
        headers_to_sign = {'Host': self.request_data.host}
        for name, value in self.request_data.headers.items():
            l_name = name.lower()
            if l_name.startswith('x-api'):
                headers_to_sign[name] = value
        return headers_to_sign

    def canonical_headers(self, headers_to_sign):
        """
        Return the headers that need to be included in the StringToSign
        in their canonical form by converting all header keys to lower
        case, sorting them in alphabetical order and then joining
        them into a string, separated by newlines.
        """
        l = sorted(['%s: %s' % (n.lower().strip(),
                                headers_to_sign[n].strip()) for n in headers_to_sign])
        return '\n'.join(l)

    def string_to_sign(self):
        """
        Return the canonical StringToSign as well as a dict
        containing the original version of all headers that
        were included in the StringToSign.
        """
        headers_to_sign = self.headers_to_sign()
        canonical_headers = self.canonical_headers(headers_to_sign)
        string_to_sign = b'\n'.join([utf8(self.request_data.method.upper()),
                                     utf8(self.request_data.uri),
                                     utf8(canonical_headers),
                                     utf8(self.request_data.body)])
        return string_to_sign

    def response_headers_to_sign(self, headers):
        """
        Select the headers from the request that need to be included
        in the StringToSign.
        """
        headers_to_sign = {}
        for name, value in headers.items():
            l_name = name.lower()
            if l_name.startswith('x-api'):
                headers_to_sign[name] = value
        return headers_to_sign

    def response_string_to_sign(self, response):
        """
        Return the canonical StringToSign as well as a dict
        containing the original version of all headers that
        were included in the StringToSign.
        """
        headers_to_sign = self.response_headers_to_sign(response.headers)
        canonical_headers = self.canonical_headers(headers_to_sign)
        string_to_sign = b'\n'.join([utf8(self.request_data.method.upper()),
                                     utf8(self.request_data.uri),
                                     utf8(canonical_headers),
                                     utf8(response.content)])
        return string_to_sign

    def signature_request(self):
        string_to_sign = self.string_to_sign()
        logger.debug(string_to_sign)
        # 如果不是 unicode 输出会引发异常
        # logger.debug('string_to_sign: %s' % string_to_sign.decode('utf-8'))
        hash_value = sha256(utf8(string_to_sign)).hexdigest()
        signature = self.sign_string(hash_value)
        return signature

    def check_response(self, response):
        logger.debug(response.headers)
        try:
            timestamp = int(response.headers.get('X-Api-Timestamp'))
        except ValueError:
            logger.debug('Invalid X-Api-Timestamp Header')
            return False

        now_ts = int(time.time())
        if abs(timestamp - now_ts) > self.signature_expire_seconds:
            logger.debug('Expired signature, timestamp: %s' % timestamp)
            logger.debug('Expired Signature')
            return False

        signature = response.headers.get('X-Api-Signature')
        if signature:
            del response.headers['X-Api-Signature']
        else:
            logger.debug('No signature provide')
            return False

        string_to_sign = self.response_string_to_sign(response)
        logger.debug(string_to_sign)
        # 如果不是 unicode 输出会引发异常
        # logger.debug('string_to_sign: %s' % string_to_sign.decode('utf-8'))
        hash_value = sha256(utf8(string_to_sign)).hexdigest()
        real_signature = self.sign_string(hash_value)
        if signature != real_signature:
            logger.debug('Signature not match: %s, %s' % (signature, real_signature))
            return False
        else:
            return True
