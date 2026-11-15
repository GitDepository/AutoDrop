import json,tempfile,unittest,sys
from datetime import date,timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from store import connect,add_customer
from seed import seed,fixture_rows,make_zip
from engine import ingest,match,execute_ready,exports
from review import get_case,decide

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'review.sqlite';seed(self.path);ingest(self.path,make_zip(fixture_rows()),'demo');match(self.path)
    def tearDown(self):self.tmp.cleanup()
    def payload(self,rid='DEMO00000005',action='approve',**extras):
        s=get_case(self.path,rid)
        p={'id':rid,'action':action,'version':s['version'],'reviewer':'Reviewer One','note':'Compared source records and verified the synthetic fixture evidence.','reason':'synthetic_fixture_verified','evidence_ref':'seed.py DUP-001 synthetic fixture','confirmed_scope':True,'confirmed_evidence':True,'candidate_choices':{'7':'accept'}}
        p.update(extras);return p
    def count(self):
        with connect(self.path) as c:return c.execute('SELECT count(*) FROM customers').fetchone()[0]
    def test_simple_reject_exemption_deletes(self):
        p=self.payload('DEMO00000004',candidate_choices={});p.update(simple=True,reason_text='Exemption is unsupported in this synthetic case',reject_exemption=True)
        decide(self.path,p)
        self.assertEqual(get_case(self.path,'DEMO00000004')['status'],3)
        with connect(self.path) as c:self.assertIsNone(c.execute('SELECT id FROM customers WHERE id=5').fetchone())
    def test_simple_keep_exemption_retains(self):
        p=self.payload('DEMO00000004',candidate_choices={});p.update(simple=True,reason_text='Retain under the synthetic classroom exemption')
        decide(self.path,p)
        self.assertEqual(get_case(self.path,'DEMO00000004')['status'],2)
        self.assertEqual(self.count(),1000)
    def test_shared_exact_evidence_not_ai(self):
        s=get_case(self.path,'DEMO00000002');self.assertIsNone(s['model_version']);self.assertEqual(s['proposed_action'],'optout')
        self.assertTrue(all(r['hash_equal'] and r['role']=='exact_hash' for r in s['records']))
        self.assertEqual(next(f['relation'] for f in s['comparisons'] if f['field']=='DOB'),'Different')
        self.assertTrue(all(r['provenance']['source']=='Synthetic generator' for r in s['records']))
    def test_ai_features_and_provenance(self):
        s=get_case(self.path,'DEMO00000005');r=next(r for r in s['records'] if r['id']==7)
        self.assertFalse(r['hash_equal']);self.assertEqual(r['model_features']['DOB equality'],1)
        self.assertIn('DUP-001',r['provenance']['context'])
    def test_approve_includes_only_explicit_links(self):
        decide(self.path,self.payload());self.assertEqual(self.count(),998)
        with connect(self.path) as c:
            row=c.execute("SELECT detail FROM audit WHERE event='review_approved'").fetchone();d=json.loads(row[0]);self.assertEqual(d['reviewer'],'Reviewer One');self.assertEqual(d['accepted_suggestions'],[7]);self.assertEqual(len(d['evidence_version']),64)
    def test_explicit_reject_then_approve_exact_only(self):
        decide(self.path,self.payload(action='reject_link',reason='insufficient_evidence',candidate_ids=[7]))
        self.assertEqual(self.count(),1000);s=get_case(self.path,'DEMO00000005');self.assertEqual(s['state'],'review');self.assertIsNone(s['status']);self.assertEqual(s['rejected_ids'],[7])
        decide(self.path,self.payload(candidate_choices={}));self.assertEqual(self.count(),999)
        with connect(self.path) as c:self.assertIsNotNone(c.execute('SELECT * FROM customers WHERE id=7').fetchone())
    def test_reject_at_approval_is_logged(self):
        decide(self.path,self.payload(candidate_choices={'7':'reject'}));self.assertEqual(self.count(),999)
        with connect(self.path) as c:self.assertEqual(json.loads(c.execute("SELECT detail FROM audit WHERE event='review_approved'").fetchone()[0])['rejected_suggestions'],[7])
    def test_unresolved_suggestion_blocks_approval(self):
        with self.assertRaisesRegex(ValueError,'every unresolved'):decide(self.path,self.payload(candidate_choices={}))
        self.assertEqual(self.count(),1000)
    def test_cannot_reject_exact_as_ai(self):
        with self.assertRaisesRegex(ValueError,'Exact matches'):decide(self.path,self.payload('DEMO00000002',action='reject_link',reason='incorrect_source_data',candidate_ids=[3]))
    def test_defer_requires_owner_due(self):
        for extra in ({'owner':''},{'due_date':'bad'},{'due_date':'2000-01-01'}):
            p=self.payload(action='defer',reason='insufficient_evidence',owner='Privacy team',due_date=date.today().isoformat());p.update(extra)
            with self.assertRaises(ValueError):decide(self.path,p)
    def test_hold_reopen_then_approve(self):
        decide(self.path,self.payload(action='defer',reason='awaiting_source_verification',owner='Privacy team',due_date=(date.today()+timedelta(days=2)).isoformat()))
        self.assertEqual(self.count(),1000);self.assertEqual(get_case(self.path,'DEMO00000005')['state'],'deferred')
        with self.assertRaises(ValueError):decide(self.path,self.payload())
        with self.assertRaises(ValueError):exports(self.path,1)
        decide(self.path,self.payload(action='reopen',reason='new_evidence'))
        self.assertEqual(get_case(self.path,'DEMO00000005')['state'],'review')
        decide(self.path,self.payload());self.assertEqual(self.count(),998)
    def test_dispute_never_denies_consumer_request(self):
        decide(self.path,self.payload('DEMO00000002',action='dispute',reason='incorrect_source_data',owner='Source team',due_date=date.today().isoformat()))
        execute_ready(self.path);s=get_case(self.path,'DEMO00000002');self.assertEqual(s['state'],'disputed');self.assertIsNone(s['status'])
        with connect(self.path) as c:self.assertEqual(c.execute('SELECT count(*) FROM customers WHERE id IN (3,4)').fetchone()[0],2)
    def test_stale_version_blocks_duplicate_review(self):
        p=self.payload();decide(self.path,self.payload(action='reject_link',reason='conflicting_identity',candidate_ids=[7]))
        with self.assertRaisesRegex(ValueError,'changed'):decide(self.path,p)
        self.assertEqual(self.count(),1000)
    def test_changed_record_invalidates_approval(self):
        p=self.payload()
        with connect(self.path) as c:
            row=json.loads(c.execute('SELECT payload FROM customers WHERE id=7').fetchone()[0]);row['DOB']='2000-01-01';c.execute('UPDATE customers SET payload=? WHERE id=7',(json.dumps(row),))
        with self.assertRaisesRegex(ValueError,'changed'):decide(self.path,p)
    def test_changed_provenance_invalidates_approval(self):
        p=self.payload()
        with connect(self.path) as c:c.execute("UPDATE record_evidence SET verification='Unverified' WHERE customer_id=7")
        with self.assertRaisesRegex(ValueError,'changed'):decide(self.path,p)
    def test_broken_hash_link_blocks_approval(self):
        with connect(self.path) as c:
            row=json.loads(c.execute('SELECT payload FROM customers WHERE id=6').fetchone()[0]);row['Email']='different@example.test';c.execute('UPDATE customers SET payload=? WHERE id=6',(json.dumps(row),))
        with self.assertRaisesRegex(ValueError,'No current direct hash'):decide(self.path,self.payload())
    def test_new_exact_record_blocks_scope(self):
        p=self.payload('DEMO00000002',candidate_choices={})
        with connect(self.path) as c:
            row=json.loads(c.execute('SELECT payload FROM customers WHERE id=3').fetchone()[0]);cid=c.execute('INSERT INTO customers(person_id,payload) VALUES(?,?)',('new-person',json.dumps(row))).lastrowid
            h=c.execute('SELECT hash FROM requests WHERE id=?',('DEMO00000002',)).fetchone()[0];c.execute('INSERT INTO hash_index VALUES(?,?,?)',('Email',h,cid))
        with self.assertRaisesRegex(ValueError,'changed'):decide(self.path,p)
        with self.assertRaisesRegex(ValueError,'Additional exact-linked'):decide(self.path,self.payload('DEMO00000002',candidate_choices={}))
    def test_exemption_needs_basis_and_scope(self):
        with self.assertRaisesRegex(ValueError,'exemption basis'):decide(self.path,self.payload('DEMO00000004',candidate_choices={}))
        decide(self.path,self.payload('DEMO00000004',candidate_choices={},exemption_basis='Synthetic exemption EXEMPT-001',exemption_scope='Retain all fields of fixture record 5'))
        self.assertEqual(self.count(),1000);self.assertEqual(get_case(self.path,'DEMO00000004')['status'],2)
    def test_required_review_inputs(self):
        for extra in ({'reviewer':''},{'note':'yes'},{'reason':'insufficient_evidence'},{'evidence_ref':''},{'confirmed_scope':False},{'confirmed_evidence':False}):
            with self.assertRaises(ValueError):decide(self.path,self.payload(**extra))
        self.assertEqual(self.count(),1000)
    def test_failed_execution_rolls_back_approval_audit(self):
        with connect(self.path) as c:c.execute('CREATE TRIGGER fail_delete BEFORE DELETE ON customers BEGIN SELECT RAISE(ABORT,"test failure"); END;')
        with self.assertRaises(Exception):decide(self.path,self.payload())
        self.assertEqual(self.count(),1000)
        with connect(self.path) as c:self.assertEqual(c.execute("SELECT count(*) FROM audit WHERE event='review_approved'").fetchone()[0],0)
    def test_legacy_database_upgrade_preserves_work(self):
        with connect(self.path) as c:
            c.execute('DROP TABLE record_evidence');c.execute('DROP TABLE review_cases')
        seed(self.path)
        self.assertEqual(self.count(),1000)
        s=get_case(self.path,'DEMO00000005');self.assertEqual(s['state'],'review');self.assertEqual(s['records'][0]['provenance']['verification'],'Unknown')
    def test_missing_provenance_is_explicit(self):
        with connect(self.path) as c:c.execute('DELETE FROM record_evidence')
        s=get_case(self.path,'DEMO00000002');self.assertEqual(s['records'][0]['provenance']['verification'],'Unknown');self.assertTrue(any('no source' in g for g in s['gaps']))
    def test_completion_cannot_be_reopened_or_replayed(self):
        decide(self.path,self.payload())
        with self.assertRaises(ValueError):decide(self.path,self.payload(action='reopen',reason='new_evidence'))
if __name__=='__main__':unittest.main()
