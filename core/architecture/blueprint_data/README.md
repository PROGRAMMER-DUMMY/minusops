# blueprint_data

Thirty-five published data-pipeline reference architectures, recorded as evidence about what
a data pipeline is usually made of.

## What this is for

`architecture_model._RULES` decides which layer every resource lands in, and therefore which
band it draws in and what its label says. Those rules were written from what we happened to
have built. The corpus is the check on that: a service that a third of the published
architectures name and `classify_role` cannot place ends up in the `other` layer, in the
footer beside the IAM roles, and nothing anywhere says so.

That is not hypothetical. `aws_vpc` is named in 10 of the 35, and the needles guarding it
were `_vpc` and `vpc_` -- both require a delimiter a bare `aws_vpc` does not have. Nor did
any rule mention a NAT gateway, an internet gateway, a route table or an elastic IP. Every
resource `modules/networking-vpc` creates classified as `other`. The corpus made that a test
failure instead of something to notice later.

## Files

| | |
| :--- | :--- |
| [`sources.json`](./sources.json) | The curated list: key, provider, title, URL. Edited by hand. |
| [`blueprints.json`](./blueprints.json) | What the fetch found, per source. Generated. |
| [`refresh.py`](./refresh.py) | Fetches every source and rewrites `blueprints.json`. |

```
python core/architecture/blueprint_data/refresh.py            # all sources
python core/architecture/blueprint_data/refresh.py aws-ara-data-lake   # one
```

Run by a person, never by the suite. A test that fetched its own reference would fail
offline, and a corpus that refreshed itself would stop being the fixed thing the classifier
is measured against.

## What is stored, and what is not

Per source: the service names the page mentions and how often, the medallion stage words it
uses, and the page's character count. **No page text and no images.** These pages belong to
AWS, Databricks, dbt Labs and the Apache Iceberg project; a count of which service names
appear where is a fact about the corpus rather than a copy of it, and
[`tests/test_blueprint_corpus.py`](../../../tests/test_blueprint_corpus.py) asserts the
stored shape stays that narrow.

## What the corpus is not

It is not an authority on how to build anything, and nothing in it is a source of a
recommendation. Extraction is keyword counting over prose, so a mention is evidence that a
page discusses a service, not evidence of a hop between two of them. Declared hops still come
only from a Terraform plan's own data-carrying arguments -- see
[`discover_data_edges`](../../reporting/drawio_generator.py).

Three sources currently 404 and are recorded in `blueprints.json` under `failed` rather than
dropped, so a URL that rots is visible instead of quietly shrinking the corpus.
