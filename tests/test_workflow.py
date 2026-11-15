import base64,csv,io,json,tempfile,unittest,zipfile,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from normalization import standardize,identifier,sha,name
from seed import seed,make_zip,fixture_rows,csv_bytes
from engine import ingest,match,act,execute_ready,exports,parse_zip
from store import connect,add_customer

def archive(kind,rows,header=None,name_override=None):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:z.writestr(name_override or f'20261114_0000_{kind}.csv',csv_bytes(header or ['Id','Hash'],rows))
    return b.getvalue()
class NormalizationTests(unittest.TestCase):
    def test_official_email_vector(self):
        self.assertEqual(identifier('Email',{'Email':'Anna.Smith@Domain.com'}),'KA18MT/ph6IHYjzT9zwETySDQyvSh87YuoSBpOQtkhE=')
    def test_official_phone_vector(self):
        self.assertEqual(identifier('Phone',{'Phone':'+1(415)555-9317'}),'vGM7y5n+hBXRSEAklhHDPCbysyNgYTmXdMcagGUOY8E=')
    def test_composite_vector(self):
        self.assertEqual(identifier('NDZ',{'FirstName':'Danielle','LastName':'Johnson','DOB':'1985-07-04','ZIP':'91790'}),'PQOfn1RffEKmqMmNAzDKKaoZCwxWbQZkQzPWmQo9REA=')
    def test_namevin_vector(self):
        self.assertEqual(identifier('NameVIN',{'FirstName':'Eve','LastName':'Genesis','VIN':'1HGCM82633A004352'}),'rtnDuXIe63jXYQQXW5r07GJ7lSsrib8+46QuKFwkOmk=')
    def test_email_preserves_plus_and_dots(self):self.assertEqual(standardize('Email',' A.B+X@EXAMPLE.test '),'a.b+x@example.test')
    def test_unicode(self):
        self.assertEqual(name('José O’Neill'),'joseoneill');self.assertEqual(name('Щука Ёж'),'shchukayozh');self.assertEqual(name('Γιώργος'),'giorgos')
        self.assertEqual(name('김민수'),'김민수');self.assertEqual(name('李明'),'李明')
    def test_missing_composite(self):self.assertIsNone(identifier('NDZ',{'FirstName':'A'}))
    def test_zip_rules(self):
        for a,b in [('91790-3771','91790'),('M1B 1A1','m1b1a'),(' 00712345','71234'),('00300-9999','300')]:self.assertEqual(standardize('ZIP',a),b)
    def test_device_ids(self):
        self.assertIsNone(identifier('MAID',{'MAID':'abc'}));self.assertTrue(identifier('CTVID',{'CTVID':'SmartTV_9F8E7D6C'}))
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'db.sqlite';seed(self.path);self.raw=make_zip(fixture_rows())
    def tearDown(self):self.tmp.cleanup()
    def load(self):ingest(self.path,self.raw,'demo');match(self.path)
    def test_full_demo_and_export(self):
        self.load()
        with connect(self.path) as c:self.assertEqual(dict(c.execute('SELECT state,count(*) FROM requests GROUP BY state').fetchall()),{'complete':1,'ready':22,'review':3})
        execute_ready(self.path)
        act(self.path,'DEMO00000002','optout','Household members confirmed')
        act(self.path,'DEMO00000004','exempt','Synthetic exemption fixture confirmed')
        act(self.path,'DEMO00000005','delete','Synthetic duplicate identity confirmed',True)
        files=exports(self.path,1)
        statuses=[r['Status'] for b in files.values() for r in csv.DictReader(io.StringIO(b.decode()))]
        self.assertEqual({v:statuses.count(v) for v in set(statuses)},{'2':1,'3':23,'4':1,'5':1})
        with connect(self.path) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM customers').fetchone()[0],976)
            self.assertEqual(c.execute('SELECT count(*) FROM customers WHERE opted_out=1').fetchone()[0],2)
            self.assertFalse(c.execute('SELECT 1 FROM hash_index WHERE customer_id=1').fetchone())
            self.assertEqual(c.execute("SELECT count(*) FROM audit WHERE event='delete'").fetchone()[0],23)
    def test_idempotent_import(self):
        ingest(self.path,self.raw,'demo');self.assertTrue(ingest(self.path,self.raw,'demo')['duplicate'])
        with connect(self.path) as c:self.assertEqual(c.execute('SELECT count(*) FROM requests').fetchone()[0],26)
    def test_idempotent_execution(self):
        self.load();act(self.path,'DEMO00000000','delete');self.assertTrue(act(self.path,'DEMO00000000','delete')['already_complete'])
    def test_review_cannot_silently_delete(self):
        self.load()
        with self.assertRaises(ValueError):act(self.path,'DEMO00000005','delete')
        with self.assertRaises(ValueError):act(self.path,'DEMO00000002','delete','Attempt delete shared')
        with self.assertRaises(ValueError):act(self.path,'DEMO00000004','delete','Attempt delete exempt')
    def test_unresolved_export_blocked(self):
        self.load()
        with self.assertRaises(ValueError):exports(self.path,1)
    def test_suppression_blocks_new_intake(self):
        self.load()
        with connect(self.path) as c:self.assertIsNone(add_customer(c,fixture_rows()[0],'new-person'))
    def test_removed_identifier(self):
        self.load();h=identifier('Email',fixture_rows()[0])
        ingest(self.path,archive('Removed',[['DEMO00000000',h,'Email']],['Id','Hash','ListType']),'demo')
        with connect(self.path) as c:
            self.assertEqual(c.execute("SELECT active FROM requests WHERE id='DEMO00000000'").fetchone()[0],0)
            self.assertIsNotNone(add_customer(c,fixture_rows()[0],'new-person'))
        with self.assertRaises(ValueError):act(self.path,'DEMO00000000','delete')
    def test_conflicting_id_rolls_back_batch(self):
        self.load()
        with self.assertRaises(ValueError):ingest(self.path,archive('Email',[['DEMO00000000',sha('other')]]),'demo')
        with connect(self.path) as c:self.assertEqual(c.execute('SELECT count(*) FROM batches').fetchone()[0],1)
    def test_zip_path_rejected(self):
        with self.assertRaises(ValueError):parse_zip(archive('Email',[],name_override='../bad.csv'))
    def test_invalid_hash(self):
        with self.assertRaises(ValueError):parse_zip(archive('Email',[['INVALID00001','bad']]))
    def test_duplicate_id_rejected(self):
        with self.assertRaises(ValueError):parse_zip(archive('Email',[['DUPLICATE001',sha('a')],['DUPLICATE001',sha('a')]]))
    def test_bad_header(self):
        with self.assertRaises(ValueError):parse_zip(archive('Email',[],['id','hash']))
    def test_all_six_lists(self):self.assertEqual({r[1] for r in parse_zip(self.raw)},{'Email','Phone','NDZ','NameVIN','MAID','CTVID'})
if __name__=='__main__':unittest.main()
