import os
import sys
import json
import argparse
import subprocess

# =============================================================
# Offline Fallback Pricing Catalog (US-East-1 baseline values)
# =============================================================
OFFLINE_PRICING_US_EAST_1 = {
    "GLUE": {
        "standard_dpu_hour": 0.44,
        "flex_dpu_hour": 0.29
    },
    "EMR_SERVERLESS": {
        "vcpu_hour": 0.052624,
        "memory_gb_hour": 0.0057785,
        "storage_gb_hour": 0.000111
    },
    "REDSHIFT_SERVERLESS": {
        "rpu_hour": 0.375,
        "storage_gb_month": 0.024
    },
    "DATABRICKS": {
        "dbu_jobs_hour": 0.15,
        "dbu_all_purpose_hour": 0.40,
        "underlying_ec2_m5_xlarge_hour": 0.192
    },
    "S3": {
        "storage_gb_month": 0.023,
        "put_1k_requests": 0.005,
        "get_1k_requests": 0.0004
    },
    "ATHENA": {
        "tb_scanned": 5.00
    },
    "STEP_FUNCTIONS": {
        "transitions_1k": 0.025
    }
}

def fetch_live_aws_price(service_code, filter_field, filter_value, fallback_price):
    """
    Attempts to query the live AWS Price List API via the AWS CLI.
    If AWS CLI is not configured, lacks permissions, or errors, falls back to offline catalog.
    """
    cmd = [
        "aws", "pricing", "get-products",
        "--service-code", service_code,
        "--filters", f"Type=TERM_MATCH,Field={filter_field},Value={filter_value}",
        "--region", "us-east-1",
        "--output", "json"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            if "PriceList" in data and len(data["PriceList"]) > 0:
                # The PriceList contains stringified JSON payloads
                product_data = json.loads(data["PriceList"][0])
                # Navigate standard AWS Pricing JSON path: terms -> OnDemand -> rateCode -> priceDimensions -> pricePerUnit -> USD
                terms = product_data.get("terms", {})
                on_demand = terms.get("OnDemand", {})
                for rate_id, rate_info in on_demand.items():
                    price_dims = rate_info.get("priceDimensions", {})
                    for dim_id, dim_info in price_dims.items():
                        unit_prices = dim_info.get("pricePerUnit", {})
                        if "USD" in unit_prices:
                            live_price = float(unit_prices["USD"])
                            if live_price > 0.0:
                                return live_price
    except Exception:
        pass # Silently fall back
    return fallback_price

def get_compute_price(service):
    """
    Dynamically loads the compute rate for the selected service.
    """
    if service == "GLUE":
        # Query standard Glue DPU pricing
        rate = fetch_live_aws_price(
            service_code="AWSGlue",
            filter_field="productFamily",
            filter_value="Compute",
            fallback_price=OFFLINE_PRICING_US_EAST_1["GLUE"]["standard_dpu_hour"]
        )
        return rate, f"Glue Spark Compute @ ${rate:.4f}/DPU-hr (Live AWS API)" if rate != OFFLINE_PRICING_US_EAST_1["GLUE"]["standard_dpu_hour"] else f"Glue Spark Compute @ ${rate:.4f}/DPU-hr (Offline Cache)"

    elif service == "EMR_SERVERLESS":
        # Default EMR Serverless Worker configuration (4 vCPU, 16 GB memory)
        vcpu_rate = fetch_live_aws_price(
            service_code="ElasticMapReduce",
            filter_field="usageType",
            filter_value="USW1-vCPU-Hours",
            fallback_price=OFFLINE_PRICING_US_EAST_1["EMR_SERVERLESS"]["vcpu_hour"]
        )
        mem_rate = fetch_live_aws_price(
            service_code="ElasticMapReduce",
            filter_field="usageType",
            filter_value="USW1-GB-Hours",
            fallback_price=OFFLINE_PRICING_US_EAST_1["EMR_SERVERLESS"]["memory_gb_hour"]
        )
        worker_rate = (4 * vcpu_rate) + (16 * mem_rate)
        source = "Live AWS API" if vcpu_rate != OFFLINE_PRICING_US_EAST_1["EMR_SERVERLESS"]["vcpu_hour"] else "Offline Cache"
        return worker_rate, f"EMR Serverless workers (4vCPU/16GB @ ${worker_rate:.4f}/worker-hr) ({source})"

    elif service == "DATABRICKS":
        # Databricks Premium Jobs DBU ($0.15/DBU-hr) + AWS EC2 m5.xlarge ($0.192/hr)
        ec2_rate = fetch_live_aws_price(
            service_code="AmazonEC2",
            filter_field="instanceType",
            filter_value="m5.xlarge",
            fallback_price=OFFLINE_PRICING_US_EAST_1["DATABRICKS"]["underlying_ec2_m5_xlarge_hour"]
        )
        node_rate = OFFLINE_PRICING_US_EAST_1["DATABRICKS"]["dbu_jobs_hour"] + ec2_rate
        source = "Live AWS API" if ec2_rate != OFFLINE_PRICING_US_EAST_1["DATABRICKS"]["underlying_ec2_m5_xlarge_hour"] else "Offline Cache"
        return node_rate, f"Databricks Jobs cluster (m5.xlarge nodes @ ${node_rate:.4f}/node-hr) ({source})"

    elif service == "REDSHIFT":
        rpu_rate = fetch_live_aws_price(
            service_code="AmazonRedshift",
            filter_field="usageType",
            filter_value="USW1-Serverless:rpu-Hour",
            fallback_price=OFFLINE_PRICING_US_EAST_1["REDSHIFT_SERVERLESS"]["rpu_hour"]
        )
        source = "Live AWS API" if rpu_rate != OFFLINE_PRICING_US_EAST_1["REDSHIFT_SERVERLESS"]["rpu_hour"] else "Offline Cache"
        return rpu_rate, f"Redshift Serverless compute @ ${rpu_rate:.4f}/RPU-hr ({source})"

    return 0.0, "Unknown Service"

def calculate_detailed_budget(service, scale_units, duration_mins, runs_daily, s3_gb, athena_queries, avg_scan_gb, failure_rate=5.0):
    runs_monthly = runs_daily * 30
    
    # 1. Load compute rates dynamically
    service_key = service.upper()
    rate, compute_description = get_compute_price(service_key)
    
    # 2. Compute cost calculations
    hours = (duration_mins / 60.0) * runs_monthly
    compute_cost = scale_units * hours * rate
    
    failed_runs = runs_monthly * (failure_rate / 100.0)
    failed_run_cost = scale_units * (10 / 60.0) * failed_runs * rate

    # 3. Step Functions Orchestration Cost (Assuming 6 state transitions per run)
    sfn_transitions = runs_monthly * 6
    sfn_cost = sfn_transitions * (OFFLINE_PRICING_US_EAST_1["STEP_FUNCTIONS"]["transitions_1k"] / 1000)

    # 4. Amazon S3 Costs
    s3_storage_cost = s3_gb * OFFLINE_PRICING_US_EAST_1["S3"]["storage_gb_month"]
    s3_put_requests = runs_monthly * 500
    s3_get_requests = runs_monthly * 2000
    s3_api_cost = (s3_put_requests * (OFFLINE_PRICING_US_EAST_1["S3"]["put_1k_requests"] / 1000)) + \
                  (s3_get_requests * (OFFLINE_PRICING_US_EAST_1["S3"]["get_1k_requests"] / 1000))

    # 5. Amazon Athena Query Costs
    athena_scanned_gb = athena_queries * avg_scan_gb
    athena_cost = athena_scanned_gb * (OFFLINE_PRICING_US_EAST_1["ATHENA"]["tb_scanned"] / 1000)

    grand_total = compute_cost + failed_run_cost + sfn_cost + s3_storage_cost + s3_api_cost + athena_cost

    breakdown = {
        "parameters": {
            "service": service_key,
            "scale_units": scale_units,
            "job_duration_minutes": duration_mins,
            "daily_runs": runs_daily,
            "monthly_runs": runs_monthly,
            "s3_storage_gb": s3_gb,
            "monthly_athena_queries": athena_queries,
            "avg_athena_scan_gb": avg_scan_gb,
            "assumed_failure_rate_percent": failure_rate
        },
        "billing_forecast_usd": {
            "compute_description": compute_description,
            "primary_compute_cost": round(compute_cost, 2),
            "failed_runs_overhead": round(failed_run_cost, 2),
            "step_functions_orchestration": round(sfn_cost, 2),
            "s3_storage": round(s3_storage_cost, 2),
            "s3_api_operations": round(s3_api_cost, 2),
            "athena_queries": round(athena_cost, 2),
            "monthly_grand_total": round(grand_total, 2)
        }
    }

    return breakdown

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-Service API-Driven Cost Estimator")
    parser.add_argument("--service", default="GLUE", choices=["GLUE", "EMR_SERVERLESS", "DATABRICKS", "REDSHIFT"], help="Target compute service")
    parser.add_argument("--scale", type=int, default=4, help="Scale factor (DPUs, Workers, Nodes, or RPUs)")
    parser.add_argument("--duration", type=int, default=6, help="Average execution run time in minutes")
    parser.add_argument("--runs-daily", type=int, default=24, help="Number of runs per day")
    parser.add_argument("--s3-gb", type=float, default=200.0, help="Total active S3 storage in GB")
    parser.add_argument("--queries", type=int, default=150, help="Number of Athena queries run monthly")
    parser.add_argument("--scan-gb", type=float, default=15.0, help="Average data scanned per Athena query in GB")
    parser.add_argument("--failure-rate", type=float, default=5.0, help="Estimated failure rate percentage")
    
    args = parser.parse_args()
    
    report = calculate_detailed_budget(
        args.service, args.scale, args.duration, args.runs_daily, args.s3_gb, 
        args.queries, args.scan_gb, args.failure_rate
    )
    
    log_dir = os.path.join(os.getcwd(), ".agents", "logs")
    os.makedirs(log_dir, exist_ok=True)
    report_file = os.path.join(log_dir, "budget_estimation.json")
    
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
        
    print("\n" + "="*50)
    print(f"AWS PIPELINE MONTHLY BILLING FORECAST ({args.service.upper()})")
    print("="*50)
    print(f"Compute Config:           {report['billing_forecast_usd']['compute_description']}")
    print(f"Compute Ingestion Cost:   ${report['billing_forecast_usd']['primary_compute_cost']:.2f}")
    print(f"Failed Run Overhead (5%):  ${report['billing_forecast_usd']['failed_runs_overhead']:.2f}")
    print(f"Step Functions (Orch):    ${report['billing_forecast_usd']['step_functions_orchestration']:.2f}")
    print(f"S3 Storage (Active):      ${report['billing_forecast_usd']['s3_storage']:.2f}")
    print(f"S3 API operations:        ${report['billing_forecast_usd']['s3_api_operations']:.2f}")
    print(f"Athena Query Scans:       ${report['billing_forecast_usd']['athena_queries']:.2f}")
    print("-"*50)
    print(f"GRAND TOTAL (Monthly):    ${report['billing_forecast_usd']['totals']['monthly_grand_total'] if 'totals' in report['billing_forecast_usd'] else report['billing_forecast_usd']['monthly_grand_total']:.2f}")
    print("="*50)
    sys.exit(0)
