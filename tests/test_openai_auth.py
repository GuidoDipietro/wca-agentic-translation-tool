import json
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from core import ReviewError
from openai_auth import ChatGPTAuth, ISSUER, RESOURCE


class ChatGPTAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cls.jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key()))
        cls.jwk.update(kid='test-key',alg='RS256',use='sig')

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'auth.json';self.auth=ChatGPTAuth(self.path)

    def begin(self, account=None):
        url=self.auth.start(8765,account)
        return {k:v[0] for k,v in parse_qs(urlsplit(url).query).items()}

    def exchange(self, params, client='issued-client', subject='user-1', scopes='resource.invoke chatgpt.tokens.use.direct openid', nonce=None):
        claims={'iss':ISSUER,'aud':client,'sub':subject,'exp':int(time.time())+3600,'iat':int(time.time()),'nonce':nonce or params['nonce'],'email':'person@example.org'}
        token=jwt.encode(claims,self.key,algorithm='RS256',headers={'kid':'test-key'})
        response={'access_token':'private-access','refresh_token':'private-refresh','id_token':token,'expires_in':3600,'token_type':'Bearer','scope':scopes}
        def get(url,body=None,form=False,headers=None):
            if url.endswith('openid-configuration'):return {'issuer':ISSUER,'jwks_uri':ISSUER+'/jwks','revocation_endpoint':ISSUER+'/revoke'}
            if url.endswith('/jwks'):return {'keys':[self.jwk]}
            if url.endswith('/oauth/token'):return response
            return {}
        with patch('openai_auth.http_json',side_effect=get) as http:
            account=self.auth.complete({'state':params['state'],'client_id':client,'code':'fake-code'})
            exchange=next(call for call in http.call_args_list if call.args[0].endswith('/oauth/token'))
            self.assertEqual(exchange.args[1]['redirect_uri'],'http://127.0.0.1:8765/auth/callback')
            self.assertEqual(exchange.args[1]['client_id'],client)
            self.assertIn('code_verifier',exchange.args[1])
        return account

    def test_registration_uses_pkce_and_opaque_persistent_host(self):
        p=self.begin();self.assertEqual(p['client_id'],'dynamic_agent_client')
        self.assertEqual(p['resource'],RESOURCE);self.assertEqual(p['code_challenge_method'],'S256')
        self.assertNotIn('client_secret',p);self.assertTrue(p['ext_agent_host_id'].startswith('urn:uuid:'))
        second=ChatGPTAuth(self.path);self.assertEqual(second.data['host_id'],self.auth.data['host_id'])
        self.assertNotEqual(self.begin()['state'],p['state'])
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode),0o600)

    def test_real_signature_is_verified_and_secrets_are_not_exposed(self):
        p=self.begin();account=self.exchange(p)
        status=self.auth.status();encoded=json.dumps(status)
        self.assertTrue(status['accounts'][0]['can_translate'])
        self.assertNotIn('private-access',encoded);self.assertNotIn('private-refresh',encoded)
        self.assertEqual(self.auth.access_token(),'private-access')
        self.assertEqual(self.auth.data['active'],account)
        with self.assertRaises(ReviewError):self.auth.complete({'state':p['state'],'client_id':'issued-client','code':'replay'})

    def test_bad_state_and_nonce_are_rejected_without_replacing_account(self):
        p=self.begin()
        with self.assertRaises(ReviewError):self.auth.complete({'state':'wrong','code':'bad','client_id':'issued-client'})
        with self.assertRaises(ReviewError):self.exchange(p,nonce='wrong-nonce')
        self.assertIsNone(self.auth.data['active']);self.assertFalse(self.auth.data['accounts'])

    def test_unsigned_token_and_wrong_audience_are_rejected(self):
        bad=jwt.encode({'sub':'user','iss':ISSUER,'aud':'other','exp':int(time.time())+60,'iat':int(time.time())},self.key,algorithm='RS256',headers={'kid':'test-key'})
        with patch('openai_auth.http_json',side_effect=[{'issuer':ISSUER,'jwks_uri':ISSUER+'/jwks'},{'keys':[self.jwk]}]):
            with self.assertRaises(ReviewError):self.auth.verify_identity(bad,'issued-client')
        unsigned=jwt.encode({'sub':'user'},key='',algorithm='none')
        with self.assertRaises(ReviewError):self.auth.verify_identity(unsigned,'issued-client')

    def test_returning_account_keeps_client_and_rejects_identity_change(self):
        account=self.exchange(self.begin());params=self.begin(account)
        self.assertEqual(params['client_id'],'issued-client');self.assertNotIn('agent_name_hint',params)
        self.assertIn('id_token_hint',params)
        with self.assertRaises(ReviewError):self.exchange(params,subject='other-user')
        self.assertEqual(self.auth.data['accounts'][account]['subject'],'user-1')
        self.assertEqual(self.auth.data['accounts'][account]['access_token'],'private-access')

    def test_identity_without_plan_permission_cannot_translate(self):
        self.exchange(self.begin(),scopes='openid email')
        self.assertFalse(self.auth.status()['accounts'][0]['can_translate'])
        with self.assertRaises(ReviewError):self.auth.access_token()

    def test_expired_token_refreshes_atomically_and_preserves_identity(self):
        account=self.exchange(self.begin());self.auth.data['accounts'][account]['expires_at']=0
        with patch('openai_auth.http_json',return_value={'access_token':'renewed-access','refresh_token':'renewed-refresh','expires_in':3600}) as http:
            self.assertEqual(self.auth.access_token(),'renewed-access')
            self.assertEqual(http.call_args.args[1]['client_id'],'issued-client')
            self.assertNotIn('scope',http.call_args.args[1])
        saved=json.loads(self.path.read_text());self.assertEqual(saved['accounts'][account]['refresh_token'],'renewed-refresh')

    def test_disconnect_clears_tokens_and_reports_failed_remote_revocation(self):
        account=self.exchange(self.begin())
        with patch('openai_auth.http_json',side_effect=ReviewError('Sin conexión')):self.auth.disconnect()
        self.assertNotIn('access_token',self.auth.data['accounts'][account])
        self.assertNotIn('refresh_token',self.auth.data['accounts'][account])
        self.assertFalse(self.auth.status()['accounts'][0]['connected'])
        self.assertIn('no se pudo confirmar',self.auth.last_error.lower())
        with self.assertRaises(ReviewError):self.auth.access_token()
