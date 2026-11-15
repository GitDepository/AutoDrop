import io,json,sys,unittest,urllib.error
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from drop_api import DropClient,ApiError,BASE
class Response:
    def __init__(self,status,body,ctype='application/json'):self.status=status;self.body=body;self.headers={'Content-Type':ctype}
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,n):return self.body[:n]
class ApiTests(unittest.TestCase):
    def test_download_auth_and_url(self):
        calls=[]
        def fake(req,timeout):calls.append(req);return Response(200,b'PKdemo','application/zip')
        r=DropClient('test-secret',fake).download();self.assertEqual(r['state'],'downloaded')
        self.assertEqual(calls[0].full_url,BASE+'/data/download');self.assertEqual(calls[0].get_header('X-api-key'),'test-secret')
    def test_missing_key(self):
        with self.assertRaises(ApiError):DropClient('').download()
    def test_preparing(self):self.assertEqual(DropClient('x',lambda *a,**k:Response(202,b'{}')).download()['state'],'preparing')
    def test_empty(self):self.assertEqual(DropClient('x',lambda *a,**k:Response(200,b'{}')).download()['state'],'no_data')
    def test_rate_limit_retry(self):
        calls=[];sleeps=[]
        def fake(req,timeout):
            calls.append(req)
            if len(calls)==1:raise urllib.error.HTTPError(req.full_url,429,'rate limit',{},None)
            return Response(202,b'{}')
        self.assertEqual(DropClient('x',fake,sleeps.append).download()['state'],'preparing');self.assertEqual(sleeps,[30])
    def test_auth_never_retried(self):
        calls=[]
        def fake(req,timeout):calls.append(req);raise urllib.error.HTTPError(req.full_url,401,'bad',{},None)
        with self.assertRaises(ApiError):DropClient('x',fake).download()
        self.assertEqual(len(calls),1)
    def test_multipart_and_amend(self):
        for amend in (False,True):
            calls=[]
            def fake(req,timeout):calls.append(req);return Response(202,json.dumps({'accepted':[{'fileName':'20261114_0000_Email.csv'}],'rejected':[]}).encode())
            result=DropClient('x',fake).upload({'20261114_0000_Email.csv':b'Id,Status\r\nABCDEF123456,5\r\n'},amend)
            self.assertEqual(result['state'],'queued_for_validation');self.assertIn(b'name="files"',calls[0].data)
            self.assertTrue(calls[0].full_url.endswith('/data/amend' if amend else '/data/upload'))
    def test_partial_acceptance(self):
        r=DropClient('x',lambda *a,**k:Response(202,b'{"accepted":[],"rejected":[{"fileName":"a.csv"}]}')).upload({'a.csv':b'Id,Status'})
        self.assertEqual(r['state'],'partial_or_rejected')
    def test_post_not_retried_on_timeout(self):
        calls=[]
        def fake(*a,**k):calls.append(1);raise TimeoutError()
        with self.assertRaises(ApiError):DropClient('x',fake).upload({'a.csv':b''})
        self.assertEqual(len(calls),1)
    def test_malformed_upload_response(self):
        with self.assertRaises(ApiError):DropClient('x',lambda *a,**k:Response(202,b'{}')).upload({'a.csv':b''})
    def test_redirect_blocked(self):
        from drop_api import NoRedirect
        self.assertIsNone(NoRedirect().redirect_request(None,None,None,None,None,None))
if __name__=='__main__':unittest.main()
