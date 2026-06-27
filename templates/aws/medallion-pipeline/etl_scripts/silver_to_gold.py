import sys
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.sql.functions import col, sum, count, date_format, current_timestamp

# Initialize Glue Context
args = getResolvedOptions(sys.argv, ['JOB_NAME', 'SILVER_BUCKET', 'GOLD_BUCKET'])
sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session
job = Job(glueContext)
job.init(args['JOB_NAME'], args)

silver_path = f"s3://{args['SILVER_BUCKET']}/cleaned-data/"
gold_path = f"s3://{args['GOLD_BUCKET']}/business-aggregations/"

print(f"Reading cleaned data from Silver bucket: {silver_path}")
# Read Parquet clean data
df_silver = spark.read.parquet(silver_path)

if df_silver.count() == 0:
    print("No records found in Silver bucket. Exiting job.")
    job.commit()
    sys.exit(0)

# Create daily aggregated reporting view (Gold Layer)
# Aggregates amount and count grouped by event type and event date
df_gold = df_silver \
    .withColumn("event_date", date_format(col("event_time"), "yyyy-MM-dd")) \
    .groupBy("event_date", "event_type") \
    .agg(
        sum("amount").alias("total_revenue"),
        count("id").alias("total_events")
    ) \
    .withColumn("aggregated_at", current_timestamp())

print(f"Writing business analytics data to Gold bucket: {gold_path}")
# Write as Parquet
df_gold.write \
    .mode("overwrite") \
    .parquet(gold_path)

print("Silver to Gold Spark job completed successfully.")
job.commit()
