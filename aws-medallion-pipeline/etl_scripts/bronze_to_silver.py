import sys
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.sql.functions import col, to_timestamp, current_timestamp, sha2

# Initialize Glue Context
args = getResolvedOptions(sys.argv, ['JOB_NAME', 'BRONZE_BUCKET', 'SILVER_BUCKET'])
sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session
job = Job(glueContext)
job.init(args['JOB_NAME'], args)

bronze_path = f"s3://{args['BRONZE_BUCKET']}/raw-data/"
silver_path = f"s3://{args['SILVER_BUCKET']}/cleaned-data/"

print(f"Reading raw data from Bronze bucket: {bronze_path}")
# Read JSON raw data
df_raw = spark.read.json(bronze_path)

if df_raw.count() == 0:
    print("No records found in Bronze bucket. Exiting job.")
    job.commit()
    sys.exit(0)

# Schema cleanup, transformation, & PII masking (e.g. hashing email)
# Example Schema: id, user_email, event_type, amount, event_time
df_cleaned = df_raw \
    .filter(col("id").isNotNull()) \
    .withColumn("amount", col("amount").cast("double")) \
    .withColumn("event_time", to_timestamp(col("event_time"), "yyyy-MM-dd'T'HH:mm:ss'Z'")) \
    .withColumn("hashed_email", sha2(col("user_email"), 256)) \
    .drop("user_email") \
    .withColumn("processed_timestamp", current_timestamp()) \
    .dropDuplicates(["id"])

print(f"Writing cleaned data to Silver bucket: {silver_path}")
# Write as Parquet partitioned by event type
df_cleaned.write \
    .mode("append") \
    .partitionBy("event_type") \
    .parquet(silver_path)

print("Bronze to Silver Spark job completed successfully.")
job.commit()
