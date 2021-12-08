import asyncio
import shlex
import os
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
        raise

async def clean_cancel(task):
    try:
        task.cancel()
        await task
    except asyncio.CancelledError:
        pass

class Deployment:
    def __init__(self, name):
        self.name = name
        self.inventory = load_inventory(name)
        self.server_hosts = self.inventory.get("server", {}).get("hosts", {})
        self.client_hosts = self.inventory.get("client", {}).get("hosts", {})
        try:
            self.monitoring_host = next(iter(self.inventory["monitoring"]["hosts"]))
        except StopIteration:
            self.monitoring_host = None

    async def ssh(self, host, command: str, capture_output=False):
        # -t -t allows terminates the remote process when the ssh connection is terminated (which is good,
        # because it prevents leftovers, but breaks the terminal output somewhat.
        #return await run(["bin/ssh", self.name, "-t", "-t", host, command], capture_output=capture_output);
        return await run(["bin/ssh", self.name, host, command], capture_output=capture_output);

    async def pssh(self, hosts: Sequence[str], command: str):
        await asyncio.gather(*[self.ssh(host, command) for host in hosts])

    async def rsync(self, src, dest, *options):
        #os.makedirs(os.path.dirname(dest), exist_ok=True)
        await run(["rsync", *options, "-r", "-e", f"bin/ssh {self.name}", src, dest])

    async def collect(self, hosts: Sequence[str], src, dest_dir):
        await asyncio.gather(*[self.rsync(f"{host}:{src}", f"{dest_dir}/{host}/", "--mkpath") for host in hosts])

    async def cs(self, /, op, pop, rate, server_hosts: Sequence[str] = None, client_hosts: Sequence[str] = None, cs = "cassandra-stress"):
        server_hosts = server_hosts or self.server_hosts
        client_hosts = client_hosts or self.client_hosts
        await self.pssh(client_hosts, "pkill -9'' -f cassandra-stress")
        node = "-node {}".format(','.join(self.server_hosts[server]["private_ip"] for server in server_hosts))
        rate = "-rate {}".format(rate)
        pop = "-pop {}".format(shlex.quote(pop))
        mode = "-mode native cql3 protocolVersion=4 maxPending=4096"
        log = "-log hdrfile=log.hdr"
        command = f"{cs} {op} {pop} {node} {rate} {log} {mode}"
        await self.pssh(client_hosts, command)

    async def populate(self, /, num_rows, replication_factor, server_hosts: Sequence[str] = None, client_hosts: Sequence[str] = None, cs = "cassandra-stress"):
        server_hosts = server_hosts or self.server_hosts
        client_hosts = client_hosts or self.client_hosts
        n_loaders = len(client_hosts)
        n_per_loader = num_rows // n_loaders
        ranges = [(n_per_loader*i+1, n_per_loader*(i+1)) for i in range(n_loaders)]
        ranges[-1] = (ranges[-1][0], num_rows)

        node = "-node {}".format(','.join(self.server_hosts[server]["private_ip"] for server in server_hosts))
        rate = "-rate threads=500"
        mode = "-mode native cql3 protocolVersion=4 maxPending=4096"
        schema = "-schema {}".format(shlex.quote(f"replication(strategy=SimpleStrategy,replication_factor={replication_factor})"))

        (server_0_name, server_0_vars) = next(iter(self.server_hosts.items()))
        await self.ssh(server_0_name, f'cqlsh -e "DROP KEYSPACE IF EXISTS keyspace1;" {server_0_vars["private_ip"]}')
        
        # Create schema.
        await self.ssh(next(iter(self.client_hosts)), f'{cs} write cl=ALL n=1 -pop seq=1..1 {schema} {node} {rate} {mode}')

        await self.ssh(server_0_name, f'cqlsh -e "ALTER KEYSPACE keyspace1 WITH DURABLE_WRITES = false;" {server_0_vars["private_ip"]}')

        await asyncio.gather(*[
            self.ssh(host, f'{cs} write cl=ONE n={range_end-range_start+1} -pop seq={range_start}..{range_end} {node} {rate} {mode}')
            for (host, (range_start, range_end)) in zip(self.client_hosts, ranges)
        ])

        await self.ssh(server_0_name, f'cqlsh -e "ALTER KEYSPACE keyspace1 WITH DURABLE_WRITES = true;" {server_0_vars["private_ip"]}')

    async def download_metrics(self, dest_dir):
        import json
        response = (await self.ssh(self.monitoring_host, "curl --silent -XPOST http://localhost:9090/api/v1/admin/tsdb/snapshot", capture_output=True))
        snapshot_name = json.loads(response.decode("utf-8"))["data"]["name"]
        await self.rsync(f'{self.monitoring_host}:data/snapshots/{snapshot_name}/', f"{dest_dir}/data")

    async def clean_metrics(self):
        await self.ssh(self.monitoring_host, """curl --silent -X POST -g 'http://localhost:9090/api/v1/admin/tsdb/delete_series?match[]={__name__=~".%2B"}'""")

    async def query_prometheus(self, query_string):
        import json
        url = f"http://localhost:9090/api/v1/query?query={query_string}"
        response = await self.ssh(self.monitoring_host, f"curl --silent {shlex.quote(url)}", capture_output=True)
        return json.loads(response)

    async def wait_for_compaction_end(self):
        query_string='sum(scylla_compaction_manager_compactions\{\})'
        poll_period=20
        print(f'Waiting for all compactions to end. Checking every {poll_period}s:')
        while True:
            response = await self.query_prometheus(query_string)
            print(response)
            ongoing_compactions = int(response["data"]["result"][0]["value"][1])
            print(f'Number of ongoing compactions: {ongoing_compactions}')
            if ongoing_compactions == 0:
                break
            await asyncio.sleep(poll_period)

    async def quiesce(self):
        await self.pssh(self.server_hosts, "nodetool flush")
        await self.wait_for_compaction_end()

    async def major_compaction(self):
        await self.pssh(self.server_hosts, "nodetool compact")

    def get_num_cpus(self):
        return next(iter(self.server_hosts.values()))['num_cpus']
    def get_server_cpuset(self):
        return next(iter(self.server_hosts.values())).get('cpuset', None)
    def get_server_irq_mask(self):
        return next(iter(self.server_hosts.values())).get('irq_mask', None)

    async def tune_scylla(self, /, irq_mask = None, cpuset = None):
        if cpuset is not None:
            await self.pssh(self.server_hosts, f'sudo scylla_cpuset_setup --cpuset {cpuset}')
        if irq_mask is not None:
            await self.pssh(self.server_hosts, f'/opt/scylladb/scripts/perftune.py --tune net --nic eth0 --tune system --tune-clock --irq-cpu-mask {irq_mask} --dump-options-file | sudo tee /etc/scylla.d/perftune.yaml')
