from utils import Deployment, load_yaml
from hdr import process_hdr_file_set
import asyncio
import os, sys
from datetime import datetime

async def main(deployment_name, config_yaml):
    deployment = Deployment(deployment_name)

    with open(config_yaml, 'r') as config_file:
        config = load_yaml(config_file)

    CS_DEFAULT_ROW_SIZE = 210 * 1024 * 1024 * 1024 / 720_000_000

    rate_fractions = config.get('rate_fractions', [0.5])
    duration = config.get('phase_duration', 300)
    warmup_seconds = config.get('warmup_seconds', 15)
    cooldown_seconds = config.get('cooldown_seconds', 15)
    java = config.get('java_path', 'java')
    cl = config.get('consistency_level', 'QUORUM')
    cs = config.get('cs_path', 'cassandra-stress')
    replication_factor = config.get('replication_factor', 3)

    num_rows = int(config['target_dataset_size_gb'] * 1024 * 1024 * 1024 / CS_DEFAULT_ROW_SIZE)

    datetime_now_string = datetime.now().replace(microsecond=0).isoformat().replace(":", "-")
    trial_dir = "trials/{}/{}".format(deployment_name, datetime_now_string)

    await deployment.collect(deployment.server_hosts, "/etc/scylla", trial_dir)
    await deployment.collect(deployment.server_hosts, "/etc/scylla.d", trial_dir)

    await deployment.populate(num_rows=num_rows, replication_factor=replication_factor)
    await deployment.quiesce()

    for WRITE_COUNT, READ_COUNT in [(0, 1), (1, 0), (1, 1)]:
        for rate_fraction in [1.0] + rate_fractions:
            if rate_fraction == 1.0:
                rate = f"threads=500"
            else:
                rate = f"threads=200 fixed={int(rate_fraction * MAX_RATE / len(deployment.client_hosts))}/s"

            await deployment.cs(
                op = f"mixed ratio\\(write={WRITE_COUNT},read={READ_COUNT}\\) duration={duration}s cl={cl}",
                pop = f"dist=UNIFORM(1..{num_rows})",
                rate = rate,
                cs = cs,
            )

            stat_dir = os.path.join(trial_dir, f"W{WRITE_COUNT}-R{READ_COUNT}", f"cassandra-stress-{rate_fraction:.2f}")
            await deployment.collect(deployment.client_hosts, "log.hdr", stat_dir)
            summary = await process_hdr_file_set(stat_dir, "log", java=java, time_start=warmup_seconds, time_end=duration-cooldown_seconds)

            if rate_fraction == 1.0:
                MAX_RATE = 0
                if WRITE_COUNT > 0:
                    MAX_RATE += summary['WRITE-st'].throughput_per_second
                if READ_COUNT > 0:
                    MAX_RATE += summary['READ-st'].throughput_per_second

            if WRITE_COUNT > 0:
                await deployment.quiesce()

    await deployment.download_metrics(trial_dir)

if len(sys.argv) != 3:
    raise Exception("Usage: ./benchmark.py DEPLOYMENT_NAME CONFIG")
asyncio.run(main(sys.argv[1], sys.argv[2]))
