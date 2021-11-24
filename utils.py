import asyncio
import shlex
from typing import List, Dict, Sequence

def load_yaml(stream):
    import yaml
    return yaml.load(stream, Loader=yaml.SafeLoader)

def load_inventory(deployment_name):
    import subprocess
    inventory_yaml = subprocess.run(['bin/ansible-inventory', deployment_name, '--list', '--yaml'], check=True, capture_output=True).stdout
    return load_yaml(inventory_yaml)["all"]["children"]

async def run(command: Sequence[str], capture_output=False):
    try:
        print(command)
        stdout_pipe = asyncio.subprocess.PIPE if capture_output else None
        proc = await asyncio.create_subprocess_exec(*command, stdout=stdout_pipe);
        stdout, _ = await proc.communicate()
        return stdout
    except asyncio.exceptions.CancelledError:
        proc.terminate()

class Deployment:
    def __init__(self, name):
        self.name = name
        self.inventory = load_inventory(name)
        self.server_hosts = self.inventory["server"]["hosts"]
        self.client_hosts = self.inventory["client"]["hosts"]
        self.monitoring_host = next(iter(self.inventory["prometheus"]["hosts"]))

    async def ssh(self, host, command: str, capture_output=False):
        return await run(["bin/ssh", self.name, host, command], capture_output=capture_output);

    async def pssh(self, hosts: Sequence[str], command: str):
        await asyncio.gather(*[self.ssh(host, command) for host in hosts])

    async def rsync(self, src, dest, *options):
        await run(["rsync", *options, "--mkpath", "-r", "-e", f"bin/ssh {self.name}", src, dest])

    async def collect(self, hosts: Sequence[str], src, dest_dir):
        await asyncio.gather(*[self.rsync(f"{host}:{src}", f"{dest_dir}/{host}/") for host in hosts])

    async def cs(self, *, op, pop, rate, server_hosts: Sequence[str] = None,  client_hosts: Sequence[str] = None):
        server_hosts = server_hosts or self.server_hosts
        client_hosts = client_hosts or self.client_hosts
        node = "-node {}".format(','.join(self.server_hosts[server]["private_ip"] for server in server_hosts))
        rate = "-rate {}".format(rate)
        pop = "-pop {}".format(shlex.quote(pop))
        mode = "-mode native cql3 protocolVersion=4 maxPending=4096"
        log = "-log hdrfile=log.hdr"
        command = f"cassandra-stress {op} {pop} {node} {rate} {log} {mode}"
        await self.pssh(client_hosts, command)

    async def populate(self, *, num_rows, replication_factor, server_hosts: Sequence[str] = None, client_hosts: Sequence[str] = None):
        server_hosts = server_hosts or self.server_hosts
        client_hosts = client_hosts or self.client_hosts
        n_loaders = len(client_hosts)
        n_per_loader = num_rows // n_loaders
        ranges = [(n_per_loader*i+1, n_per_loader*(i+1)) for i in range(n_loaders)]

        node = "-node {}".format(','.join(self.server_hosts[server]["private_ip"] for server in server_hosts))
        rate = "-rate threads=200"
        mode = "-mode native cql3 protocolVersion=4"
        schema = f"-schema replication(strategy=SimpleStrategy,replication_factor={replication_factor})"

        (server_0_name, server_0_vars) = next(iter(self.server_hosts.items()))
        await self.ssh(server_0_name, f'cqlsh -e "DROP KEYSPACE IF EXISTS keyspace1;" {server_0_vars["private_ip"]}')
        
        await asyncio.gather(*[
            self.ssh(host, f'cassandra-stress write cl=ONE n={range_end-range_start+1} -pop seq={range_start}..{range_end} {node} {rate} {mode}')
            for (host, (range_start, range_end)) in zip(self.client_hosts, ranges)
        ])

    async def download_metrics(self, dest_dir):
        import json
        response = (await self.ssh(self.monitoring_host, "curl --silent -XPOST http://localhost:9090/api/v1/admin/tsdb/snapshot", capture_output=True))
        snapshot_name = json.loads(response.decode("utf-8"))["data"]["name"]
        await self.rsync(f'{self.monitoring_host}:data/snapshots/{snapshot_name}/', f"{dest_dir}/data")

    async def clean_metrics(self, dest_dir):
        await self.ssh(self.monitoring_host, """curl --silent -X POST -g 'http://localhost:9090/api/v1/admin/tsdb/delete_series?match[]={__name__=~".+"}'""")
