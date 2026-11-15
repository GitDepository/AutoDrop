"""Actual DROP sandbox HTTP adapter, based on official v1.2 documentation.
Sandbox only: this prototype must never submit mock-database outcomes to production.
"""
import json,os,time,uuid,urllib.request,urllib.error
BASE='https://api.drop.privacy.ca.gov/sandbox'
class ApiError(Exception):
    def __init__(self,message,status=None):super().__init__(message);self.status=status
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None
class DropClient:
    def __init__(self,key=None,opener=None,sleep=time.sleep):
        self.key=key or os.environ.get('DROP_API_KEY','');self.opener=opener or urllib.request.build_opener(NoRedirect()).open;self.sleep=sleep
    def request(self,method,path,body=None,content_type=None):
        if not self.key:raise ApiError('Set DROP_API_KEY to your sandbox key, then restart the server.')
        headers={'X-API-KEY':self.key,'Accept':'application/zip, application/json' if method=='GET' else 'application/json'}
        if content_type:headers['Content-Type']=content_type
        # Download retries only. An ambiguous POST failure needs operator reconciliation.
        attempts=3 if method=='GET' else 1
        for attempt in range(attempts):
            try:
                req=urllib.request.Request(BASE+path,data=body,headers=headers,method=method)
                with self.opener(req,timeout=30) as r:
                    data=r.read(20*1024*1024+1)
                    if len(data)>20*1024*1024:raise ApiError('Response exceeds prototype 20 MB limit')
                    return r.status,r.headers.get('Content-Type',''),data
            except urllib.error.HTTPError as e:
                if e.code in (429,500,503) and attempt+1<attempts:
                    self.sleep(30 if e.code==429 else 2**attempt);continue
                hints={401:'Invalid sandbox API key.',403:'Check sandbox access and selected lists.',404:'Endpoint unavailable; check current official API documentation.',429:'Rate limited. Wait at least 30 seconds before retrying.',400:'Request rejected; verify CSV files.',409:'Resolve outstanding work in the portal.'}
                raise ApiError(f'HTTP {e.code}: '+hints.get(e.code,'API request failed; reconcile in the portal before retrying uploads.'),e.code) from None
            except (urllib.error.URLError,TimeoutError,OSError):
                raise ApiError('Network failure. For uploads, delivery is unknown: check the portal before retrying.') from None
    def download(self):
        status,kind,data=self.request('GET','/data/download')
        if status==200 and ('zip' in kind.lower() or data.startswith(b'PK')):return {'state':'downloaded','data':data,'http':status}
        if status not in (200,202):raise ApiError('Unexpected download response',status)
        return {'state':'preparing' if status==202 else 'no_data','http':status}
    def upload(self,files,amend=False):
        boundary='drop'+uuid.uuid4().hex;parts=[]
        for filename,data in files.items():
            if any(ch in filename for ch in '\r\n"/\\'):raise ApiError('Unsafe upload filename')
            parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{filename}"\r\nContent-Type: text/csv\r\n\r\n'.encode(),data,b'\r\n'])
        parts.append(f'--{boundary}--\r\n'.encode())
        status,kind,data=self.request('POST','/data/amend' if amend else '/data/upload',b''.join(parts),'multipart/form-data; boundary='+boundary)
        try: result=json.loads(data)
        except (ValueError,UnicodeError):raise ApiError('Upload delivery unknown: response was not JSON. Reconcile in portal.')
        if not isinstance(result,dict) or not isinstance(result.get('accepted'),list) or not isinstance(result.get('rejected'),list):raise ApiError('Upload delivery unknown: unexpected response schema. Reconcile in portal.')
        accepted=[i.get('fileName') for i in result['accepted'] if isinstance(i,dict)]
        return {'http':status,'state':'queued_for_validation' if set(accepted)==set(files) and not result['rejected'] else 'partial_or_rejected','accepted':accepted,'rejected':result['rejected'],'note':'Acceptance is not final validation. Check portal/email for row-level results.'}
