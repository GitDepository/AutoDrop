import base64,json,os,sys,tempfile,threading,unittest,urllib.request,urllib.error
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
from review import get_case
from seed import seed,make_zip,fixture_rows
from drop_api import ApiError
from store import connect
class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=app.DATA;app.DATA=Path(self.tmp.name)
        seed(app.db('demo'));seed(app.db('sandbox'))
    def tearDown(self):app.DATA=self.old;self.tmp.cleanup()
    def test_separate_modes(self):
        app.command('demo_import',{'mode':'demo'})
        self.assertEqual(len(app.state('demo')['requests']),26);self.assertEqual(len(app.state('sandbox')['requests']),0)
    def test_demo_upload_prohibited(self):
        with self.assertRaises(ValueError):app.command('upload',{'mode':'demo','batch_id':1,'confirm':'SANDBOX'})
    def test_mocked_real_api_workflow(self):
        # Stub transport only; ingest, match, action, export and submission records are real.
        with patch('app.DropClient') as cls,patch.dict(os.environ,{'DROP_API_KEY':'fixture-key'}):
            cls.return_value.download.return_value={'state':'downloaded','data':make_zip(fixture_rows()),'http':200}
            app.command('download',{'mode':'sandbox'});app.command('match',{'mode':'sandbox'});app.command('execute',{'mode':'sandbox'})
            for rid in ['DEMO00000002','DEMO00000004','DEMO00000005']:
                case=get_case(app.db('sandbox'),rid)
                app.command('review',{'mode':'sandbox','id':rid,'action':'approve','reviewer':'Test reviewer','reason':'synthetic_fixture_verified','note':'Reviewed the synthetic fixture evidence and scope','evidence_ref':'seed.py fixture reference','version':case['version'],'confirmed_evidence':True,'confirmed_scope':True,'candidate_choices':{str(i):'reject' for i in case['suggestion_ids']},'exemption_basis':'Synthetic fixture EXEMPT-001','exemption_scope':'Retain the entire fictional record for the demo'})
            cls.return_value.upload.return_value={'state':'queued_for_validation','http':202,'accepted':[],'rejected':[]}
            app.command('upload',{'mode':'sandbox','batch_id':1,'confirm':'SANDBOX'})
            self.assertTrue(cls.return_value.upload.called)
            with self.assertRaises(ValueError):app.command('upload',{'mode':'sandbox','batch_id':1,'confirm':'SANDBOX'})
            app.command('reconcile',{'mode':'sandbox','id':1,'result':'validated','note':'Mock portal validation evidence'})
            app.command('upload',{'mode':'sandbox','batch_id':1,'confirm':'SANDBOX','amend':True})
            self.assertTrue(cls.return_value.upload.call_args.args[1])
    def test_http_session_and_routes(self):
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler);old_port=app.PORT;app.PORT=server.server_address[1]
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start();url='http://127.0.0.1:'+str(app.PORT)
        try:
            self.assertIn(b'DROP Assistant',urllib.request.urlopen(url).read())
            data=json.loads(urllib.request.urlopen(url+'/api/state?mode=demo').read());self.assertEqual(data['total_customers'],1000)
            req=urllib.request.Request(url+'/api/demo_import',b'{"mode":"demo"}',{'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as err:urllib.request.urlopen(req)
            self.assertEqual(err.exception.code,403)
            req.add_header('X-CSRF-Token',app.TOKEN)
            self.assertEqual(json.loads(urllib.request.urlopen(req).read())['count'],26)
            req=urllib.request.Request(url+'/api/match',b'{"mode":"demo"}',{'Content-Type':'application/json','X-CSRF-Token':app.TOKEN})
            self.assertEqual(json.loads(urllib.request.urlopen(req).read())['processed'],26)
        finally:server.shutdown();server.server_close();app.PORT=old_port
if __name__=='__main__':unittest.main()
