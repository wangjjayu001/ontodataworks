"""Read-only HTTPS formal/guest acceptance; credentials stay in runtime local/."""
import argparse
import http.cookiejar
import json
import ssl
import time
import urllib.request
import urllib.error
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--runtime-root',type=Path,required=True)
    parser.add_argument('--base-url',default='https://localhost')
    args=parser.parse_args()
    tls=ssl.create_default_context(cafile=str(args.runtime_root/'local/tls/root-ca.crt'))
    class Client:
        def __init__(self):
            self.csrf='';self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                urllib.request.HTTPSHandler(context=tls),urllib.request.ProxyHandler({}))
        def call(self,path,body=None,status=200):
            headers={'Content-Type':'application/json','Origin':args.base_url}
            if self.csrf:headers['X-CSRF-Token']=self.csrf
            request=urllib.request.Request(args.base_url+'/api'+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
            try:response=self.opener.open(request,timeout=60)
            except urllib.error.HTTPError as exc:response=exc
            result=json.load(response)
            assert response.code==status,(path,response.code)
            return result
    admin=Client();guest=Client()
    credentials=json.loads((args.runtime_root/'local/credentials.json').read_text(encoding='utf-8'))
    admin.csrf=admin.call('/auth/login',{'username':credentials['admin_username'],'password':credentials['admin_password']})['csrf']
    guest.csrf=guest.call('/auth/guest',{})['csrf']
    guest_me=guest.call('/auth/me');guest_wid=guest_me['workspaces'][0]['id']
    report={'version':'1.2.0','modes':[]}
    for label,client,wid in [('formal',admin,3),('guest',guest,guest_wid)]:
        assets=client.call(f'/workspaces/{wid}/assets');ids={a['id'] for a in assets}
        focus=next(a for a in assets if a['table_name']=='dwd_orders')
        started=time.perf_counter();data=client.call(f"/workspaces/{wid}/assets/{focus['id']}/lineage")
        assert data['workspace_id']==wid and data['table_edges'] and data['column_edges']
        assert all(n.get('asset_id') in ids for n in data['nodes'] if n['kind']=='table')
        assert all(any(r['id']==e['record_id'] for r in data['records']) for e in data['column_edges'])
        assert any(e['source_column']=='order_id' and e['target_column']=='order_id' for e in data['column_edges'])
        assert all(r['column_status'] in {'PARSED','UNRESOLVED'} for r in data['records'])
        without=client.call(f"/workspaces/{wid}/assets/{focus['id']}/lineage?include_queries=false&depth=2")
        assert not any(n['kind']=='query' for n in without['nodes'])
        summary={'mode':label,'workspace_id':wid,'asset_id':focus['id'],'tables':len(assets),'nodes':len(data['nodes']),
            'table_edges':len(data['table_edges']),'column_edges':len(data['column_edges']),
            'parsed_records':sum(r['column_status']=='PARSED' for r in data['records']),
            'statuses':sorted({r['status'] for r in data['records']}),'seconds':round(time.perf_counter()-started,3)}
        report['modes'].append(summary);print(json.dumps(summary,ensure_ascii=False),flush=True)
    guest.call(f'/workspaces/3/assets/{focus["id"]}/lineage',status=403)
    guest.call(f'/workspaces/{guest_wid}/agent/models',status=403)
    guest.call(f'/workspaces/{guest_wid}/assets/{report["modes"][0]["asset_id"]}/lineage',status=404)
    report.update(cross_workspace_denied=True,guest_model_disabled=True,passed=True)
    output=Path(__file__).resolve().parents[1]/'evidence/v12/lineage-http.json';output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    guest.call('/auth/logout',{});admin.call('/auth/logout',{})
    print('PASS scoped formal/guest lineage and isolation',flush=True)


if __name__=='__main__':main()
