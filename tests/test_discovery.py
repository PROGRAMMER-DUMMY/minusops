import discovery


def test_terraform_urls_strip_aws_prefix():
    assert discovery.terraform_resource_url("aws_glue_job").endswith("/resources/glue_job")
    assert discovery.terraform_datasource_url("aws_caller_identity").endswith("/data-sources/caller_identity")


def test_awscli_and_pricing_url_shape():
    assert discovery.awscli_url("s3api", "head-bucket").endswith("/reference/s3api/head-bucket.html")
    assert "AWSGlue/current/index.json" in discovery.pricing_index_url("AWSGlue")


def test_research_record_carries_sources_and_resolution_order():
    rec = discovery.research_record("mwaa airflow", resource_types=["aws_mwaa_environment"],
                                    service_codes=["AmazonMWAA"])
    assert rec["topic"] == "mwaa airflow"
    assert "aws_mwaa_environment" in rec["sources"]["terraform_resources"]
    assert rec["sources"]["well_architected"]
    assert any("in-repo pattern" in step for step in rec["resolution_order"])


def test_record_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery, "CACHE_DIR", str(tmp_path / "research"))
    rec = discovery.research_record("kinesis speed layer", resource_types=["aws_kinesis_stream"])
    path = discovery.save_record(rec)
    assert path.endswith("kinesis-speed-layer.json")
    assert discovery.load_record("kinesis speed layer")["topic"] == "kinesis speed layer"
    assert discovery.load_record("never saved") is None
