#!/usr/bin/env python3
"""Long-recording sweep for synchronous durable ingestion and exact search."""
import argparse
import itertools
import json
import math
import os
from pathlib import Path
import platform
import resource
import sys
import threading
import time
import uuid

import psutil
import pyarrow as pa
import pyarrow.parquet as pq
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from run import measure, summary
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"/"ingest"))
from parquet_import import Config, import_files


def sample(index, channel):
    noise=((index*1664525+channel*1013904223)&0xffff)/65535.-.5
    return math.sin(math.tau*(8+channel)*index/512.)+.08*noise


def generate(path,length,channels):
    writer=None
    rows=[]
    try:
        for channel in range(channels):
            for start in range(0,length,2048):
                rows.append({"recording_id":f"ch-{channel:04}","start_sample":start,
                             "sample_rate_hz":4096.,"channel":"vibration","unit":"g",
                             "metadata":json.dumps({"cohort":"base","channel_index":channel}),
                             "samples":[sample(i,channel) for i in range(start,min(start+2048,length))]})
                if len(rows)==16:
                    table=pa.Table.from_pylist(rows)
                    if writer is None: writer=pq.ParquetWriter(path,table.schema,compression="zstd")
                    writer.write_table(table)
                    rows=[]
        if rows:
            table=pa.Table.from_pylist(rows)
            if writer is None: writer=pq.ParquetWriter(path,table.schema,compression="zstd")
            writer.write_table(table)
    finally:
        if writer is not None: writer.close()


class Monitor:
    """Sampled process metrics: PostgreSQL RSS sums double-count shared pages."""
    def __init__(self,conn,local_postgres):
        self.client=psutil.Process()
        self.server=None
        if local_postgres:
            if conn.info.host not in ("localhost","127.0.0.1","::1",""):
                raise ValueError("local Postgres monitoring requires a local connection")
            backend=psutil.Process(conn.execute("SELECT pg_backend_pid()").fetchone()[0])
            if backend.name()!="postgres" or backend.parent().name()!="postgres":
                raise ValueError("cannot identify local Postgres processes")
            self.server=backend.parent()
        self.last_cpu={}
        self.postgres_cpu=0.
        self.client_peak=0
        self.postgres_peak=0
        self.stop=threading.Event()
        self.thread=threading.Thread(target=self.loop,daemon=True)
        self.sample(initial=True)
        usage=resource.getrusage(resource.RUSAGE_SELF)
        self.client_cpu=usage.ru_utime+usage.ru_stime

    def sample(self,initial=False):
        self.client_peak=max(self.client_peak,self.client.memory_info().rss)
        rss=0
        if self.server is not None:
            for proc in [self.server]+self.server.children(recursive=True):
                try:
                    cpu=proc.cpu_times().user+proc.cpu_times().system
                    previous=self.last_cpu.get(proc.pid,cpu if initial else 0.)
                    self.postgres_cpu+=max(0.,cpu-previous)
                    self.last_cpu[proc.pid]=cpu
                    rss+=proc.memory_info().rss
                except (psutil.NoSuchProcess,psutil.AccessDenied):
                    pass
            self.postgres_peak=max(self.postgres_peak,rss)

    def loop(self):
        while not self.stop.wait(.05): self.sample()

    def finish(self):
        self.stop.set();self.thread.join();self.sample()
        usage=resource.getrusage(resource.RUSAGE_SELF)
        return {"client_cpu_s":usage.ru_utime+usage.ru_stime-self.client_cpu,
                "client_peak_rss_bytes_sampled":self.client_peak,
                "postgres_cpu_s_sampled":self.postgres_cpu if self.server else None,
                "postgres_peak_summed_rss_bytes_sampled":self.postgres_peak if self.server else None}


def case(args,path,length,channels,chunk_size,rpc_batch):
    schema="morlet_stream_sweep_"+uuid.uuid4().hex
    collection=f"{schema}.signals"
    config=Config(chunk_size=chunk_size,rpc_chunks=rpc_batch,commit_chunks=args.transaction_chunks)
    config.validate()
    query=[sample(i,0) for i in range(512)]
    with psycopg.connect(args.dsn,autocommit=True) as conn:
        conn.execute("SET statement_timeout='120s'")
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            conn.execute("SELECT morlet_create_stream_collection(%s,'signals')",(schema,))
            wal_start=conn.execute("SELECT pg_current_wal_insert_lsn()").fetchone()[0]
            monitor=Monitor(conn,args.monitor_local_postgres)
            monitor.thread.start()
            try:
                imported=import_files([path],collection,config,dsn=args.dsn)
            finally:
                metrics=monitor.finish()
            wal_bytes=int(conn.execute("SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(),%s::pg_lsn)",(wal_start,)).fetchone()[0])
            relation_names=[collection,collection+"_chunks",collection+"_segments"]
            stored_bytes=sum(conn.execute("SELECT pg_total_relation_size(%s::regclass)",(name,)).fetchone()[0] for name in relation_names)
            per_channel=1+(length-512)//256
            for channel in range(channels):
                status=conn.execute("SELECT received_samples,indexed_segments FROM morlet_stream_status(%s,%s)",(collection,f"ch-{channel:04}")).fetchone()
                assert status==(length,per_channel)
            crossing=max(0,((chunk_size-256)//256)*256)
            if crossing+512<=length:
                actual=conn.execute(sql.SQL("SELECT features FROM {} WHERE recording_id='ch-0000' AND start_sample=%s").format(sql.Identifier(schema,'signals_segments')),(crossing,)).fetchone()[0]
                expected=conn.execute("SELECT morlet_features(morlet_normalize(%s::float8[],'zscore'),4096)",([sample(i,0) for i in range(crossing,crossing+512)],)).fetchone()[0]
                assert actual==expected
            before=conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(schema,'signals_chunks'))).fetchone()[0]
            replay=import_files([path],collection,config,dsn=args.dsn)
            after=conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(schema,'signals_chunks'))).fetchone()[0]
            assert before==after
            search=lambda:conn.execute("SELECT * FROM morlet_search_streams(%s,%s::float8[],4096,'vibration','g',%s::jsonb)",
                                       (collection,query,Jsonb({"cohort":"base","channel_index":0}))).fetchall()
            search_report={"eligible_segments":per_channel}
            if per_channel<=4096:
                assert search()[0][3]==0.
                search_report["alone"]=measure(search,args.repeats)
                ready,start,stop=threading.Event(),threading.Event(),threading.Event()
                errors,commits=[],[]
                def writer():
                    try:
                        with psycopg.connect(args.dsn,autocommit=True) as other:
                            other.execute("SET statement_timeout='60s'")
                            other.execute("SELECT morlet_start_recording(%s,'writer',4096,'vibration','g',%s::jsonb)",(collection,Jsonb({"cohort":"writer"})))
                            ready.set();start.wait()
                            offset=0
                            while not stop.is_set():
                                other.execute("SELECT morlet_append_chunk(%s,'writer',%s,%s::float8[])",(collection,offset,[0.]*1024))
                                commits.append(time.perf_counter());offset+=1024
                    except Exception as exc:
                        errors.append(type(exc).__name__);ready.set()
                thread=threading.Thread(target=writer)
                thread.start();ready.wait(30)
                if not ready.is_set():
                    stop.set();start.set();thread.join();raise RuntimeError("writer startup timed out")
                start.set();mixed_start=time.perf_counter()
                try:
                    search_report["with_writer"]=summary([measure_once(search) for _ in range(args.repeats)])
                    mixed_end=time.perf_counter()
                finally:
                    stop.set();thread.join()
                assert not errors, f"mixed writer failed: {errors}"
                search_report["writer_commits_during_queries"]=sum(mixed_start<=t<=mixed_end for t in commits)
            else:
                search_report["skipped"]="prototype exact-search budget exceeds 4096 segments"
            return {"length_samples_per_channel":length,"channels":channels,"chunk_size":chunk_size,
                    "rpc_batch":rpc_batch,"transaction_chunks":args.transaction_chunks,
                    "total_samples":length*channels,"parquet_sha256":imported["files"][0]["sha256"],
                    "import_s_excluding_hash":imported["elapsed_s"],"import_wall_s":imported["wall_elapsed_s"],
                    "samples_per_s_excluding_hash":length*channels/imported["elapsed_s"],
                    "append_rpc_calls":imported["rpc_calls"],"wal_insert_bytes_instance_delta":wal_bytes,
                    "stored_bytes_including_indexes_toast":stored_bytes,"replay_wall_s":replay["wall_elapsed_s"],
                    "replay_no_duplicates":True,"boundary_features_match":True,"search":search_report,**metrics}
        finally:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def measure_once(fn):
    start=time.perf_counter();fn();return time.perf_counter()-start


def integers(text): return [int(part) for part in text.split(",")]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn",default=os.environ.get("DATABASE_URL",""))
    parser.add_argument("--lengths",type=integers,default=[65536,262144])
    parser.add_argument("--channels",type=integers,default=[1,4])
    parser.add_argument("--chunk-sizes",type=integers,default=[1024,8192])
    parser.add_argument("--rpc-batches",type=integers,default=[1,16])
    parser.add_argument("--transaction-chunks",type=int,default=16)
    parser.add_argument("--repeats",type=int,default=20)
    parser.add_argument("--monitor-local-postgres",action="store_true")
    parser.add_argument("--output",type=Path,default=Path("streaming-output/report.json"))
    args=parser.parse_args()
    matrix=list(itertools.product(args.lengths,args.channels,args.chunk_sizes,args.rpc_batches))
    if not matrix or len(matrix)>64 or args.repeats<5 or any(n<512 for n in args.lengths) or any(not 1<=n<=64 for n in args.channels):
        parser.error("require 1..64 cases, lengths >=512, channels 1..64 and repeats >=5")
    for _,_,chunk,rpc in matrix:
        Config(chunk_size=chunk,rpc_chunks=rpc,commit_chunks=args.transaction_chunks).validate()
    if args.output.exists():parser.error("output already exists")
    args.output.parent.mkdir(parents=True,exist_ok=True)
    data_dir=args.output.parent/(args.output.stem+"-data")
    data_dir.mkdir(exist_ok=False)
    with psycopg.connect(args.dsn,autocommit=True) as conn:
        version=conn.execute("SELECT version(),morlet_extractor_version()").fetchone()
        settings={name:conn.execute(sql.SQL("SHOW {}").format(sql.Identifier(name))).fetchone()[0]
                  for name in ("shared_buffers","work_mem","max_parallel_workers_per_gather","synchronous_commit","fsync")}
    report={"benchmark_version":1,"revision":os.environ.get("GITHUB_SHA","unspecified"),
            "build_profile":os.environ.get("MORLET_BUILD_PROFILE","unspecified"),
            "environment":{"platform":platform.platform(),"cpu_count":os.cpu_count(),"server":version[0],
                           "extractor_version":version[1],"settings":settings,"python":platform.python_version(),
                           "pyarrow":pa.__version__,"psycopg":psycopg.__version__,"psutil":psutil.__version__},
            "limitations":["single-node synthetic sweep, not beyond-RAM or production scale",
                           "one measured import per case, fixed case order and warm OS caches; no causal speedup claim",
                           "CPU/RSS import metrics describe client and optional local server; sampled RSS sums double-count shared pages",
                           "short-lived process CPU can be missed by 50ms sampling; WAL delta includes other server activity",
                           "synchronous feature extraction; no async lag or workload-isolation guarantee",
                           "mixed writer uses 1024-sample commits; queries filter a fixed channel/cohort",
                           "search is skipped if one channel exceeds the prototype's 4096-segment budget"],"cases":[]}
    paths={}
    for length,channels in itertools.product(args.lengths,args.channels):
        path=data_dir/f"signals-{length}-{channels}.parquet"
        generate(path,length,channels);paths[length,channels]=path
    for length,channels,chunk,rpc in matrix:
        report["cases"].append(case(args,paths[length,channels],length,channels,chunk,rpc))
        args.output.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))


if __name__=="__main__":main()
