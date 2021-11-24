from utils import Deployment, load_yaml
import asyncio
import sys
import shlex
from datetime import datetime

async def main(deployment_name):
    deployment = Deployment(deployment_name)
    cs_default_row_size_bytes = 210 * 1024 * 1024 * 1024 / 720_000_000

    await deployment.populate(num_rows=10000000, replication_factor=3)
    await deployment.cs(
        op = "read cl=QUORUM duration=1m",
        pop = "dist=UNIFORM(1..10000000)",
        rate = "threads=200",
    );

    datetime_now_string = datetime.now().replace(microsecond=0).isoformat().replace(":", "-")
    trial_dir = "trials/{}_{}".format(deployment_name, datetime_now_string)

    await deployment.download_metrics(trial_dir)
    await deployment.collect(deployment.client_hosts, "log.hdr", trial_dir)

if len(sys.argv) != 2:
    raise Exception("Usage: ./benchmark.py [DEPLOYMENT_NAME]")
asyncio.run(main(sys.argv[1])
