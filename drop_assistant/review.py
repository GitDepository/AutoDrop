"""Reviewer evidence, workflow, and atomic decisions. No automatic consumer denial."""
import hashlib,json
from datetime import date
from store import connect,event,now
from normalization import identifier,standardize
from ai import features,score,VERSION
from engine import act
OPEN_STATES=('review','deferred','disputed')
FIELDS=('FirstName','LastName','DOB','ZIP','Email','Phone','VIN','MAID','CTVID')
REASONS={
 'approve':{'evidence_confirmed','synthetic_fixture_verified'},
 'reject_link':{'conflicting_identity','insufficient_evidence','incorrect_source_data'},
 'defer':{'insufficient_evidence','awaiting_source_verification','awaiting_policy_review'},
 'dispute':{'incorrect_source_data','conflicting_identity','hash_or_normalization_issue'},
 'reopen':{'new_evidence','source_corrected','policy_review_complete'},
}
def snapshot(c,rid):
    row=c.execute('SELECT * FROM requests WHERE id=?',(rid,)).fetchone()
    if not row:raise ValueError('Request not found')
    r=dict(row);d=json.loads(r['candidates']);d=d if isinstance(d,dict) else dict(exact=[],suggested=[],shared=False,exempt=False)
    b=dict(c.execute('SELECT * FROM batches WHERE id=?',(r['batch_id'],)).fetchone())
    case=c.execute('SELECT * FROM review_cases WHERE request_id=?',(rid,)).fetchone()
    case=dict(case) if case else {'owner':'','due_date':'','updated':''}
    records=[];raw=[];exact_payloads=[]
    rejected={i['id'] for i in d.get('rejected',[])}
    suggested={i['id']:i for i in d.get('suggested',[])}
    all_ids=sorted(set(d['exact'])|set(suggested))
    for i in all_ids:
        x=c.execute('SELECT * FROM customers WHERE id=?',(i,)).fetchone()
        x=dict(x) if x else None;ev=c.execute('SELECT * FROM record_evidence WHERE customer_id=?',(i,)).fetchone();ev=dict(ev) if ev else None
        raw.append({'id':i,'row':x,'evidence':ev})
        payload=json.loads(x['payload']) if x else {}
        h=identifier(r['kind'],payload) if x else None
        role='exact_hash' if i in d['exact'] and h==r['hash'] else 'internal_identity' if i in d['exact'] else 'ai_suggestion'
        if i in d['exact'] and x:exact_payloads.append(payload)
        records.append({'id':i,'person_id':x['person_id'] if x else 'Deleted / unavailable','data':payload,'role':role,'local_hash':h,'hash_equal':bool(h and h==r['hash']),'exempt':bool(x and x['exempt']),'opted_out':bool(x and x['opted_out']),'available':x is not None,'score':suggested.get(i,{}).get('score'),'link_rejected':i in rejected,'provenance':ev or {'source':'Not recorded','source_ref':'Not recorded','verification':'Unknown','context':'No source or verification history is available. Obtain evidence or defer.'}})
    for rec in records:
        if rec['role']=='ai_suggestion' and exact_payloads:
            anchor=max(exact_payloads,key=lambda p:score(p,rec['data']));f=features(anchor,rec['data']);rec['model_features']=dict(zip(['First-name similarity','Last-name similarity','DOB equality','ZIP equality','Phone equality'],f[1:]))
    comparisons=[]
    for f in FIELDS:
        vals=[rec['data'].get(f,'') for rec in records]
        normalized=[standardize(f,v) for v in vals]
        present=[v for v in normalized if v]
        relation='Missing' if not present else 'Incomplete' if len(present)<len(vals) else 'Agree after normalization' if len(set(present))==1 else 'Different'
        comparisons.append({'field':f,'values':vals,'normalized':normalized,'relation':relation})
    history=[dict(e) for e in c.execute('SELECT * FROM audit WHERE request_id=? ORDER BY id',(rid,))]
    blockers=[]
    if not r['active']:blockers.append('This identifier was removed; no action can be approved.')
    if r['state'] in ('deferred','disputed'):blockers.append('Case is on hold. Reopen it with resolution evidence before approval.')
    if any(not p['available'] for p in records if p['id'] in d['exact']):blockers.append('An exact-linked record is no longer available. Investigate before approving.')
    if records and not any(p['hash_equal'] for p in records if p['id'] in d['exact']):blockers.append('No current direct hash match supports this case. Investigate the source data.')
    if d['exempt'] and any(not p['exempt'] for p in records if p['id'] in d['exact']):blockers.append('Mixed exempt/non-exempt scope needs specialist resolution.')
    live_direct=[x[0] for x in c.execute('SELECT customer_id FROM hash_index WHERE kind=? AND hash=?',(r['kind'],r['hash']))]
    live_people={x[0] for i in live_direct for x in c.execute('SELECT person_id FROM customers WHERE id=?',(i,))}
    live_linked={x[0] for person in live_people for x in c.execute('SELECT id FROM customers WHERE person_id=?',(person,))}
    if live_linked-set(d['exact']):blockers.append('Additional exact-linked records appeared after matching. Investigate and rematch the scope before approving.')
    gaps=['DROP provides hashes, not raw consumer names or email ownership evidence.','No external service providers, backup stores, or production systems are connected.']
    if any(not item['evidence'] for item in raw):gaps.append('One or more records have no source provenance; reviewer evidence is required.')
    if d['shared']:gaps.append('Shared email does not establish a household relationship or merge these identities.')
    if d['exempt']:gaps.append('An exemption flag alone is not a legal basis. Record the basis and scope or defer.')
    if d['suggested']:gaps.append('Model score is experimental and uncalibrated. Equal DOB/ZIP and similar names do not independently prove identity.')
    action='optout' if d['shared'] else 'exempt' if d['exempt'] else 'delete'
    effects={'optout':'Retain all exact-linked records, mark them opted out, and record status 4. Do not merge or delete identities.','exempt':'Retain all exact-linked records and record status 2 for this synthetic exemption case.','delete':'Delete exact-linked synthetic records plus individually approved AI candidates, then record status 3. Rejected suggestions remain untouched.'}
    version=hashlib.sha256(json.dumps({'request':r,'records':raw,'live_linked':sorted(live_linked),'case':case,'audit':history[-1]['id'] if history else 0},sort_keys=True).encode()).hexdigest()
    return {'id':rid,'state':r['state'],'status':r['status'],'active':bool(r['active']),'kind':r['kind'],'request_hash':r['hash'],'file_name':r['file_name'],'batch':b,'case':case,'records':records,'comparisons':comparisons,'history':history,'blockers':blockers,'gaps':gaps,'proposed_action':action,'effect':effects[action],'exact_ids':d['exact'],'suggestion_ids':list(suggested),'rejected_ids':sorted(rejected),'model_version':VERSION if d['suggested'] else None,'version':version,'scope':'Local synthetic database only'}

def get_case(path,rid):
    with connect(path) as c:return snapshot(c,rid)

def decide(path,p):
    rid=p.get('id');action=p.get('action');actor=str(p.get('reviewer','')).strip();note=str(p.get('note','')).strip();reason=p.get('reason');ref=str(p.get('evidence_ref','')).strip()
    simple=p.get('simple') is True
    reject_exemption=simple and p.get('reject_exemption') is True
    if simple:
        actor=str(p.get('reviewer','')).strip()
        note=str(p.get('reason_text','')).strip()
        extra=str(p.get('additional_info','')).strip()
        if extra:note+='\nAdditional info: '+extra
        reason={'approve':'evidence_confirmed','reject_link':'insufficient_evidence','defer':'insufficient_evidence','dispute':'incorrect_source_data','reopen':'new_evidence'}.get(action)
        ref='Current case evidence displayed in the review card'
    if action not in REASONS:raise ValueError('Choose a supported review action')
    if len(actor)<2 or len(actor)>120:raise ValueError('Enter reviewer name or ID (2–120 characters)')
    if len(note)<(8 if simple else 20) or len(note)>4000:raise ValueError('Provide a meaningful reason (at least 8 characters)')
    if reason not in REASONS[action]:raise ValueError('Select a reason appropriate to this action')
    if len(ref)>1000:raise ValueError('Evidence reference is too long')
    with connect(path) as c:
        c.execute('BEGIN IMMEDIATE');s=snapshot(c,rid)
        if p.get('version')!=s['version']:raise ValueError('Case changed since you opened it. Reload and review the current evidence.')
        if not s['active'] or s['state'] not in OPEN_STATES:raise ValueError('This case is not open for review')
        evidence={'reviewer':actor,'reason':reason,'note':note,'evidence_ref':ref,'evidence_version':s['version']}
        if action in ('defer','dispute'):
            owner=actor if simple else str(p.get('owner','')).strip();due=date.today().isoformat() if simple else p.get('due_date','')
            if len(owner)<2 or len(owner)>120:raise ValueError('Assign a follow-up owner (2–120 characters)')
            try:target=date.fromisoformat(due)
            except (ValueError,TypeError):raise ValueError('Choose a valid follow-up date')
            if target<date.today():raise ValueError('Follow-up date cannot be in the past')
            c.execute('INSERT INTO review_cases VALUES(?,?,?,?) ON CONFLICT(request_id) DO UPDATE SET owner=excluded.owner,due_date=excluded.due_date,updated=excluded.updated',(rid,owner,due,now()))
            target_state='deferred' if action=='defer' else 'disputed';c.execute('UPDATE requests SET state=? WHERE id=?',(target_state,rid))
            event(c,'review_'+action,rid,{**evidence,'owner':owner,'follow_up_date':due,'outcome':'Unresolved; no DROP status assigned'})
            return {'state':target_state,'message':'Case held. No record action or final DROP status was recorded.'}
        if action=='reopen':
            if s['state'] not in ('deferred','disputed'):raise ValueError('Only held cases can be reopened')
            if len(ref)<5:raise ValueError('Reference the evidence resolving the hold')
            c.execute("UPDATE requests SET state='review' WHERE id=?",(rid,));event(c,'review_reopen',rid,evidence)
            return {'state':'review','message':'Reopened for review. Approval still requires a separate decision.'}
        if s['state']!='review':raise ValueError('Reopen the held case before deciding on matches or actions')
        d=json.loads(c.execute('SELECT candidates FROM requests WHERE id=?',(rid,)).fetchone()[0])
        if action=='reject_link':
            chosen=p.get('candidate_ids',[])
            if not isinstance(chosen,list) or not chosen or any(type(i)!=int or i not in s['suggestion_ids'] for i in chosen):raise ValueError('Select one or more AI suggestions to deny. Exact matches must be disputed instead.')
            rejected={i['id']:i for i in d.get('rejected',[])}
            for i in chosen:rejected[i]={'id':i,'reviewer':actor,'reason':reason}
            d['rejected']=list(rejected.values());c.execute('UPDATE requests SET candidates=? WHERE id=?',(json.dumps(d),rid))
            event(c,'review_reject_link',rid,{**evidence,'candidate_ids':chosen,'outcome':'Suggestion rejected; consumer request remains open'})
            return {'state':'review','message':'AI suggestions denied. The exact matched consumer request remains open.'}
        if s['blockers']:raise ValueError('Approval blocked: '+' '.join(s['blockers']))
        if len(ref)<5:raise ValueError('Record a source/document reference supporting this decision')
        if not simple and (p.get('confirmed_scope') is not True or p.get('confirmed_evidence') is not True):raise ValueError('Confirm that you reviewed evidence and the action scope')
        choices=p.get('candidate_choices',{})
        if not isinstance(choices,dict):raise ValueError('Candidate decisions must be an object')
        accepted=[];rejected=list(s['rejected_ids'])
        for i in s['suggestion_ids']:
            if i in s['rejected_ids']:continue
            decision=choices.get(str(i))
            if decision not in ('accept','reject'):raise ValueError('Accept or reject every unresolved AI suggestion, or defer the case')
            if decision=='accept':
                record=next(r for r in s['records'] if r['id']==i)
                if not record['available'] or record['exempt']:raise ValueError('Suggested record is unavailable or exempt; defer for investigation')
                related={x[0] for x in c.execute('SELECT id FROM customers WHERE person_id=?',(record['person_id'],))}
                if related-set(s['exact_ids'])-set(s['suggestion_ids']):raise ValueError('Suggested identity has additional unreviewed records; defer for complete scope review')
                accepted.append(i)
            else:rejected.append(i)
        for i in accepted:
            person=next(r['person_id'] for r in s['records'] if r['id']==i)
            related={x[0] for x in c.execute('SELECT id FROM customers WHERE person_id=?',(person,))}
            if related-set(s['exact_ids'])-set(accepted):raise ValueError('Accepted identity includes records excluded from scope. Defer for complete identity review.')
        if any(k not in [str(i) for i in s['suggestion_ids']] for k in choices):raise ValueError('Unknown candidate decision')
        if reject_exemption:
            if s['proposed_action']!='exempt':raise ValueError('This case has no exemption to reject')
            if any(r['provenance']['source']!='Synthetic generator' for r in s['records'] if r['id'] in s['exact_ids']):raise ValueError('Only documented synthetic exemptions can be overridden here')
            for i in s['exact_ids']:c.execute('UPDATE customers SET exempt=0 WHERE id=?',(i,))
            d['exempt']=False;c.execute('UPDATE requests SET candidates=? WHERE id=?',(json.dumps(d),rid))
            event(c,'synthetic_exemption_rejected',rid,{**evidence,'record_ids':s['exact_ids']})
            s['proposed_action']='delete'
        if s['proposed_action']=='exempt':
            basis=note if simple else str(p.get('exemption_basis','')).strip();scope='Retain all exact-linked synthetic records' if simple else str(p.get('exemption_scope','')).strip()
            if len(basis)<(8 if simple else 12) or len(scope)<12:raise ValueError('Record exemption basis and retention scope, or defer')
            evidence.update(exemption_basis=basis,exemption_scope=scope)
        # Ensure reviewer, choice record, and mutation commit or roll back together.
        event(c,'review_approved',rid,{**evidence,'action':s['proposed_action'],'exact_ids':s['exact_ids'],'accepted_suggestions':accepted,'rejected_suggestions':rejected,'scope':s['scope'],'reviewer_identity':'Self-reported; not enterprise-authenticated'})
        result=act(path,rid,s['proposed_action'],note,bool(accepted),_connection=c,selected_ids=accepted)
        return {**result,'message':'Reviewed action completed in the synthetic database.'}
