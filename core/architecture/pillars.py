"""
The 18 enterprise pillars, and the arithmetic that makes each later question specific.

A questionnaire that asks the same 18 questions in the same words regardless of what was
already said is a form, not an interview. The operator says "8 GB a day, hourly partitions"
in pillar 3 and the interview carries on offering the same three storage options it would
have offered for 8 TB -- when what that answer actually implies is 85 MB per partition,
below the Parquet block size, which is the small-file problem being designed in on purpose.

So this module is two things, and the split matters:

  PILLARS   is the catalogue -- question, options, what each option maps to in the module
            registry, and the DEPTH follow-ups that only become answerable once the pillar
            itself is answered. Data, no logic.
  derive()  reads the answers so far and computes what the next questions should recommend.
            Real arithmetic over real published capacities, not a remembered rule of thumb.

WHAT THIS REFUSES TO DO. It never invents an input. With no stated volume there is no
recommended worker count, and `derive` returns `determinable: False` with the reason --
never a plausible-looking default. A number nobody gave us, presented as a recommendation,
is the same fabrication as a made-up price; the operator cannot tell it from a real one.

Heuristics are labelled as heuristics. `SPARK_MEMORY_FACTOR` is this project's working
assumption, not an AWS figure, and it says so where it is defined so a reader does not cite
it back to Amazon.

Grounded in: the RE'25 follow-up-question-generation framework (arXiv:2507.02858), which is
why depth follow-ups are attached per-pillar and conditioned on the answer rather than asked
as a flat list.

Depends on: nothing. Standard library only.
Shells out to: nothing.
Used by: core/architecture/requirements.py (pillar-id validation),
    .agents/skills/grill-me/SKILL.md (the interview reads its recommendations from here),
    tests/test_pillars.py
"""
import argparse
import json
import math
import sys

# --- Published capacities -------------------------------------------------------------
#
# Every number below is a vendor-published capacity, with the source that states it. They
# are separated from the heuristics on purpose: these can be checked, the heuristics can
# only be argued about.

SOURCES = {
    "glue_workers": "https://docs.aws.amazon.com/glue/latest/dg/worker-types.html",
    "glue_large_workers":
        "https://aws.amazon.com/blogs/big-data/scale-your-aws-glue-for-apache-spark-jobs-"
        "with-new-larger-worker-types-g-4x-and-g-8x",
    "parquet_file_size":
        "https://docs.aws.amazon.com/athena/latest/ug/performance-tuning-s3-throttling-"
        "optimizing-your-tables.html",
    "glue_compaction": "https://docs.aws.amazon.com/glue/latest/dg/compaction-management.html",
    "kinesis_shard": "https://docs.aws.amazon.com/streams/latest/dev/service-sizes-and-limits.html",
    "lake_formation": "https://docs.aws.amazon.com/lake-formation/latest/dg/lf-tag-considerations.html",
    "orchestration":
        "https://aws.amazon.com/blogs/big-data/choosing-the-right-workflow-orchestration-"
        "service-for-your-use-case-amazon-mwaa-and-aws-step-functions/",
    "ecr_immutability":
        "https://docs.aws.amazon.com/AmazonECR/latest/userguide/image-tag-mutability.html",
    "codeartifact_token":
        "https://docs.aws.amazon.com/codeartifact/latest/ug/tokens-authentication.html",
}

# worker type -> (vCPU, memory GB, disk GB, DPU). AWS Glue worker-types documentation.
GLUE_WORKERS = (
    ("G.1X", 4, 16, 94, 1),
    ("G.2X", 8, 32, 138, 2),
    ("G.4X", 16, 64, 256, 4),
    ("G.8X", 32, 128, 512, 8),
)

# The R series carries twice the memory of its G equivalent, for shuffle- and cache-heavy
# work. Announced 2025-07; not available in every region, which is why a recommendation
# that reaches for one says so.
GLUE_MEMORY_WORKERS = (
    ("R.1X", 4, 32, 94, 1),
    ("R.2X", 8, 64, 138, 2),
    ("R.4X", 16, 128, 256, 4),
    ("R.8X", 32, 256, 512, 8),
)

# One Kinesis Data Streams shard ingests 1 MB/s OR 1,000 records/s, whichever binds first.
KINESIS_SHARD_MB_PER_SEC = 1.0
KINESIS_SHARD_RECORDS_PER_SEC = 1000

# Target object size on S3, by read pattern. Below the floor, per-object request overhead
# and Glue Catalog metadata dominate; above the ceiling, parallelism drops.
TARGET_OBJECT_MB = {
    "read_heavy": (256, 512),
    "mixed": (128, 256),
    "write_heavy": (64, 128),
}
PARQUET_BLOCK_MB = 128          # Parquet's own default block size, and the practical floor.

# --- Heuristics (this project's assumptions, not vendor figures) -----------------------

# Spark wants headroom over the working set it shuffles. 3x for a wide shuffle (joins,
# skewed aggregations), 1.5x for a narrow scan-and-write. This is OUR working assumption,
# arrived at from the worker sizes AWS recommends for each workload shape -- it is not a
# published Amazon ratio, and a recommendation built on it says which factor it used so the
# operator can disagree with the number rather than with the conclusion.
SPARK_MEMORY_FACTOR = {"wide": 3.0, "narrow": 1.5}

# A run that touches one day's data is the common case; a backfill is not, and is asked
# about rather than assumed.
DEFAULT_RUNS_PER_DAY = 1


def _undetermined(reason, **extra):
    """Absence, stated. Never a default standing in for an answer nobody gave."""
    result = {"determinable": False, "reason": reason}
    result.update(extra)
    return result


# --- Derivations ------------------------------------------------------------------------

def glue_worker_plan(daily_gb, shuffle="wide", runs_per_day=DEFAULT_RUNS_PER_DAY):
    """Pick a Glue worker type and count from the volume one run actually processes.

    Returns the smallest configuration whose aggregate memory covers the working set times
    the shuffle factor, preferring more small workers over fewer large ones until a single
    G.1X can no longer hold a partition's share.
    """
    if daily_gb in (None, "") or float(daily_gb) <= 0:
        return _undetermined(
            "no daily data volume was given, so no worker count can be computed -- ask "
            "pillar 3 before pillar 6")
    if shuffle not in SPARK_MEMORY_FACTOR:
        return _undetermined(f"unknown transform shape {shuffle!r}; expected one of "
                             f"{sorted(SPARK_MEMORY_FACTOR)}")

    per_run_gb = float(daily_gb) / max(int(runs_per_day or 1), 1)
    factor = SPARK_MEMORY_FACTOR[shuffle]
    needed_gb = per_run_gb * factor

    for name, vcpu, memory_gb, disk_gb, dpu in GLUE_WORKERS:
        count = max(2, math.ceil(needed_gb / memory_gb))
        if count <= 20:
            return {
                "determinable": True,
                "worker_type": name,
                "number_of_workers": count,
                "total_dpu": count * dpu,
                "per_run_gb": round(per_run_gb, 2),
                "memory_target_gb": round(needed_gb, 1),
                "because": (
                    f"{per_run_gb:.1f} GB per run x {factor} ({shuffle} shuffle) needs about "
                    f"{needed_gb:.0f} GB of executor memory; {count} x {name} "
                    f"({memory_gb} GB, {vcpu} vCPU, {disk_gb} GB disk each) covers it at "
                    f"{count * dpu} DPU"),
                "memory_optimized_alternative": _memory_alternative(needed_gb),
                "source": SOURCES["glue_workers"],
            }

    return {
        "determinable": True,
        "worker_type": "G.8X",
        "number_of_workers": math.ceil(needed_gb / 128),
        "total_dpu": math.ceil(needed_gb / 128) * 8,
        "per_run_gb": round(per_run_gb, 2),
        "memory_target_gb": round(needed_gb, 1),
        "because": (
            f"{per_run_gb:.1f} GB per run x {factor} needs about {needed_gb:.0f} GB, which is "
            f"past what the G series covers comfortably -- price EMR against this before "
            f"committing, because at this size the Glue premium stops being small"),
        "memory_optimized_alternative": _memory_alternative(needed_gb),
        "source": SOURCES["glue_large_workers"],
    }


def _memory_alternative(needed_gb):
    """The R-series equivalent, when the working set is what hurts rather than the CPU."""
    for name, _vcpu, memory_gb, _disk, dpu in GLUE_MEMORY_WORKERS:
        count = max(2, math.ceil(needed_gb / memory_gb))
        if count <= 20:
            return (f"{count} x {name} ({memory_gb} GB each) if caching or a skewed join is "
                    f"what runs you out of memory; check regional availability first")
    return None


def object_size_plan(daily_gb, partitions_per_day, read_pattern="mixed"):
    """What the stated volume and partitioning actually produce, per object.

    This is the check that catches a small-file problem at design time rather than at the
    first slow query: hourly partitions on a small feed produce objects below the Parquet
    block size, and no amount of later tuning undoes a partition key.
    """
    if daily_gb in (None, "") or float(daily_gb) <= 0:
        return _undetermined("no daily data volume was given")
    if not partitions_per_day or int(partitions_per_day) <= 0:
        return _undetermined("no partition granularity was given")
    if read_pattern not in TARGET_OBJECT_MB:
        return _undetermined(f"unknown read pattern {read_pattern!r}; expected one of "
                             f"{sorted(TARGET_OBJECT_MB)}")

    floor_mb, ceiling_mb = TARGET_OBJECT_MB[read_pattern]
    per_partition_mb = float(daily_gb) * 1024 / int(partitions_per_day)

    result = {
        "determinable": True,
        "per_partition_mb": round(per_partition_mb, 1),
        "target_mb": [floor_mb, ceiling_mb],
        "read_pattern": read_pattern,
        "source": SOURCES["parquet_file_size"],
    }
    # Four bands, not three. The middle two are both acceptable and they are NOT the same
    # thing, so they do not share a sentence: an object above the target still reads fine and
    # only costs parallelism, while one below the floor costs a request per object forever.
    if per_partition_mb < floor_mb:
        result["verdict"] = "TOO_SMALL"
        result["because"] = (
            f"{daily_gb} GB/day across {partitions_per_day} partitions is "
            f"{per_partition_mb:.0f} MB each, under the {floor_mb} MB floor for "
            f"{read_pattern} reads"
            + (f" and under the {PARQUET_BLOCK_MB} MB Parquet block size"
               if per_partition_mb < PARQUET_BLOCK_MB else "")
            + ". Every query pays per-object request and catalog overhead for data that "
              "would fit in one object.")
        result["options"] = (
            "Partition coarser -- daily instead of hourly is usually the whole fix.",
            f"Keep the partitioning and schedule compaction into {floor_mb}-{ceiling_mb} MB "
            f"objects ({SOURCES['glue_compaction']}).",
            "Drop the partition key entirely and rely on Iceberg metadata pruning.",
        )
    elif per_partition_mb <= ceiling_mb:
        result["verdict"] = "OK"
        result["because"] = (
            f"{per_partition_mb:.0f} MB per partition sits inside the "
            f"{floor_mb}-{ceiling_mb} MB target for {read_pattern} reads.")
    elif per_partition_mb <= ceiling_mb * 4:
        result["verdict"] = "ABOVE_TARGET"
        result["because"] = (
            f"{per_partition_mb:.0f} MB per partition is above the {ceiling_mb} MB target "
            f"for {read_pattern} reads. That reads correctly -- it costs parallelism, not "
            f"correctness, because the partition count caps how many workers can share the "
            f"scan.")
        result["options"] = (
            "Accept it if the partition count already exceeds the worker count.",
            f"Partition finer, or set a target file size so writers split at "
            f"{floor_mb}-{ceiling_mb} MB.",
        )
    else:
        result["verdict"] = "TOO_LARGE"
        result["because"] = (
            f"{per_partition_mb:.0f} MB per partition is more than four times the "
            f"{ceiling_mb} MB ceiling for {read_pattern} reads; parallelism is capped by the "
            f"partition count long before the workers are busy.")
        result["options"] = (
            "Partition finer, or add a second key the queries actually filter on.",
            f"Set a target file size so writers split at {floor_mb}-{ceiling_mb} MB.",
        )
    return result


def kinesis_shard_plan(events_per_sec, avg_record_kb):
    """Shards needed, and which of the two published limits binds.

    A shard takes 1 MB/s or 1,000 records/s. Which one runs out first changes the answer and
    the fix, so both are reported rather than only the maximum.
    """
    if not events_per_sec or float(events_per_sec) <= 0:
        return _undetermined("no event rate was given")
    if not avg_record_kb or float(avg_record_kb) <= 0:
        return _undetermined("no average record size was given")

    events = float(events_per_sec)
    mb_per_sec = events * float(avg_record_kb) / 1024
    by_throughput = math.ceil(mb_per_sec / KINESIS_SHARD_MB_PER_SEC)
    by_count = math.ceil(events / KINESIS_SHARD_RECORDS_PER_SEC)
    shards = max(by_throughput, by_count, 1)
    binding = "throughput" if by_throughput >= by_count else "record count"

    return {
        "determinable": True,
        "shards": shards,
        "mb_per_sec": round(mb_per_sec, 2),
        "by_throughput": by_throughput,
        "by_record_count": by_count,
        "binding_limit": binding,
        "because": (
            f"{events:,.0f} records/s at {avg_record_kb} KB is {mb_per_sec:.2f} MB/s. A shard "
            f"carries {KINESIS_SHARD_MB_PER_SEC} MB/s or "
            f"{KINESIS_SHARD_RECORDS_PER_SEC:,} records/s, so {binding} is what binds: "
            f"{shards} shard(s)."),
        "note": ("If the rate is spiky rather than steady, on-demand removes the shard "
                 "decision entirely and is usually the right answer for an unknown profile."),
        "source": SOURCES["kinesis_shard"],
    }


def engine_recommendation(daily_gb, transform_shape, has_spark_skills=None):
    """Which compute engine, driven by the transform's SHAPE first and volume second.

    Volume alone is the wrong primary axis, and the fixed GB bands this skill used to quote
    were nobody's published guidance. What actually decides it is whether the work is
    expressible in SQL: a SQL-only transform on a large table is still SQL, and moving it to
    Spark buys a cluster to run a query Athena would have run.
    """
    if transform_shape == "sql_only":
        return {
            "determinable": True,
            "engine": "dbt-on-Athena",
            "because": ("the transformation is expressible in SQL, so the engine choice is "
                        "made -- volume changes the partitioning and the scanned-bytes bill, "
                        "not whether Spark is needed"),
            "maps_to": ["query-athena", "dbt-semantic-layer"],
            "revisit_if": ("a transform appears that SQL cannot express -- an ML feature step, "
                           "a custom Python UDF, a non-tabular parse"),
        }

    if transform_shape not in ("spark", "streaming"):
        return _undetermined(
            "the transform shape was not established; ask whether the work is expressible in "
            "SQL before choosing an engine")

    if transform_shape == "streaming":
        return {
            "determinable": True,
            "engine": "Kinesis + Firehose, or Glue streaming",
            "because": ("a continuous feed is an orchestration and delivery decision before "
                        "it is a compute one"),
            "maps_to": ["ingest-firehose", "streaming-msk-kafka", "compute-glue-etl"],
        }

    plan = glue_worker_plan(daily_gb, shuffle="wide")
    if not plan["determinable"]:
        return _undetermined(
            "Spark is needed, but without a volume there is no sizing -- and an unsized Glue "
            "job is the default that produced this project's over-provisioned runs",
            engine="AWS Glue (unsized)", maps_to=["compute-glue-etl"])

    recommendation = {
        "determinable": True,
        "engine": "AWS Glue",
        "because": (f"Spark is required and {plan['worker_type']} x "
                    f"{plan['number_of_workers']} covers it at {plan['total_dpu']} DPU, with "
                    f"no cluster to operate"),
        "maps_to": ["compute-glue-etl"],
        "sizing": plan,
    }
    if plan["total_dpu"] >= 64:
        recommendation["revisit_if"] = (
            f"{plan['total_dpu']} DPU is large enough that EMR's lower per-unit price can "
            f"outweigh the cluster it costs you to run -- price both before committing, and "
            f"note that EMR needs someone to own it")
        recommendation["maps_to"].append("compute-emr-serverless")
    if has_spark_skills is False:
        recommendation["because"] += ("; the team stated no Spark experience, which is an "
                                      "argument against EMR regardless of price")
    return recommendation


DERIVATIONS = {
    "glue_worker_plan": glue_worker_plan,
    "object_size_plan": object_size_plan,
    "kinesis_shard_plan": kinesis_shard_plan,
    "engine_recommendation": engine_recommendation,
}


# --- The catalogue ----------------------------------------------------------------------
#
# `depth` is the part the flat 18-question list was missing. A pillar's main question picks
# a direction; the depth follow-ups are what that direction then obliges you to decide, and
# they are keyed by the option chosen because a follow-up that applies to every answer is
# just another top-level question. `informs` records which later pillars an answer feeds, so
# the interview can say WHY it is asking, and skip what it can already derive.
#
# `forgotten` is the single thing about this pillar that people leave out and discover in
# production. It is stated separately from the question because it survives the answer: an
# operator who picks an option still needs telling what the option does not cover.

def _p(number, phase, key, title, question, options, maps_to, depth, informs=(),
       derives=None, forgotten=None):
    return {"id": number, "phase": phase, "key": key, "title": title, "question": question,
            "options": list(options), "maps_to": list(maps_to), "depth": depth,
            "informs": list(informs), "derives": derives, "forgotten": forgotten}


PHASES = {
    1: "Ingestion, storage and data quality",
    2: "Compute, sizing and runtime",
    3: "Network, accounts and governance",
    4: "Serving, delivery and proving",
}

PILLARS = (
    _p(1, 1, "ingestion_source", "Ingestion source and delivery protocol",
       "Where does the data come from today, and how does it arrive?",
       ("Batch files landing in S3 (CSV, JSON, Parquet)",
        "Database CDC replication (DMS or Glue JDBC)",
        "Partner file drops over SFTP (Transfer Family)",
        "Continuous event feed (Kinesis, MSK, API Gateway)"),
       ("ingestion-dms", "ingestion-appflow", "ingestion-sftp", "ingestion-webhook",
        "ingest-firehose"),
       depth={
           "Batch files landing in S3 (CSV, JSON, Parquet)": (
               "What lands the file -- a partner job, an internal export, or a person?",
               "Is a late or duplicate file possible, and which copy wins?",
               "Can a file arrive partially written, and is there a completion marker?"),
           "Database CDC replication (DMS or Glue JDBC)": (
               "Full load once then CDC, or CDC only? A CDC-only start has no history.",
               "Are DELETEs replicated, or is the target append-only?",
               "What happens to the pipeline when the source schema changes?"),
           "Partner file drops over SFTP (Transfer Family)": (
               "How many partners, and does each get its own prefix and key?",
               "Is the file encrypted at rest by the partner, or only in transit?"),
           "Continuous event feed (Kinesis, MSK, API Gateway)": (
               "Peak events per second, and average record size in KB? Both are needed to "
               "size shards; one alone cannot.",
               "Steady rate or spiky? Spiky argues for on-demand over provisioned shards.",
               "Is ordering required, and if so within which key?"),
       },
       informs=("compute_engine", "orchestration", "partitioning"),
       derives="kinesis_shard_plan",
       forgotten="A lake with no inbound path is the one failure that cannot be fixed later."),

    _p(2, 1, "storage_format", "Storage medallion and table format",
       "How should the storage zones, table format and encryption be structured?",
       ("Bronze raw, Silver Parquet, Gold Iceberg, customer-managed KMS key",
        "Bronze, Silver and Gold Parquet on the AWS-managed S3 key",
        "Two tiers, raw and curated, with SSE-S3"),
       ("storage-medallion-s3", "table-format-iceberg"),
       depth={
           "*": (
               "Who may read Bronze? It holds whatever arrived, including the fields Gold "
               "restricts.",
               "Does anything need row-level updates or deletes -- an erasure request, a "
               "restatement? That is the argument for Iceberg over plain Parquet.",
               "Is a customer-managed key required by policy, or is the AWS-managed key "
               "acceptable? A CMK bills monthly and per request."),
       },
       informs=("partitioning", "governance_access"),
       forgotten="Whether Bronze is readable by the same people who may read Gold."),

    _p(3, 1, "partitioning", "Partitioning, retention and object size",
       "What partition key, what retention per zone, and how many GB per day?",
       ("Date-partitioned, Bronze to Glacier after a stated period",
        "Date and tenant partitioned, regulatory retention in Glacier",
        "Unpartitioned, relying on Iceberg metadata pruning"),
       ("storage-medallion-s3", "query-athena"),
       depth={
           "*": (
               "Daily volume in GB -- the number, not a band. Everything downstream is sized "
               "from it, and nothing downstream can be sized without it.",
               "Partition granularity: daily, hourly, or by an event key?",
               "Read pattern: mostly large scans, mostly point lookups, or mixed?",
               "Is there a backfill, and how much history does it replay at once?",
               "Retention per zone, in days, and to which storage class."),
       },
       informs=("compute_engine", "worker_sizing", "serving"),
       derives="object_size_plan",
       forgotten=("Hourly partitioning on a small feed designs in the small-file problem. "
                  "The partition key is the one choice that cannot be tuned afterwards.")),

    _p(4, 1, "data_quality", "Data quality contracts and quarantine routing",
       "What happens to a record that fails a quality assertion?",
       ("Quarantine: route failing rows aside and continue the run",
        "Fail fast: abort the whole run on any assertion failure",
        "Warn only: record the metric and load everything"),
       ("dq-great-expectations",),
       depth={
           "Quarantine: route failing rows aside and continue the run": (
               "Who reads the quarantine bucket, and how often? An unread quarantine is "
               "silent data loss with extra steps.",
               "At what failure rate does a quarantined run become a failed run? A job that "
               "quarantines most of its input and reports success is the worst outcome.",
               "Are quarantined rows replayable after a fix, or written off?"),
           "Fail fast: abort the whole run on any assertion failure": (
               "One malformed row then halts the pipeline for every consumer. Is that the "
               "intended trade?",
               "Is there an override for a known-bad upstream day?"),
           "Warn only: record the metric and load everything": (
               "Which downstream consumer is trusted to notice? Warn-only moves the problem "
               "to whoever queries Gold, without telling them.",),
       },
       informs=("alerting",),
       forgotten="The failure branch. Everyone specifies the assertion; few specify the row."),

    _p(5, 2, "compute_engine", "Compute engine selection",
       "Is the transformation expressible in SQL, or does it need Spark?",
       ("SQL only -- dbt on Athena",
        "Spark needed -- AWS Glue",
        "Spark at scale with cluster ownership -- EMR"),
       ("compute-glue-etl", "dbt-semantic-layer", "compute-emr-serverless",
        "compute-emr-ec2-spot"),
       depth={
           "Spark needed -- AWS Glue": (
               "What makes it non-SQL -- a Python UDF, an ML step, a non-tabular parse?",
               "Wide shuffle (joins, skewed aggregations) or narrow (scan, map, write)? That "
               "sets the memory factor and therefore the worker count.",
               "How many runs per day? Volume per run sizes the workers, not volume per day."),
           "Spark at scale with cluster ownership -- EMR": (
               "Who operates the cluster? EMR's lower unit price is paid for in ops time.",
               "Spot for task nodes -- and what happens to a run when spot capacity is "
               "reclaimed mid-shuffle?"),
           "SQL only -- dbt on Athena": (
               "Athena bills scanned bytes. Do the queries filter on the partition key?",
               "Is a per-query or per-workgroup scan limit wanted as a cost guardrail?"),
       },
       informs=("worker_sizing", "runtime_packages"),
       derives="engine_recommendation",
       forgotten="A SQL-only transform does not need Spark, whatever the volume is."),

    _p(6, 2, "worker_sizing", "Worker sizing, headroom and autoscaling",
       "How should compute workers be sized? Derived from volume -- confirm or override.",
       ("Accept the derived sizing",
        "Override with a stated worker type and count",
        "Autoscale between a floor and a ceiling"),
       ("compute-glue-etl", "compute-emr-ec2-spot"),
       depth={
           "*": (
               "Is there a run much larger than the others -- a month-end, a backfill? Size "
               "for it, or schedule it separately.",
               "What run duration is acceptable? Doubling workers roughly halves it until "
               "shuffle dominates.",
               "Should the job fail or queue when it exceeds the ceiling?"),
       },
       derives="glue_worker_plan",
       forgotten="Sizing from daily volume rather than per-run volume over-provisions every run."),

    _p(7, 2, "runtime_packages", "Runtime libraries and dependency delivery",
       "What packages or JARs do the jobs need, and where do they come from?",
       ("Public PyPI via --additional-python-modules",
        "Private wheels or JARs from an internal repository",
        "Custom container image on ECR"),
       ("compute-glue-etl",),
       depth={
           "*": (
               "Are versions pinned? An unpinned dependency makes the job non-reproducible, "
               "and the failure arrives on someone else's shift.",
               "Does anything need a native extension or a compiler? That decides container "
               "versus plain packages.",
               "Who patches these when a CVE lands?"),
       },
       forgotten="Dependency pinning. An unpinned job is a different job every run."),

    _p(8, 2, "orchestration", "Orchestration cadence and triggers",
       "What starts a run?",
       ("Event-driven: EventBridge on S3 arrival into Step Functions",
        "Scheduled DAG on MWAA",
        "EventBridge cron straight to a Glue workflow"),
       ("orchestrator-stepfunctions", "orchestrator-mwaa"),
       depth={
           "Event-driven: EventBridge on S3 arrival into Step Functions": (
               "What if two files land at once -- concurrent runs, or a queue?",
               "What if no file ever lands? A missing-input alarm is a different alarm from "
               "a failure alarm, and only one of them fires here."),
           "Scheduled DAG on MWAA": (
               "MWAA bills for an always-on environment. Is a scheduler running around the "
               "clock justified by the DAG count, or is this one DAG?",
               "Which cron, in which timezone, and what happens on a DST shift?",
               "Does anything orchestrate outside AWS? That is the strongest argument for "
               "Airflow over Step Functions."),
           "EventBridge cron straight to a Glue workflow": (
               "No retry topology and no branching. Is the workflow really that linear?",),
       },
       informs=("alerting",),
       forgotten=("A pipeline nobody scheduled never runs, and a missing-input alarm is not "
                  "the same alarm as a job-failure alarm.")),

    _p(9, 3, "network", "Availability zones, subnets and endpoints",
       "How should networking be laid out?",
       ("Private multi-AZ VPC with an S3 gateway endpoint",
        "Two AZs, one shared NAT gateway, S3 gateway endpoint",
        "Attach to existing corporate subnets"),
       ("networking-vpc",),
       depth={
           "*": (
               "Is the S3 gateway endpoint present? Without it every lake read is billed as "
               "NAT traffic. Nothing fails; it only shows up on the invoice.",
               "Which other services need endpoints -- Secrets Manager, KMS, CloudWatch?",
               "Does anything need inbound access from outside the VPC?"),
       },
       forgotten="The S3 gateway endpoint. It is free, and its absence is invisible until billing."),

    _p(10, 3, "account_topology", "Account boundaries",
       "One account, or a producer and consumer split?",
       ("Hub and spoke: a central lake account, separate consumer accounts",
        "Single account with logical separation"),
       ("governance-lakeformation", "security-iam-scoped"),
       depth={
           "Hub and spoke: a central lake account, separate consumer accounts": (
               "Who owns the central account, and who approves a new spoke?",
               "Cross-account sharing through Lake Formation or through bucket policies? "
               "They fail differently and are debugged differently."),
           "Single account with logical separation": (
               "What stops a consumer role from reading Bronze directly?",),
       },
       informs=("governance_access",),
       forgotten="Who approves a new consumer, and where that approval is recorded."),

    _p(11, 3, "disaster_recovery", "Multi-region DR and data residency",
       "What are the recovery and residency requirements?",
       ("Active-passive multi-region with S3 cross-region replication",
        "Single region with versioning and a Glacier archive"),
       ("storage-medallion-s3",),
       depth={
           "*": (
               "RTO and RPO as numbers -- how long down, how much data lost. Without both, "
               "'highly available' is not a requirement anyone can build to.",
               "Is there a residency rule that forbids a second region?",
               "Has the restore ever been run? An untested backup is a hypothesis."),
       },
       informs=("criticality",),
       forgotten=("RTO and RPO. A stated uptime target with no recovery numbers behind it "
                  "cannot be designed for or verified.")),

    _p(12, 3, "governance_access", "Fine-grained access control",
       "Is row- or column-level access control required on Gold tables?",
       ("Lake Formation tag-based access control with column-level permissions",
        "IAM and bucket policies only"),
       ("governance-lakeformation", "security-iam-scoped"),
       depth={
           "Lake Formation tag-based access control with column-level permissions": (
               "Which columns are restricted, and to which group? Lake Formation RESTRICTS "
               "a column; it does not mask it. A requirement for a masked value means the "
               "masking happens in the transform, before Gold.",
               "Who assigns LF-Tags, and is that grant reviewed?",
               "Do the same rules apply to Silver, or only to Gold?"),
           "IAM and bucket policies only": (
               "S3 and IAM act on objects and prefixes, not rows and columns -- a Parquet "
               "file either grants GetObject or it does not. If a column-level rule exists, "
               "this option cannot enforce it.",),
       },
       forgotten=("Lake Formation does not mask values; it grants or withholds a column. "
                  "A masked-value requirement is a transform requirement.")),

    _p(13, 4, "serving", "Serving layer and consumption",
       "Who reads the output, and with what?",
       ("Athena over Gold Iceberg tables",
        "Redshift Serverless with an RPU usage limit",
        "A semantic layer (dbt MetricFlow or Cube)"),
       ("query-athena", "consumption-redshift-serverless", "dbt-semantic-layer",
        "cube-semantic-layer"),
       depth={
           "Athena over Gold Iceberg tables": (
               "Which groups query it -- analysts, data science, modelling -- and does each "
               "need a different Gold prefix? One role shared between them is the wildcard "
               "least privilege exists to refuse, wearing a narrower name.",
               "What is the per-query scan ceiling for each group? An analyst exploring and "
               "a scheduled dashboard do not want the same limit.",
               "Does each group carry its own cost centre, or is all query spend one line "
               "nobody can attribute?"),
           "Redshift Serverless with an RPU usage limit": (
               "Base and maximum RPU, and which groups share the workgroup?",
               "On a usage-limit breach, log or deactivate? Deactivate takes BI offline "
               "mid-quarter-close.",
               "Is anyone loading INTO Redshift, or is it read-only over Spectrum? A writer "
               "needs a different policy from every reader."),
           "A semantic layer (dbt MetricFlow or Cube)": (
               "Who owns the metric definitions -- one platform team, or each consumer "
               "group? Two owners of one metric is the problem a semantic layer removes.",
               "Does the layer enforce access, or inherit it from the lake? A layer that "
               "answers a query the lake would refuse is a bypass, not a cache.",
               "Are pre-aggregations built per group or shared? Cube's entire cost argument "
               "is the cache; without one it re-scans the lake per dashboard refresh."),
       },
       informs=("governance_access", "criticality", "alerting"),
       derives="consumer_access_plan",
       forgotten="A scan limit. Serverless query engines have no natural cost ceiling."),

    _p(14, 4, "cicd", "CI/CD pipeline, control plane hosting and secrets",
       "Which platform builds, scans and deploys this, and where does MinusOps itself run?",
       ("GitHub Actions with AWS OIDC federation",
        "Jenkins on private agents with instance profiles",
        "GitLab CI or Azure DevOps"),
       ("core/generation/cicd.py",),
       depth={
           "*": (
               "Control plane hosting -- how is MinusOps itself driven? An operator laptop "
               "on the CLI, a CI runner, or in-cluster on EKS. They need different "
               "credentials and different artifacts: a laptop uses the ambient CLI chain, a "
               "runner uses OIDC, and EKS uses IRSA to bind a service account to a role. "
               "Answer this before CI/CD, because it decides what CI/CD is authenticating as.",
               "OIDC federation, or long-term AKIA access keys? A static key in CI is the "
               "credential most often found in a breach post-mortem, and this project's own "
               "deploy gate warns on AKIA rather than ASIA for the same reason. OIDC issues "
               "a short-lived credential per run via AssumeRole and leaves nothing to leak.",
               "Where do the pipeline's own secrets live -- Secrets Manager for anything "
               "that rotates, SSM Parameter Store for configuration that does not? A "
               "rotating credential in Parameter Store is a credential nobody rotates.",
               "Which KMS key encrypts what, and who can use it as opposed to manage it? "
               "kms:Decrypt on the wrong principal undoes the bucket policy above it.",
               "Is the Terraform lock file committed and are provider versions pinned? "
               "Without both, CI applies something the reviewer never planned.",
               "Who may approve a production deploy, and is that enforced or documented?"),
       },
       forgotten=("Whether the plan CI applies is the plan a human actually reviewed -- and "
                  "whether CI holds a static key that makes the question moot.")),

    _p(15, 4, "artifacts", "Artifact management",
       "Where do built packages and images go?",
       ("ECR and an immutable S3 artifact bucket, digest-verified",
        "JFrog Artifactory",
        "AWS CodeArtifact"),
       ("core/generation/cicd.py",),
       depth={
           "ECR and an immutable S3 artifact bucket, digest-verified": (
               "Are image tags immutable? A mutable tag means the digest you approved is "
               "not the digest that runs.",
               "Does scan-on-push block the deploy on a critical finding, or only record it?",
               "Who pulls it -- this account only, or is a cross-account policy needed?"),
           "JFrog Artifactory": (
               "What is the base URL and the repository key?",
               "Which Secrets Manager ARN holds the credential? A token passed as a "
               "Terraform variable lands in the plan and in state (FM-02).",
               "Local, remote or virtual repository? A virtual repo can resolve to an "
               "upstream nobody reviewed."),
           "AWS CodeArtifact": (
               "Which domain and repository?",
               "Is there an upstream to public PyPI or npm, and is that upstream allowed "
               "to reach production?",
               "A CodeArtifact auth token lasts 12 hours at most. Is the whole pipeline, "
               "including any manual approval wait, shorter than the token?"),
       },
       informs=("cicd", "proving"),
       derives="artifact_promotion_plan",
       forgotten="Build once, deploy many. A per-environment rebuild untests the testing."),

    _p(16, 4, "criticality", "Business criticality tier",
       "What breaks, and for whom, when this pipeline fails?",
       ("Tier 0 -- regulatory, billing or financial ledger",
        "Tier 1 -- executive or customer-facing reporting",
        "Tier 2 -- departmental analytics with a manual workaround",
        "Tier 3 -- sandbox or ad-hoc"),
       ("core/reporting/incident_diagnostics.py",),
       depth={
           "Tier 0 -- regulatory, billing or financial ledger": (
               "Who is on call, on what rota, and have they agreed to it?",
               "What recovery time is promised, and does the design in pillar 11 actually "
               "meet it? A tier and an RTO that disagree is the contradiction to surface "
               "now rather than during the incident."),
           "Tier 2 -- departmental analytics with a manual workaround": (
               "What is the workaround, and who knows how to run it?",),
       },
       informs=("alerting", "disaster_recovery"),
       forgotten=("A criticality tier is a claim about response, not about the architecture. "
                  "Do not state an availability figure the design cannot deliver.")),

    _p(17, 4, "alerting", "Alert routing, log retention and budget policy",
       "Who is told what, and how long are logs kept?",
       ("Three routes: on-call for crashes, a data-quality channel, a FinOps channel",
        "One consolidated topic",
        "External webhook to Slack or PagerDuty"),
       ("governance-observability",),
       depth={
           "*": (
               "Three questions, not one: who is paged when the job CRASHES, who is told "
               "when data quality DEGRADES, and who sees a BUDGET breach. Usually three "
               "different people, almost always collapsed into one topic.",
               "Log retention per log group, in days. CloudWatch defaults to never expire, "
               "which bills forever and was nobody's decision.",
               "The monthly budget, as a number. It is compared against the BCM forecast, "
               "and a guardrail below that forecast alarms on the mismatch rather than on "
               "overspend."),
       },
       forgotten=("Log retention. The default is never-expire, so the cost is silent, "
                  "permanent, and invisible until someone reads the bill.")),

    _p(18, 4, "proving", "Pre-production proving and the production gate",
       "How is this proven before it reaches production?",
       ("Live five-hop proof against real infrastructure",
        "Local no-cloud simulation only"),
       ("core/reporting/seed.py", "core/governance/plan_gate.py"),
       depth={
           "*": (
               "Who approves the production apply, and is one person enough?",
               "Is the approval bound to the exact plan hash, or to a description of it?",
               "What is the rollback, and has it been run?"),
       },
       forgotten="The rollback. It is written down far more often than it is executed."),
)

BY_KEY = {p["key"]: p for p in PILLARS}
BY_ID = {p["id"]: p for p in PILLARS}
PILLAR_IDS = tuple(p["id"] for p in PILLARS)
PILLAR_KEYS = tuple(p["key"] for p in PILLARS)

# Aliases for backwards compatibility with earlier documentation and prompts
ALIASES = {
    "runtime_dependencies": "runtime_packages",
}


# --- Putting it together ----------------------------------------------------------------

# The facts a derivation can consume. Anything not in here is a pillar CHOICE, not an input
# to arithmetic, and no amount of it will produce a worker count.
# The pillar 15 answer, mapped to the repository constant core/generation/cicd.py renders a
# publish stage for. Without this the interview records a preference that selects nothing.
ARTIFACT_REPO_BY_CHOICE = {
    "ECR and an immutable S3 artifact bucket, digest-verified": "ecr",
    "JFrog Artifactory": "artifactory",
    "AWS CodeArtifact": "codeartifact",
}


def artifact_repo_for(choice):
    """The cicd.py artifact repository a pillar 15 answer selects, or None."""
    return ARTIFACT_REPO_BY_CHOICE.get((choice or "").strip())


def artifact_promotion_plan(artifact_repo=None, immutable_tags=None, rebuild_per_env=None):
    """Whether the stated artifact setup can promote one build through environments.

    Build once, deploy many is a property of the whole chain, not of the repository. A
    mutable tag and a per-environment rebuild each break it on their own, and both are
    ordinary defaults rather than mistakes anyone makes deliberately.
    """
    if not artifact_repo:
        return _undetermined("no artifact repository was chosen")

    if artifact_repo not in ("ecr", "artifactory", "codeartifact", "s3"):
        return _undetermined(f"unknown artifact repository {artifact_repo!r}")

    result = {"determinable": True, "artifact_repo": artifact_repo}
    breaks = []

    if rebuild_per_env is True:
        breaks.append("the artifact is rebuilt per environment, so production runs a build "
                      "that staging never tested")
    if artifact_repo == "ecr" and immutable_tags is False:
        breaks.append("ECR tags are mutable, so the digest approved at the gate is not "
                      "necessarily the digest that runs")
        result["source"] = SOURCES["ecr_immutability"]
    if rebuild_per_env is None:
        return _undetermined("it is unstated whether the artifact is rebuilt per environment")

    result["verdict"] = "BREAKS_PROMOTION" if breaks else "PROMOTABLE"
    result["because"] = ("; ".join(breaks) if breaks else
                         "one build is published once and promoted by digest")
    return result


def consumer_access_plan(group_count=None, scopes_differ=None, all_attributed=None):
    """Whether splitting Gold access by consumer group buys least privilege or only labels.

    `security-iam-scoped` renders one policy per group. That is worth doing when the groups
    read different prefixes; when they all read the same one it is the same grant under
    several names, which reads as least privilege in an audit and is not.
    """
    if group_count in (None, ""):
        return _undetermined("the number of consumer groups was not stated")
    count = int(group_count)
    if count < 1:
        return _undetermined("a stack with no reader has no serving layer to scope")

    result = {"determinable": True, "group_count": count}

    if count == 1:
        result["verdict"] = "SINGLE_CONSUMER"
        result["because"] = "one group reads Gold; the scalar inputs cover it"
    elif scopes_differ is None:
        return _undetermined("it is unstated whether the groups read different prefixes")
    elif scopes_differ:
        result["verdict"] = "SCOPED"
        result["because"] = f"{count} groups read different prefixes, so a policy each is a real boundary"
    else:
        result["verdict"] = "SHARED_SCOPE"
        result["because"] = (f"{count} groups all read the same prefix, so per-group policies "
                             "label the access rather than narrow it")

    # Separate from the verdict: a correctly scoped split can still be unattributable, and an
    # unscoped one can still be billed properly. They are different failures.
    if all_attributed is False:
        result["attribution"] = "PARTIAL"
        result["attribution_because"] = ("at least one group carries no cost centre, so its "
                                         "spend cannot be separated in Cost Explorer")
    elif all_attributed is True:
        result["attribution"] = "COMPLETE"

    return result


FACT_KEYS = ("daily_gb", "partitions_per_day", "read_pattern", "transform_shape", "shuffle",
             "runs_per_day", "events_per_sec", "avg_record_kb", "has_spark_skills",
             "artifact_repo", "immutable_tags", "rebuild_per_env",
             "consumer_group_count", "consumer_scopes_differ", "consumers_all_attributed")


def derive(facts):
    """Compute every recommendation the stated facts support, and say what blocks the rest.

    Returns {pillar_key: result}, where a result either carries `determinable: True` and its
    arithmetic, or `determinable: False` and the fact that is missing. The second case is the
    useful one during an interview: it names the question to ask next, and it names it for a
    reason rather than because it came next in a list.
    """
    facts = {k: v for k, v in (facts or {}).items() if k in FACT_KEYS}
    out = {}

    out["ingestion_source"] = kinesis_shard_plan(
        facts.get("events_per_sec"), facts.get("avg_record_kb"))

    out["partitioning"] = object_size_plan(
        facts.get("daily_gb"), facts.get("partitions_per_day"),
        facts.get("read_pattern") or "mixed")

    out["compute_engine"] = engine_recommendation(
        facts.get("daily_gb"), facts.get("transform_shape"), facts.get("has_spark_skills"))

    out["worker_sizing"] = glue_worker_plan(
        facts.get("daily_gb"), facts.get("shuffle") or "wide",
        facts.get("runs_per_day") or DEFAULT_RUNS_PER_DAY)

    out["artifacts"] = artifact_promotion_plan(
        facts.get("artifact_repo"), facts.get("immutable_tags"),
        facts.get("rebuild_per_env"))

    out["serving"] = consumer_access_plan(
        facts.get("consumer_group_count"), facts.get("consumer_scopes_differ"),
        facts.get("consumers_all_attributed"))

    return out


def missing_facts(facts):
    """Which inputs are still unstated. The interview's own to-do list."""
    facts = facts or {}
    return [k for k in FACT_KEYS if facts.get(k) in (None, "")]


def next_pillar(answered=(), facts=None):
    """The next pillar to ask, enriched with whatever the answers so far already decide.

    `answered` is the set of pillar keys (or ids) already covered. Returns None when the
    interview is complete.
    """
    answered = {str(a) for a in (answered or ())}
    for pillar in PILLARS:
        if pillar["key"] in answered or str(pillar["id"]) in answered:
            continue
        return question_for(pillar["key"], facts)
    return None


def question_for(key, facts=None):
    """One pillar, rendered with its derived recommendation and its depth follow-ups."""
    actual_key = ALIASES.get(key, key)
    pillar = BY_KEY.get(actual_key) or BY_ID.get(actual_key)
    if not pillar:
        raise KeyError(f"no such pillar: {key!r}")

    rendered = {
        "id": pillar["id"],
        "key": pillar["key"],
        "phase": pillar["phase"],
        "phase_title": PHASES[pillar["phase"]],
        "title": pillar["title"],
        "question": pillar["question"],
        "options": list(pillar["options"]),
        "maps_to": list(pillar["maps_to"]),
        "informs": list(pillar["informs"]),
        "forgotten": pillar["forgotten"],
        "depth": depth_for(pillar["key"], None),
    }
    if pillar["derives"]:
        rendered["derived"] = derive(facts or {}).get(pillar["key"])
    return rendered


def depth_for(key, chosen_option):
    """The follow-ups that apply once an option is picked.

    A `"*"` entry applies whatever was chosen; an option-specific entry replaces it, because
    a follow-up that fits every answer belongs at the top level rather than in the depth of
    one branch.
    """
    actual_key = ALIASES.get(key, key)
    pillar = BY_KEY.get(actual_key) or BY_ID.get(actual_key)
    if not pillar:
        raise KeyError(f"no such pillar: {key!r}")
    depth = pillar["depth"] or {}
    if chosen_option and chosen_option in depth:
        return list(depth[chosen_option])
    if chosen_option is None:
        collected = []
        for value in depth.values():
            collected.extend(value)
        return collected
    return list(depth.get("*", ()))


# --- CLI ---------------------------------------------------------------------------------

def _facts_from(pairs):
    """Parse `key=value` arguments, keeping numbers numeric so the arithmetic works."""
    facts = {}
    for pair in pairs or ():
        if "=" not in pair:
            raise SystemExit(f"expected key=value, got {pair!r}")
        key, _, raw = pair.partition("=")
        key = key.strip()
        raw = raw.strip()
        if key not in FACT_KEYS:
            raise SystemExit(f"unknown fact {key!r}; known facts: {', '.join(FACT_KEYS)}")
        if raw.lower() in ("true", "false"):
            facts[key] = raw.lower() == "true"
        else:
            try:
                facts[key] = float(raw) if "." in raw else int(raw)
            except ValueError:
                facts[key] = raw
    return facts


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="The 18 enterprise pillars and the sizing they imply.")
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="Every pillar, by phase.")
    listing.add_argument("--json", action="store_true")

    show = sub.add_parser("show", help="One pillar, with its depth follow-ups.")
    show.add_argument("pillar", help="Pillar key or id.")
    show.add_argument("--option", help="Render only the depth for this chosen option.")
    show.add_argument("fact", nargs="*", help="key=value facts, e.g. daily_gb=50")
    show.add_argument("--json", action="store_true")

    nxt = sub.add_parser("next", help="The next pillar to ask.")
    nxt.add_argument("--answered", default="", help="Comma-separated pillar keys already asked.")
    nxt.add_argument("fact", nargs="*")
    nxt.add_argument("--json", action="store_true")

    derived = sub.add_parser("derive", help="Everything the stated facts already decide.")
    derived.add_argument("fact", nargs="*")
    derived.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "list":
        if args.json:
            print(json.dumps([{k: p[k] for k in ("id", "key", "phase", "title", "question",
                                                 "options", "maps_to", "forgotten")}
                              for p in PILLARS], indent=2))
            return 0
        for phase, title in PHASES.items():
            print(f"\nPhase {phase}: {title}")
            for pillar in PILLARS:
                if pillar["phase"] == phase:
                    print(f"  {pillar['id']:>2}. {pillar['title']}")
        return 0

    facts = _facts_from(getattr(args, "fact", []))

    if args.command == "show":
        rendered = question_for(args.pillar, facts)
        if args.option:
            rendered["depth"] = depth_for(args.pillar, args.option)
        if args.json:
            print(json.dumps(rendered, indent=2))
            return 0
        _print_question(rendered)
        return 0

    if args.command == "next":
        answered = [a for a in args.answered.split(",") if a.strip()]
        rendered = next_pillar(answered, facts)
        if rendered is None:
            print("All 18 pillars are answered.")
            return 0
        if args.json:
            print(json.dumps(rendered, indent=2))
            return 0
        _print_question(rendered)
        return 0

    if args.command == "derive":
        results = derive(facts)
        if args.json:
            print(json.dumps({"facts": facts, "derived": results,
                              "missing_facts": missing_facts(facts)}, indent=2))
            return 0
        for key, result in results.items():
            print(f"\n{key}")
            if result.get("determinable"):
                print(f"  {result.get('because')}")
            else:
                print(f"  not determinable: {result['reason']}")
        still = missing_facts(facts)
        if still:
            print(f"\nStill unstated: {', '.join(still)}")
        return 0

    return 1


def _print_question(rendered):
    print(f"\nPillar {rendered['id']} -- {rendered['title']}")
    print(f"Phase {rendered['phase']}: {rendered['phase_title']}")
    print(f"\n  {rendered['question']}\n")
    for option in rendered["options"]:
        print(f"  - {option}")
    derived = rendered.get("derived")
    if derived:
        print()
        if derived.get("determinable"):
            print(f"  Derived: {derived.get('because')}")
        else:
            print(f"  Cannot recommend yet: {derived['reason']}")
    if rendered["depth"]:
        print("\n  Then ask:")
        for item in rendered["depth"]:
            print(f"    - {item}")
    if rendered["forgotten"]:
        print(f"\n  Usually forgotten: {rendered['forgotten']}")
    if rendered["maps_to"]:
        print(f"\n  Maps to: {', '.join(rendered['maps_to'])}")


if __name__ == "__main__":
    sys.exit(main())
