"""SQL field lineage and scoped read API regressions; no source/model calls."""
import os
import sys
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import patch
from cryptography.fernet import Fernet
os.environ.setdefault('DATABASE_URL','sqlite://')
os.environ.setdefault('ENCRYPTION_KEY',Fernet.generate_key().decode())
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'backend'))
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.db import Base, Workspace, User, Membership, Asset, Datasource, TransformRun, TransformEdge, QueryRun, Lineage
from app.catalog_lineage import derive_columns, router
from app.pipeline_models import Definition, PipelineRun
from app.security import identity, db_session
try:
    from app.guest_models import GuestSnapshot
except ImportError:
    GuestSnapshot = None


def asset(aid,name,columns):
    return SimpleNamespace(id=aid,namespace='dwd',table_name=name,columns=[{'name':n,'type':t} for n,t in columns.items()])


class ColumnParsing(TestCase):
    def setUp(self):
        self.assets=[asset(1,'orders',{'id':'int','price':'numeric','status':'text'}),asset(2,'items',{'order_id':'int','qty':'int'})]

    def test_cte_join_aggregate_values_and_condition_fields_are_distinct(self):
        rows,conditions=derive_columns("WITH x AS (SELECT o.id,o.price*i.qty AS amount FROM dwd.orders o JOIN dwd.items i ON o.id=i.order_id WHERE o.status='paid') SELECT id,SUM(amount) AS total FROM x GROUP BY id",self.assets)
        self.assertEqual({(r['asset_id'],r['column']) for r in rows[1]['sources']},{(1,'price'),(2,'qty')})
        self.assertIn({'asset_id':1,'column':'status'},conditions)
        self.assertNotIn({'asset_id':1,'column':'id'},rows[1]['sources'])

    def test_star_expansion_and_union_keep_all_physical_origins(self):
        rows,_=derive_columns('SELECT o.* FROM dwd.orders o',self.assets)
        self.assertEqual([r['name'] for r in rows],['id','price','status'])
        rows,_=derive_columns('SELECT id AS key FROM dwd.orders UNION ALL SELECT order_id AS key FROM dwd.items',self.assets)
        self.assertEqual({(s['asset_id'],s['column']) for s in rows[0]['sources']},{(1,'id'),(2,'order_id')})

    def test_constant_and_record_count_are_not_fabricated_field_edges(self):
        rows,_=derive_columns('SELECT COUNT(*) AS records, 1 AS constant FROM dwd.orders',self.assets)
        self.assertEqual(rows[0]['status'],'ROW_COUNT');self.assertEqual(rows[0]['sources'],[])
        self.assertEqual(rows[1]['status'],'CONSTANT');self.assertEqual(rows[1]['sources'],[])

    def test_unknown_or_ambiguous_field_never_becomes_a_guessed_mapping(self):
        with self.assertRaises(Exception):derive_columns('SELECT nonexistent AS total FROM dwd.orders',self.assets)
        with self.assertRaises(ValueError):derive_columns('SELECT id AS key,price AS key FROM dwd.orders',self.assets)


class LineageAPI(TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine);self.db=Session(self.engine,expire_on_commit=False)
        self.admin=User(id=1,username='admin',display_name='Admin',role='admin',password_hash='unused')
        self.guest=User(id=2,username='guest',display_name='Guest',role='guest',password_hash='unused')
        self.db.add_all([self.admin,self.guest,Workspace(id=3,name='formal'),Workspace(id=19,name='guest')]);self.db.flush()
        self.db.add_all([Membership(user_id=2,workspace_id=19),Datasource(id=1,workspace_id=3,name='source',host='db',port=5432,database='synthetic',username='reader',secret='unused',schemas=['dwd']),Datasource(id=2,workspace_id=19,name='clone',host='db',port=5432,database='clone',username='reader',secret='unused',schemas=['dwd'])])
        if GuestSnapshot:
            self.db.add(GuestSnapshot(id=1,workspace_id=19,user_id=2,content={},content_hash='fixture'))
        self.db.flush()
        for aid,wid,sid,name,cols in [(1,3,1,'orders',{'id':'int','price':'numeric'}),(2,3,1,'totals',{'id':'int','amount':'numeric'}),(3,19,2,'orders',{'id':'int','price':'numeric'}),(4,19,2,'totals',{'id':'int','amount':'numeric'})]:
            self.db.add(Asset(id=aid,workspace_id=wid,datasource_id=sid,namespace='dwd',table_name=name,name=name,columns=[{'name':n,'type':t}for n,t in cols.items()],fingerprint='fp'+str(aid),state='active',layer='DWD'))
        self.db.flush()
        for rid,wid,sid,parent,target in [(1,3,1,1,2),(2,19,2,3,4)]:
            query='SELECT id, price*2 AS amount FROM dwd.orders'
            self.db.add(TransformRun(id=rid,workspace_id=wid,datasource_id=sid,target_asset_id=target,run_key='test',kind='warehouse_transform',status='recomputed_match',select_sql=query,evidence={'sql_sha256':hashlib.sha256(query.encode()).hexdigest(),'input_snapshots':[{'asset_id':parent,'fingerprint':'fp'+str(parent)}],'target_fingerprint':'fp'+str(target)},actor='test'))
            self.db.add(TransformEdge(workspace_id=wid,run_id=rid,source_asset_id=parent,target_asset_id=target))
        self.db.commit()
        self.app=FastAPI();self.app.include_router(router)
        self.app.dependency_overrides[identity]=lambda:self.admin
        self.app.dependency_overrides[db_session]=lambda:self.db
        self.client=TestClient(self.app)
        self.guard=patch('app.catalog_lineage.guard_asset');self.guard.start()

    def tearDown(self):
        self.guard.stop();self.client.close();self.db.close();self.engine.dispose()

    def get(self,wid=3,aid=2,**params):
        return self.client.get(f'/api/workspaces/{wid}/assets/{aid}/lineage',params=params)

    def test_formal_and_guest_share_the_contract_without_cross_workspace_nodes(self):
        formal=self.get().json();self.assertEqual(len(formal['column_edges']),2)
        self.app.dependency_overrides[identity]=lambda:self.guest
        guest=self.get(19,4).json()
        self.assertEqual({n['asset_id']for n in guest['nodes']},{3,4})
        self.assertEqual(len(guest['column_edges']),2)
        self.assertEqual(self.get(19,2).status_code,404)
        self.assertEqual(self.get(3,2).status_code,403)

    def test_unreadable_input_hides_sql_and_field_names(self):
        self.guard.stop()
        def denied(db,wid,user,a,**kw):
            if a.id==1:raise HTTPException(403,'private')
        with patch('app.catalog_lineage.guard_asset',side_effect=denied):
            data=self.get().json()
        self.guard.start()
        self.assertEqual(data['records'],[]);self.assertEqual(data['column_edges'],[])
        self.assertNotIn('price',str(data))

    def test_structure_drift_retains_historical_table_evidence_but_no_field_arrows(self):
        self.db.get(Asset,1).fingerprint='changed';self.db.commit()
        data=self.get().json();self.assertEqual(data['records'][0]['status'],'SOURCE_CHANGED')
        self.assertEqual(data['column_edges'],[]);self.assertEqual(len(data['table_edges']),1)

    def test_bad_sql_hash_is_not_displayed_as_evidence(self):
        run=self.db.get(TransformRun,1);run.select_sql='SELECT id,price*3 AS amount FROM dwd.orders';self.db.commit()
        data=self.get().json();self.assertEqual(data['records'],[]);self.assertTrue(data['warnings'])

    def test_successful_query_adds_result_field_lineage_and_failed_query_does_not(self):
        sql='SELECT SUM(price) AS total FROM dwd.orders'
        for qid,status in [(1,'success'),(2,'failed')]:
            self.db.add(QueryRun(id=qid,workspace_id=3,datasource_id=1,actor='test',sql=sql,status=status))
            self.db.flush();self.db.add(Lineage(workspace_id=3,query_id=qid,asset_id=1,source='dwd.orders',target='query:'+str(qid),evidence={'sql_sha256':hashlib.sha256(sql.encode()).hexdigest(),'metadata_fingerprint':'fp1'}))
        self.db.commit();data=self.get(aid=1).json()
        self.assertIn('query:1',{n['id']for n in data['nodes']});self.assertNotIn('query:2',{n['id']for n in data['nodes']})
        edge=next(e for e in data['column_edges']if e['target']=='query:1')
        self.assertEqual((edge['source_column'],edge['target_column']),('price','total'))
        self.assertFalse(any(n['kind']=='query' for n in self.get(aid=1,include_queries=False).json()['nodes']))

    def test_depth_is_bounded_and_static_declared_source_stays_declared(self):
        run=self.db.get(TransformRun,1);run.kind='synthetic_generation';run.status='declared_only';self.db.commit()
        data=self.get().json();self.assertEqual(data['records'][0]['status'],'declared_only')
        self.assertEqual(self.get(depth=4).status_code,422)

    def test_committed_pipeline_sql_and_cross_source_sync_preserve_exact_mapping(self):
        # The target connection may differ for sync; physical SQL tasks stay scoped.
        self.db.add(Datasource(id=5,workspace_id=3,name='target',host='db',port=5432,
            database='target',username='reader',secret='unused',schemas=['dwd']))
        self.db.add(Asset(id=5,workspace_id=3,datasource_id=5,namespace='dwd',table_name='synced',
            name='synced',columns=[{'name':'key','type':'int'},{'name':'value','type':'numeric'}],
            fingerprint='fp5',state='active',layer='DWD'))
        self.db.add(Definition(id=1,workspace_id=3,kind='sync',name='fixture',config={},actor_id=1))
        self.db.flush()
        for run_id,kind,target,config,committed in [
            ('sql-ok','sql',2,{'sql':'SELECT id,price*3 AS amount FROM dwd.orders'},True),
            ('sync-ok','sync',5,{'source_asset_id':1,'mapping':{'id':'key','price':'value'}},True),
            ('sync-uncommitted','sync',5,{'source_asset_id':1,'mapping':{'id':'key'}},False)]:
            self.db.add(PipelineRun(id=run_id,workspace_id=3,definition_id=1,revision=1,actor_id=1,
                batch_key=run_id,request_key=run_id,business_date='2025-06-01',status='SUCCEEDED',
                snapshot={'kind':kind,'config':{'target_asset_id':target,**config}},
                evidence={'committed':committed,'inputs':[{'asset_id':1,'fingerprint':'fp1'}],
                          'target_fingerprint':'fp'+str(target)}))
        self.db.commit()
        sql_data=self.get(aid=2).json()
        self.assertTrue(any(e['record_id']=='transform:pipeline-sql-ok' and e['target_column']=='amount'
                            for e in sql_data['column_edges']))
        sync=self.get(aid=5).json()
        self.assertEqual({(e['source_column'],e['target_column']) for e in sync['column_edges']},
                         {('id','key'),('price','value')})
        self.assertEqual(sync['records'][0]['status'],'committed')
        self.assertNotIn('sync-uncommitted',str(sync))

    def test_depth_expands_a_chain_without_including_unrequested_levels(self):
        self.db.add(Asset(id=5,workspace_id=3,datasource_id=1,namespace='dwd',table_name='summary',
            name='summary',columns=[{'name':'id','type':'int'},{'name':'amount','type':'numeric'}],
            fingerprint='fp5',state='active',layer='DWS'))
        self.db.flush()
        sql='SELECT id,amount FROM dwd.totals'
        self.db.add(TransformRun(id=3,workspace_id=3,datasource_id=1,target_asset_id=5,
            run_key='second',kind='warehouse_transform',status='recomputed_match',select_sql=sql,
            evidence={'input_snapshots':[{'asset_id':2,'fingerprint':'fp2'}],
                      'target_fingerprint':'fp5'},actor='test'))
        self.db.add(TransformEdge(workspace_id=3,run_id=3,source_asset_id=2,target_asset_id=5))
        self.db.commit()
        self.assertEqual({n['asset_id'] for n in self.get(aid=1,depth=1).json()['nodes']},{1,2})
        data=self.get(aid=1,depth=2).json()
        self.assertEqual({n['asset_id'] for n in data['nodes']},{1,2,5})
        self.assertEqual(len(data['column_edges']),4)


if __name__=='__main__':main()
