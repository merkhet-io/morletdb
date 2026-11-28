#!/usr/bin/env python3
"""Disposable end-to-end smoke: seed, Grafana query API, browser viewer drill-down.

Requires psycopg 3.2.10, numpy 2.2.6 and Playwright 1.56.0 + Chromium.
Never connects to an existing database. Every container/network is uniquely named.
"""
import base64
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.request import Request,urlopen
from urllib.parse import parse_qs,urlsplit

import psycopg
from psycopg import sql
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools/timescale/tests'))
import test_integration as native
sys.path.insert(0,str(Path(__file__).parent))
from seed import seed
from make_dashboard import dashboard
sys.path.insert(0,str(ROOT/'tools/timescale'))
from bridge import Bridge, DAY_NS
from cli import tick


def request(base,path,auth,data=None):
    req=Request(base+path,headers={'Authorization':auth,'Content-Type':'application/json'},
                data=None if data is None else json.dumps(data).encode())
    with urlopen(req,timeout=15) as response:
        return json.load(response)


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0))
        return s.getsockname()[1]


def main():
    server=native.Integration
    docker=native.runtime.docker
    name='morlet-ts-ui-'+secrets.token_hex(4)
    network=name+'-net'
    viewer=None
    made_network=False
    made_grafana=False
    server.setUpClass()
    result={}
    try:
        with server.connection('postgres') as c:
            c.execute('CREATE DATABASE morlet_demo')
        with server.connection('morlet_demo') as c:
            reader_password=secrets.token_hex(24)
            viewer_port=free_port()
            viewer_base=f'http://127.0.0.1:{viewer_port}'
            admin_dsn=psycopg.conninfo.make_conninfo(c.info.dsn,password=server.password)
            origin=seed(admin_dsn,reader_password,viewer_base)
            # Rerun the seed: waveform, metrics and telemetry counts must stay fixed.
            seed(admin_dsn,reader_password,viewer_base)
            b=Bridge(c)
            assert b.one('SELECT count(*) AS n FROM morlet_metrics.metrics')['n']==4
            # Exercise the same bounded tick used by the scheduled worker.
            result['tick']=tick(b,10,100,60)
            reader_dsn=psycopg.conninfo.make_conninfo(host='127.0.0.1',port=server.port,
                        dbname='morlet_demo',user='timescale_demo_reader',password=reader_password)
            viewer=subprocess.Popen([sys.executable,str(ROOT/'tools/viewer/viewer.py'),'--port',str(viewer_port)],
                       env=dict(os.environ,DATABASE_URL=reader_dsn),stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            docker('network','create',network)
            made_network=True
            docker('network','connect','--alias','db',network,server.name)
            admin_password=secrets.token_hex(24)
            env=dict(os.environ,GF_SECURITY_ADMIN_PASSWORD=admin_password,MORLET_PG_PASSWORD=reader_password)
            docker('run','-d','--name',name,'--network',network,'-p','127.0.0.1::3000',
                   '-e','GF_SECURITY_ADMIN_PASSWORD','-e','MORLET_PG_PASSWORD',
                   '-e','GF_ANALYTICS_REPORTING_ENABLED=false','-e','GF_ANALYTICS_CHECK_FOR_UPDATES=false',
                   '-v',str(ROOT/'examples/timescale/provisioning')+':/etc/grafana/provisioning:ro',
                   '-v',str(ROOT/'examples/timescale/dashboards')+':/var/lib/grafana/dashboards:ro',
                   'grafana/grafana-oss:11.2.2',env=env)
            made_grafana=True
            port=int(docker('port',name,'3000/tcp').decode().strip().split(':')[-1])
            base=f'http://127.0.0.1:{port}'
            auth='Basic '+base64.b64encode(('admin:'+admin_password).encode()).decode()
            for _ in range(60):
                try:
                    health=request(base,'/api/health',auth)
                    if health['database']=='ok':
                        break
                except Exception:
                    time.sleep(0.5)
            else:
                raise RuntimeError('Grafana not ready')
            result['datasource']=request(base,'/api/datasources/uid/morlet-timescale/health',auth)
            assert result['datasource']['status']=='OK',result['datasource']
            counts={}
            for panel in dashboard['panels']:
                query={**panel['targets'][0],'intervalMs':1000,'maxDataPoints':500}
                answer=request(base,'/api/ds/query',auth,{'queries':[query],'from':'0','to':str(int(time.time()*1000))})
                data=answer['results']['A']
                assert not data.get('error'),data
                frames=data.get('frames',[])
                counts[panel['title']]=sum(len(f['data']['values'][0]) for f in frames if f['data']['values'])
                assert counts[panel['title']]>0, panel['title']
            result['panel_rows']=counts
            link=b.one('SELECT url FROM morlet_metrics.viewer_links ORDER BY evidence_id LIMIT 1')['url']
            params=parse_qs(urlsplit(link).query)
            assert params['recording']==['capture & A'] and params['start']==['0'] and params['end']==['4096']
            with sync_playwright() as p:
                browser=p.chromium.launch()
                context=browser.new_context(extra_http_headers={'Authorization':auth},viewport={'width':1440,'height':2000})
                page=context.new_page()
                # Grafana login session avoids relying on browser Basic-auth navigation.
                response=context.request.post(base+'/login',data={'user':'admin','password':admin_password})
                assert response.ok
                page.goto(base+'/d/morlet-timescale',wait_until='networkidle')
                page.get_by_text('Metric intervals — open original waveform',exact=False).first.wait_for()
                page.get_by_text('Publication progress — pending is not healthy',exact=False).first.wait_for()
                cell=page.locator('a[href^="'+viewer_base+'"]').first
                cell.wait_for()
                clicked=cell.get_attribute('href')
                selected=parse_qs(urlsplit(clicked).query)
                rid=selected['recording'][0]
                start,end=int(selected['start'][0]),int(selected['end'][0])
                evidence=b.one('''SELECT e.evidence_id FROM morlet_metrics.evidence e
                    JOIN morlet_metrics.sources s USING(source_id)
                    WHERE s.recording_id=%s AND e.start_sample=%s AND e.end_sample=%s''',(rid,start,end))
                assert evidence and b.resolve(evidence['evidence_id'])['state']=='raw_available', {
                    'clicked':clicked,'selected':selected,'evidence':evidence,
                    'stored_links':b.all('SELECT * FROM morlet_metrics.viewer_links')}
                with context.expect_page() as opened:
                    cell.click()
                page=opened.value
                page.wait_for_load_state('networkidle')
                page.wait_for_function("rid => document.querySelector('#recs .on')?.getAttribute('data-id') === rid",arg=rid)
                assert page.evaluate('st.view')==[start,end], page.evaluate('st.view')
                # Inspect exact API coordinates as well as the visible page selection.
                response=page.request.get(viewer_base+'/api/overview',params={
                    'collection':'public.signals','recording':rid,'start':start,'end':end})
                assert response.ok, response.text()
                assert response.json()['start']==start and response.json()['end']==end
                result['browser']={'dashboard_loaded':True,'recording':rid,'interval':[start,end],'interval_api_status':response.status}
                browser.close()
            # Retention touches derived metrics only, while the displayed historical
            # summary and waveform evidence remain queryable after refresh floor advances.
            cutoff=(origin//DAY_NS+1)*DAY_NS
            plan=b.retention_plan(cutoff)
            b.apply_retention(plan)
            assert b.one('SELECT count(*) AS n FROM morlet_metrics.metrics')['n']==0
            assert b.one('SELECT count(*) AS n FROM morlet_metrics.minute')['n']==2
            assert b.resolve(1)['state']=='raw_available'
            result['retention']={'metric_rows':0,'aggregate_rows':2,'raw_available':True}
            # Run the interval panel again: explicit expired state is still visible.
            answer=request(base,'/api/ds/query',auth,{'queries':[dashboard['panels'][2]['targets'][0]],'from':'0','to':str(int(time.time()*1000))})
            assert not answer['results']['A'].get('error'),answer
            assert 'metric expired' in json.dumps(answer)
        print(json.dumps(result,indent=2,default=str))
    finally:
        if viewer:
            viewer.terminate()
            viewer.wait(timeout=10)
        if made_grafana:
            docker('rm','-f','-v',name)
        if made_network:
            docker('network','disconnect',network,server.name)
            docker('network','rm',network)
        server.tearDownClass()


if __name__=='__main__':
    main()
