"""Run an isolated, repeatable demo and write actual measured results to an output folder."""
import argparse,json,time,zipfile
from pathlib import Path
from seed import seed,make_zip,fixture_rows
from store import connect
from engine import ingest,match,execute_ready,act,exports
from review import get_case,decide

def run(output,n=1000):
    output=Path(output);output.mkdir(parents=True,exist_ok=True);path=output/'run.sqlite3'
    if path.exists():raise ValueError('Use a new output directory to preserve earlier run evidence')
    t=time.perf_counter();seed(path,n);seed_s=time.perf_counter()-t
    t=time.perf_counter();ingest(path,make_zip(fixture_rows(n)),'demo');ingest_s=time.perf_counter()-t
    t=time.perf_counter();match(path);match_s=time.perf_counter()-t
    with connect(path) as c:initial=dict(c.execute('SELECT state,count(*) FROM requests GROUP BY state').fetchall())
    t=time.perf_counter();execute_ready(path);execute_s=time.perf_counter()-t
    for rid in ['DEMO00000002','DEMO00000004','DEMO00000005']:
        case=get_case(path,rid)
        decide(path,{'id':rid,'action':'approve','reviewer':'Scripted demo reviewer','reason':'synthetic_fixture_verified','note':'Reviewed the synthetic fixture evidence and confirmed action scope.','evidence_ref':'seed.py / synthetic fixture labels','version':case['version'],'confirmed_evidence':True,'confirmed_scope':True,'candidate_choices':{str(i):'accept' for i in case['suggestion_ids']},'exemption_basis':'Classroom fixture EXEMPT-001, not a legal exemption','exemption_scope':'Retain all fields in fictional record 5 for this demo'})
    with connect(path) as c:
        statuses={str(r[0]):r[1] for r in c.execute('SELECT status,count(*) FROM requests GROUP BY status')}
        remaining=c.execute('SELECT count(*) FROM customers').fetchone()[0]
        audit=[dict(r) for r in c.execute('SELECT * FROM audit ORDER BY id')]
    result={'dataset':'synthetic','database_records_before':n,'work_items':26,'initial_states':initial,'final_status_counts':statuses,'database_records_after':remaining,'timings_seconds':{'seed':round(seed_s,4),'ingest':round(ingest_s,4),'match':round(match_s,4),'execute_ready':round(execute_s,4)},'api_mode':'offline demo; no authenticated API calls','review':'Three scripted decisions against fixture ground truth; not independent human validation','accuracy_note':'Fixture outcomes only; no claim of real-world model accuracy or calibrated confidence'}
    (output/'results.json').write_text(json.dumps(result,indent=2));(output/'audit.json').write_text(json.dumps(audit,indent=2))
    with zipfile.ZipFile(output/'status_responses.zip','w',zipfile.ZIP_DEFLATED) as z:
        for name,b in exports(path,1).items():z.writestr(name,b)
    print(json.dumps(result,indent=2));return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',default='demo_run');p.add_argument('--records',type=int,default=1000);a=p.parse_args()
    if a.records<1000: p.error('--records must be at least 1000')
    run(a.output,a.records)
