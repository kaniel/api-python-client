# -*- coding: utf-8 -*-
# created by restran on 2016/02/18

from __future__ import unicode_literals, absolute_import

import traceback
import logging
import json as json_util
import sys
import base64
import hashlib
import random
import time
import hmac
from hashlib import sha256, sha1

import requests
from Crypto import Random
from Crypto.Cipher import AES

logger = logging.getLogger(__name__)

PY2 = sys.version_info[0] == 2
PY3 = sys.version_info[0] == 3

if PY3:
    text_type = str
    binary_type = bytes
    from urllib.parse import urlencode
    from urllib.parse import urlparse, urlunparse
else:
    from urllib import urlencode
    from urlparse import urlparse, urlunparse

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
        return to_unicode(base64.b64encode(iv + cipher.encrypt(raw)))

    def decrypt(self, enc):
        logger.debug(type(enc))
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
        return s + (self.bs - len(s) % self.bs) * utf8(chr(self.bs - len(s) % self.bs))

    @staticmethod
    def _unpad(s):
        return s[:-ord(s[len(s) - 1:])]


_UTF8_TYPES = (bytes, type(None))


def utf8(value):
    """Converts a string argument to a byte string.

    If the argument is already a byte string or None, it is returned unchanged.
    Otherwise it must be a unicode string and is encoded as utf8.
    """
    if isinstance(value, _UTF8_TYPES):
        return value
    if not isinstance(value, text_type):
        raise TypeError(
            "Expected bytes, unicode, or None; got %r" % type(value)
        )
    return value.encode("utf-8")


_TO_UNICODE_TYPES = (text_type, type(None))


def to_unicode(value):
    """Converts a string argument to a unicode string.

    If the argument is already a unicode string or None, it is returned
    unchanged.  Otherwise it must be a byte string and is decoded as utf8.
    """
    if isinstance(value, _TO_UNICODE_TYPES):
        return value
    if not isinstance(value, bytes):
        raise TypeError(
            "Expected bytes, unicode, or None; got %r" % type(value)
        )
    return value.decode("utf-8")


def utf8_encoded_dict(in_dict):
    """
    使用 utf-8 重新编码字典
    :param in_dict:
    :return:
    """
    out_dict = {}
    for k, v in in_dict.items():
        out_dict[utf8(k)] = utf8(v)
    return out_dict


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


class APIClient(object):
    def __init__(self, access_key, secret_key, api_server, *args, **kwargs):
        self.access_key = access_key
        self.secret_key = secret_key
        self.api_server = api_server

        self.gateway_error_status_code = kwargs.get(
            'gateway_error_status_code', GATEWAY_ERROR_STATUS_CODE)
        self.signature_expire_seconds = kwargs.get(
            'signature_expire_seconds', SIGNATURE_EXPIRE_SECONDS)


class APIRequest(object):
    # TODO 重新整理代码, 将签名和加解密部分分离出来

    def __init__(self, client, endpoint, version='', encrypt_type='raw',
                 require_hmac=True, require_response_sign=True, *args, **kwargs):
        self.access_key = client.access_key
        self.secret_key = client.secret_key
        self.api_server = client.api_server.strip()
        self.endpoint = endpoint.strip().strip('/')
        self.version = version.strip().strip('/')
        self.encrypt_type = encrypt_type
        self.require_hmac = require_hmac
        self.require_response_sign = require_response_sign
        self.request_data = RequestObject()

        self.gateway_error_status_code = client.gateway_error_status_code
        self.signature_expire_seconds = client.signature_expire_seconds

    def prepare_request(self, method, uri, params=None, headers=None, data=None, json=None):
        params = {} if params is None else params
        if not isinstance(params, dict):
            raise TypeError('params should be dict')

        method = method.upper()
        params = utf8_encoded_dict(params)
        logger.debug(uri)
        url = '/'.join([self.api_server, self.endpoint, self.version]) + uri.strip()
        logger.debug(url)
        url_parsed = urlparse(url)
        enc_params = urlencode(params)
        logger.debug(enc_params)
        if url_parsed.query == '' or url_parsed.query is None:
            query = enc_params
        elif enc_params == '' or enc_params is None:
            query = url_parsed.query
        else:
            query = '%s&%s' % (url_parsed.query, enc_params)

        real_uri = urlunparse(('', '', url_parsed.path, url_parsed.params,
                               query, url_parsed.fragment))

        real_url = urlunparse((url_parsed.scheme, url_parsed.netloc, url_parsed.path,
                               url_parsed.params,
                               query, url_parsed.fragment))

        self.request_data.host = url_parsed.netloc
        self.request_data.uri = real_uri
        self.request_data.method = method
        self.request_data.headers = {
            'Accept': 'application/json; charset=utf-8'
        }
        if headers is not None:
            # headers 是字典
            self.request_data.headers.update(headers)

        if method == 'GET':
            self.request_data.body = ''
        else:
            if json is not None:
                self.request_data.headers['Content-Type'] = 'application/json; charset=utf-8'
                self.request_data.body = json_util.dumps(json, ensure_ascii=False)
            else:
                self.request_data.body = data

        return real_url

    def get_auth_headers(self):
        headers = {
            'X-Api-Timestamp': text_type(int(time.time())),
            'X-Api-Nonce': text_type(random.random()),
            'X-Api-Access-Key': text_type(self.access_key),
            'X-Api-Encrypt-Type': text_type(self.encrypt_type)
        }

        # 检查是否需要返回结果的签名
        if self.require_response_sign:
            headers['X-Api-Require-Response-Signature'] = 'true'

        return headers

    def encrypt_data(self):
        aes_cipher = AESCipher(self.secret_key)
        headers_str = json_util.dumps(self.request_data.headers)
        # 加密 Headers 和 url
        self.request_data.headers = {
            'Content-Type': 'application/octet-stream',
            'X-Api-Encrypted-Headers': aes_cipher.encrypt(utf8(headers_str)),
            'X-Api-Encrypted-Uri': aes_cipher.encrypt(utf8(self.request_data.uri))
        }
        self.request_data.uri = '/%s/%s/?_t=%d&_nonce=%s' % \
                                (self.endpoint, self.version,
                                 int(time.time()), text_type(random.random()))

        # 设置一个新的 url
        url = self.api_server + self.request_data.uri

        if self.request_data.body is not None and len(self.request_data.body) > 0:
            self.request_data.body = aes_cipher.encrypt(utf8(self.request_data.body))
            logger.debug(self.request_data.body)
        return url

    def decrypt_data(self, body):
        try:
            aes_cipher = AESCipher(self.secret_key)
            if body and len(body) > 0:
                logger.debug('解密 body')
                body = aes_cipher.decrypt(utf8(body))
                # logger.debug(body.decode('hex'))
        except Exception as e:
            logger.error('解密数据出错')
            logger.error(e)
            logger.error(traceback.format_exc())
            return None

        # 由于 requests 的 content 不是 unicode 类型, 为了兼容, 这里改成 utf8
        if isinstance(body, text_type):
            body = body.encode('utf-8')

        return body

    def get(self, uri, params=None, headers=None, **kwargs):
        logger.debug(uri)
        url = self.prepare_request('GET', uri, params=params, headers=headers)
        logger.debug(url)

        if self.encrypt_type == 'aes':
            url = self.encrypt_data()

        self.request_data.headers.update(self.get_auth_headers())
        # 需要对请求的内容进行 hmac 签名
        if self.require_hmac:
            signature = self.signature_request()
            self.request_data.headers['X-Api-Signature'] = signature

        r = requests.get(url, headers=self.request_data.headers, **kwargs)
        logger.debug(r.status_code)
        if r.status_code != self.gateway_error_status_code:
            is_valid = self.check_response(r)
            if not is_valid:
                # TODO 返回结果签名不正确需要给出提示
                logger.error('返回结果签名不正确')
                raise ValueError('返回结果签名不正确')

        r_encrypt_type = r.headers.get('x-api-encrypt-type', 'raw')
        if r_encrypt_type == 'aes':
            r._content = self.decrypt_data(r.content)

        return r

    def post(self, uri, data=None, json=None, params=None, headers=None, **kwargs):
        url = self.prepare_request('POST', uri, params=params,
                                   data=data, json=json, headers=headers)

        if self.encrypt_type == 'aes':
            url = self.encrypt_data()
        self.request_data.headers.update(self.get_auth_headers())
        logger.debug(self.request_data.headers)

        # 需要对请求的内容进行 hmac 签名
        if self.require_hmac:
            signature = self.signature_request()
            self.request_data.headers['X-Api-Signature'] = signature

        r = requests.post(url, headers=self.request_data.headers,
                          data=utf8(self.request_data.body), **kwargs)

        logger.debug(url)
        logger.debug(self.request_data.headers)

        if r.status_code != GATEWAY_ERROR_STATUS_CODE:
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
        return to_unicode(b64encode(new_hmac.digest()).rstrip(b'\n'))

    def headers_to_sign(self):
        """
        Select the headers from the request that need to be included
        in the StringToSign.
        """
        headers_to_sign = {'Host': self.request_data.host}
        for name, value in iteritems(self.request_data.headers):
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
        headers_to_sign = unicode_encoded_dict(headers_to_sign)
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
        for name, value in iteritems(headers):
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
        logger.debug(utf8(string_to_sign))
        logger.debug(len(string_to_sign))
        # 如果不是 unicode 输出会引发异常
        # logger.debug('string_to_sign: %s' % string_to_sign.decode('utf-8'))
        hash_value = sha1(utf8(string_to_sign)).hexdigest()
        signature = self.sign_string(hash_value)
        return signature

    def check_response(self, response):
        # 不需要检查返回的签名就直接返回
        if not self.require_response_sign:
            return True

        # logger.debug(response.headers)
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
        hash_value = sha1(utf8(string_to_sign)).hexdigest()
        real_signature = self.sign_string(hash_value)
        if signature != real_signature:
            logger.debug('Signature not match: %s, %s' % (signature, real_signature))
            return False
        else:
            return True


if __name__ == '__main__':
    access_key = 'abcd'
    secret_key = '1234'
    api_gateway = 'http://127.0.0.1:6500'
    endpoint = 'test_api'
    version = 'v1'
    client = APIClient(access_key, secret_key, api_gateway)
    request = APIRequest(client, endpoint, version)
    # get
    r = request.get('/resource/')
    print(r.content)

    json_data = {
        'a': 1,
        'b': 'test string',
        'c': '中文'
    }

    # post
    r = request.post('/resource/', json=json_data)
    print(r.content)

    # post image
    with open('img.jpg', 'rb') as f:
        data = f.read()
        r = request.post('/resource/', data=data)

    # use aes encrypt
    request = APIRequest(client, endpoint, version, 'aes')
