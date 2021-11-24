import os
import asyncio
import glob
import csv
import shlex
import pkg_resources
from collections import namedtuple
from multiprocessing import Pool
from functools import partial

async def process_hdr(dir, java, time_start=None, time_end=None):
    p = HdrLogProcessor(java=java, time_start=time_start, time_end=time_end)
    await p.process_hdr_dir(dir)

async def process_hdr_file_set(dir, name, java, time_start=None, time_end=None):
    p = HdrLogProcessor(java=java, time_start=time_start, time_end=time_end)
    return await p.process_hdr_file_set(dir, name)

class HdrLogProcessor:
    def __init__(self, /, java, time_start, time_end):
        self.dir = dir
        self.java = java
        self.time_start = time_start
        self.time_end = time_end
        self.semaphore = asyncio.Semaphore(2 * len(os.sched_getaffinity(0)))

    async def run(self, *args):
        from utils import run
        async with self.semaphore:
            await run(*args)

    async def process_hdr_file_set(self, dir, name):
        await self.trim_recursively(dir, name)
        await self.merge_recursively(dir, name)
        await self.process_recursively(dir, name)
        return await self.summarize_recursively(dir, name)

    async def process_hdr_dir(self, dir):
        files = glob.iglob(f"{dir}/**/*.hdr", recursive=True)
        names = set(os.path.splitext(os.path.basename(f))[0] for f in files)
        await asyncio.gather(*(self.process_hdr_file_set(dir, name) for name in names if not name.endswith(".trimmed")))

    async def trim(self, file):
        file_no_ext = os.path.splitext(file)[0]
        args = f'union -ifp {file} -of {file_no_ext}.trimmed.hdr'
        if self.time_start is not None:
            args = f'{args} -start {self.time_start}'
        if self.time_end is not None:
            args = f'{args} -end {self.time_end}'
        cmd = f'{self.java} -cp lib/processor.jar CommandDispatcherMain {args}'
        await self.run(shlex.split(cmd))

    async def trim_recursively(self, dir, name):
        files = glob.iglob(f'{dir}/**/{name}.hdr', recursive=True)
        await asyncio.gather(*(self.trim(file) for file in files if not file.endswith("trimmed.hdr")))

    async def merge_recursively(self, dir, name):
        files = glob.iglob(f'{dir}/**/{name}.trimmed.hdr', recursive=True)
        input = " ".join(f"-ifp {file}" for file in files)
        cmd = f'{self.java} -cp lib/processor.jar CommandDispatcherMain union {input} -of {dir}/{name}.trimmed.hdr'
        await self.run(shlex.split(cmd))

    async def summarize(self, file):
        file_no_ext = os.path.splitext(file)[0]

        summary_text_name = f"{file_no_ext}-summary.txt"
        #summary_csv_name = f"{file_no_ext}-summary.csv"

        args = f'-ifp {file_no_ext}.hdr'
        await self.run(["bash", "-c", f'{self.java} -cp lib/processor.jar CommandDispatcherMain summarize {args} > {summary_text_name}'])

        entries = {}
        with open(summary_text_name, 'r') as summary_text_file:
            for line in summary_text_file:
                row = line.split('=')
                entries[row[0].strip()] = row[1].strip()

        #with open(summary_csv_name, 'w') as summary_csv_file:
        #    header = ','.join(entries.keys())
        #    content = ','.join(entries.values())
        #    summary_csv_file.write(f'{header}\n')
        #    summary_csv_file.write(f'{content}\n')

    async def summarize_recursively(self, dir, name):
        files = glob.iglob(f'{dir}/**/{name}.trimmed.hdr', recursive=True)
        await asyncio.gather(*(self.summarize(file) for file in files))
        return parse_profile_summary_file(f'{dir}/{name}.trimmed-summary.txt')

    async def process(self, file):
        file_no_ext = os.path.splitext(file)[0]
        tags = set()
        with open(file, "r") as hdr_file:
            reader = csv.reader(hdr_file, delimiter=',')
            # Skip headers
            for i in range(5):
                next(reader, None)
            for row in reader:
                first_column = row[0]
                tag = first_column[4:]
                tags.add(tag)

        tasks = []
        for tag in tags:
            logprocessor = f'{self.java} -cp lib/HdrHistogram-2.1.12.jar org.HdrHistogram.HistogramLogProcessor'
            #args = f'-i {file} -o {file_no_ext}_{tag}.csv -tag {tag} -csv'
            #tasks.append(self.run(shlex.split(f'{logprocessor} {args}')))
            args = f'-i {file} -o {file_no_ext}_{tag} -tag {tag}'
            tasks.append(self.run(shlex.split(f'{logprocessor} {args}')))
        asyncio.gather(*tasks)

    async def process_recursively(self, dir, name):
        files = glob.iglob(f'{dir}/**/{name}.trimmed.hdr', recursive=True)
        await asyncio.gather(*(self.process(file) for file in files))

ProfileSummaryResult = namedtuple('ProfileSummaryResult',
                                  ['ops_count', 'stress_time_s', 'throughput_per_second', 'mean_latency_ms',
                                   'median_latency_ms', 'p90_latency_ms', 'p99_latency_ms', 'p99_9_latency_ms',
                                   'p99_99_latency_ms', 'p99_999_latency_ms'])

def parse_profile_summary_file(path):
    with open(path) as f:
        lines = f.readlines()
        summary = dict([x.split('=') for x in lines])
        tags = set([x.split('.')[0] for x in lines])
        result = {}
        for tag in tags:
            ops_count = int(summary[f'{tag}.TotalCount'])
            stress_time_s = float(summary[f'{tag}.Period(ms)']) / 1000
            throughput_per_second = float(summary[f'{tag}.Throughput(ops/sec)'])
            mean_latency_ms = float(summary[f'{tag}.Mean']) / 1_000_000
            median_latency_ms = float(summary[f'{tag}.50.000ptile']) / 1_000_000
            p90_latency_ms = float(summary[f'{tag}.90.000ptile']) / 1_000_000
            p99_latency_ms = float(summary[f'{tag}.99.000ptile']) / 1_000_000
            p99_9_latency_ms = float(summary[f'{tag}.99.900ptile']) / 1_000_000
            p99_99_latency_ms = float(summary[f'{tag}.99.990ptile']) / 1_000_000
            p99_999_latency_ms = float(summary[f'{tag}.99.999ptile']) / 1_000_000

            result[tag] = ProfileSummaryResult(
                ops_count=ops_count,
                stress_time_s=stress_time_s,
                throughput_per_second=throughput_per_second,
                mean_latency_ms=mean_latency_ms,
                median_latency_ms=median_latency_ms,
                p90_latency_ms=p90_latency_ms,
                p99_latency_ms=p99_latency_ms,
                p99_9_latency_ms=p99_9_latency_ms,
                p99_99_latency_ms=p99_99_latency_ms,
                p99_999_latency_ms=p99_999_latency_ms)
        return result
