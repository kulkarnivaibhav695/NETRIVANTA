#!/usr/bin/env python3
"""NETRIVANTA volunteer measurement agent. Run with --once or --days 7 --interval-minutes 360."""
import argparse, json, os, platform, socket, statistics, time
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import requests

CONFIG = Path(__file__).with_name("config.json")

def load_config(args):
    if args.api_url and args.api_key:
        return {"api_url":args.api_url,"api_key":args.api_key,"target_host":args.target_host,"max_test_bytes":args.max_test_bytes}
    if not CONFIG.exists(): raise SystemExit("Missing agent/config.json. Copy config.example.json and fill in your API URL and API key.")
    cfg=json.loads(CONFIG.read_text())
    for k,v in {"api_url":args.api_url,"api_key":args.api_key,"target_host":args.target_host,"max_test_bytes":args.max_test_bytes}.items():
        if v is not None: cfg[k]=v
    return cfg

def jitter(samples):
    return statistics.mean(abs(b-a) for a,b in zip(samples,samples[1:])) if len(samples)>1 else 0.0

def interface_hint():
    try:
        import subprocess
        if platform.system()=="Windows":
            out=subprocess.check_output(["netsh","wlan","show","interfaces"],text=True,stderr=subprocess.DEVNULL)
            for line in out.splitlines():
                if "Name" in line and ":" in line: return line.split(":",1)[1].strip()
        else:
            out=subprocess.check_output(["ip","route","get","1.1.1.1"],text=True,stderr=subprocess.DEVNULL)
            parts=out.split()
            if "dev" in parts: return parts[parts.index("dev")+1]
    except Exception: pass
    return "unknown"

def wifi_signal_dbm(interface):
    """Best-effort Wi-Fi RSSI in dBm; return None when unsupported/unavailable."""
    if not interface or interface == "unknown": return None
    try:
        import subprocess, re
        system=platform.system()
        if system == "Linux":
            out=subprocess.check_output(["iw","dev",interface,"link"],text=True,stderr=subprocess.DEVNULL,timeout=3)
            match=re.search(r"signal:\s*(-?\d+)\s*dBm",out)
            return float(match.group(1)) if match else None
        if system == "Darwin":
            candidates=["/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"]
            for command in candidates:
                out=subprocess.check_output([command,"-I"],text=True,stderr=subprocess.DEVNULL,timeout=3)
                match=re.search(r"agrCtlRSSI:\s*(-?\d+)",out)
                if match: return float(match.group(1))
    except Exception: pass
    return None

def run_test(cfg):
    base=cfg["api_url"].rstrip("/"); target=cfg.get("target_host","example.com"); session=requests.Session()
    interface=interface_hint()
    record={"measured_at":datetime.now(timezone.utc).isoformat(),"target_host":target,"connection_interface":interface,"test_status":"complete"}
    # DNS resolution delay, timed separately from HTTP request latency.
    try:
        start=time.perf_counter(); socket.getaddrinfo(target,None); record["dns_delay_ms"]=(time.perf_counter()-start)*1000
    except Exception as exc: record["dns_delay_ms"]=None; record["error_detail"]=f"DNS: {exc}"
    # Five controlled endpoint requests. Failed probes count as packet loss.
    samples=[]; failures=0
    for _ in range(5):
        try:
            start=time.perf_counter(); r=session.get(base+"/api/probe/health",timeout=8); r.raise_for_status()
            samples.append((time.perf_counter()-start)*1000)
        except Exception: failures+=1
    record["latency_ms"]=statistics.mean(samples) if samples else None
    record["packet_loss_pct"]=failures/5*100
    record["jitter_ms"]=jitter(samples) if samples else None
    # Limited throughput probes, capped at 1 MiB per direction by the server.
    size=min(int(cfg.get("max_test_bytes",524288)),1048576)
    try:
        start=time.perf_counter(); r=session.get(base+f"/api/probe/download?bytes={size}",timeout=20); r.raise_for_status(); elapsed=max(time.perf_counter()-start,0.001)
        record["download_mbps"]=(len(r.content)*8/1_000_000)/elapsed
    except Exception as exc: record["download_mbps"]=None; record["test_status"]="partial"; record["error_detail"]=(record.get("error_detail","")+f" Download: {exc}")[:500]
    try:
        payload=b"U"*size; start=time.perf_counter(); r=session.post(base+"/api/probe/upload",data=payload,headers={"Content-Type":"application/octet-stream"},timeout=20); r.raise_for_status(); elapsed=max(time.perf_counter()-start,0.001)
        record["upload_mbps"]=(size*8/1_000_000)/elapsed
    except Exception as exc: record["upload_mbps"]=None; record["test_status"]="partial"; record["error_detail"]=(record.get("error_detail","")+f" Upload: {exc}")[:500]
    # Loaded jitter: measure controlled endpoint latency while a capped download is active.
    loaded=[]
    def download_load():
        try: requests.get(base+f"/api/probe/download?bytes={size}",timeout=20).content
        except Exception: pass
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(download_load)
            for _ in range(5):
                try:
                    start=time.perf_counter(); session.get(base+"/api/probe/health",timeout=8).raise_for_status(); loaded.append((time.perf_counter()-start)*1000)
                except Exception: pass
            future.result(timeout=25)
        record["loaded_jitter_ms"]=jitter(loaded) if loaded else None
    except Exception: record["loaded_jitter_ms"]=None
    record["wifi_signal_dbm"]=wifi_signal_dbm(interface)
    response=session.post(base+"/api/measurements",json=record,headers={"X-API-Key":cfg["api_key"]},timeout=15)
    response.raise_for_status()
    print(json.dumps({"stored":response.json(),"measurement":record},indent=2))

def main():
    p=argparse.ArgumentParser(description="NETRIVANTA scheduled volunteer measurement agent")
    p.add_argument("--api-url"); p.add_argument("--api-key"); p.add_argument("--target-host"); p.add_argument("--max-test-bytes",type=int)
    p.add_argument("--once",action="store_true"); p.add_argument("--days",type=int,default=7); p.add_argument("--interval-minutes",type=int,default=360)
    args=p.parse_args(); cfg=load_config(args)
    if args.once: run_test(cfg); return
    end=datetime.now(timezone.utc)+timedelta(days=max(1,args.days))
    while datetime.now(timezone.utc)<end:
        try: run_test(cfg)
        except Exception as exc: print(f"Measurement failed: {exc}")
        time.sleep(max(1,args.interval_minutes)*60)

if __name__=="__main__": main()
