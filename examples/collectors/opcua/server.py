#!/usr/bin/env python3
"""A local OPC UA test server with the information model the reference collector reads. Not a real machine.

  python examples/collectors/opcua/server.py [--port 4840] [--block-hz 5] [--start-block 0]

Namespace urn:morletdb:example-machine, under Objects:

  Machine (Object)
    Speed   Double, rpm   scalar process tag, changes irregularly; source timestamp and status (Good/Uncertain)
    Load    Double, %     scalar process tag, as Speed
    State   String        operating state: Running, Setup or Stopped
    Vibration (Object)    one regularly sampled waveform, delivered in numbered blocks
      SampleRate  Double  samples per second (5120)
      BlockSize   UInt32  samples per block (1024)
      Unit        String  unit of the samples ("g")
      LatestBlock UInt64  index of the newest block; block i holds samples i*BlockSize .. (i+1)*BlockSize-1
      OldestBlock UInt64  index of the oldest block still held (a ring buffer of the last 64 blocks)
      ReadBlock(index UInt64) -> (samples Double[], start_time DateTime)
                          one block between OldestBlock and LatestBlock; other indices are an error

Block samples are a deterministic function of the block index (examples/collectors/opcua/signal_model.py), so a test
can check every stored sample. Block indices continue from wall-clock time across restarts (block i starts at
EPOCH + i * BlockSize / SampleRate), as a device with a monotonic sample counter would.
"""
import argparse
import asyncio
import random
from datetime import datetime, timezone

from asyncua import Server, ua
from asyncua.common.methods import uamethod

import signal_model as sm

RING = 64


async def main(port, start_block):
    server = Server()
    await server.init()
    server.set_endpoint(f"opc.tcp://127.0.0.1:{port}/morletdb/")
    server.set_server_name("MorletDB example machine (test server)")
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    ns = await server.register_namespace(sm.NAMESPACE)
    objects = server.nodes.objects
    machine = await objects.add_object(ua.NodeId("Machine", ns), ua.QualifiedName("Machine", ns))
    speed = await machine.add_variable(ua.NodeId("Machine.Speed", ns), ua.QualifiedName("Speed", ns), 1500.0)
    load = await machine.add_variable(ua.NodeId("Machine.Load", ns), ua.QualifiedName("Load", ns), 40.0)
    state = await machine.add_variable(ua.NodeId("Machine.State", ns), ua.QualifiedName("State", ns), "Running")
    vib = await machine.add_object(ua.NodeId("Machine.Vibration", ns), ua.QualifiedName("Vibration", ns))
    await vib.add_variable(ua.NodeId("Machine.Vibration.SampleRate", ns), ua.QualifiedName("SampleRate", ns), sm.RATE)
    await vib.add_variable(ua.NodeId("Machine.Vibration.BlockSize", ns), ua.QualifiedName("BlockSize", ns),
                           ua.Variant(sm.BLOCK, ua.VariantType.UInt32))
    await vib.add_variable(ua.NodeId("Machine.Vibration.Unit", ns), ua.QualifiedName("Unit", ns), sm.UNIT)
    latest = await vib.add_variable(ua.NodeId("Machine.Vibration.LatestBlock", ns), ua.QualifiedName("LatestBlock", ns),
                                    ua.Variant(0, ua.VariantType.UInt64))
    oldest = await vib.add_variable(ua.NodeId("Machine.Vibration.OldestBlock", ns), ua.QualifiedName("OldestBlock", ns),
                                    ua.Variant(0, ua.VariantType.UInt64))
    current = {"latest": -1}

    @uamethod
    def read_block(parent, index):
        index = int(index)
        if index > current["latest"] or index <= current["latest"] - RING:
            raise ua.UaStatusCodeError(ua.StatusCodes.BadOutOfRange)
        return sm.block(index).tolist(), sm.block_start(index)

    await vib.add_method(ua.NodeId("Machine.Vibration.ReadBlock", ns), ua.QualifiedName("ReadBlock", ns), read_block,
                         [ua.VariantType.UInt64], [ua.VariantType.Double, ua.VariantType.DateTime])
    rng = random.Random(7)
    async with server:
        print(f"OPC UA test server on opc.tcp://127.0.0.1:{port}/morletdb/ (no security; local tests only)", flush=True)
        next_tag = 0.0
        while True:
            now = datetime.now(timezone.utc)
            idx = max(start_block, sm.block_at(now) - 1)          # the newest block that is complete
            if idx != current["latest"]:
                current["latest"] = idx
                await server.write_attribute_value(oldest.nodeid, ua.DataValue(ua.Variant(max(start_block, idx - RING + 1), ua.VariantType.UInt64)))
                await server.write_attribute_value(latest.nodeid, ua.DataValue(ua.Variant(idx, ua.VariantType.UInt64),
                                                      SourceTimestamp=sm.block_start(idx)))
            if now.timestamp() >= next_tag:                         # irregular tag changes, every 0.3 to 1.5 s
                status = ua.StatusCode(ua.StatusCodes.Uncertain if rng.random() < 0.1 else ua.StatusCodes.Good)
                await server.write_attribute_value(speed.nodeid, ua.DataValue(ua.Variant(1500 + rng.uniform(-20, 20)), StatusCode_=status,
                                                     SourceTimestamp=now))
                await server.write_attribute_value(load.nodeid, ua.DataValue(ua.Variant(40 + rng.uniform(-5, 5)), SourceTimestamp=now))
                if rng.random() < 0.15:
                    await server.write_attribute_value(state.nodeid, ua.DataValue(ua.Variant(rng.choice(["Running", "Setup", "Running"])),
                                                         SourceTimestamp=now))
                next_tag = now.timestamp() + rng.uniform(0.3, 1.5)
            await asyncio.sleep(0.05)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=4840)
    p.add_argument("--start-block", type=int, default=0, help="do not publish blocks below this index")
    a = p.parse_args()
    asyncio.run(main(a.port, a.start_block))
