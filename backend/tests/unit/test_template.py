"""Policy-as-code: the template's security and reliability rules, enforced as tests.

cfn-lint checks that the template is valid CloudFormation; these tests check that it is the
template we designed (least privilege, no public data, retry maths, free-tier limits).
"""

from __future__ import annotations

import pytest
from cfnlint.decode import decode_str

from etl_processor.jobs import DEFAULT_LEASE_SECONDS
from tests.conftest import ROOT

TEMPLATE, _ = decode_str((ROOT / "template.yaml").read_text(encoding="utf-8"))
RES = TEMPLATE["Resources"]


def props(name: str) -> dict:
    return RES[name]["Properties"]


def statements(document: dict) -> list[dict]:
    """Flatten statements, resolving Fn::If [cond, statement, NoValue] to the statement."""
    out = []
    for st in document["Statement"]:
        if "Fn::If" in st:
            st = st["Fn::If"][1]
        out.append(st)
    return out


def as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def resource_text(resource) -> str:
    if isinstance(resource, dict):
        if "Fn::Sub" in resource:
            sub = resource["Fn::Sub"]
            return sub if isinstance(sub, str) else sub[0]
        if "Fn::GetAtt" in resource:
            return "GetAtt:" + ".".join(as_list(resource["Fn::GetAtt"]))
    return str(resource)


LAMBDA_STATEMENTS = statements(props("EtlProcessorRole")["Policies"][0]["PolicyDocument"])
INGESTER_STATEMENTS = statements(props("IngesterRole")["Policies"][0]["PolicyDocument"])
API_STATEMENTS = statements(props("ApiRole")["Policies"][0]["PolicyDocument"])
ALL_STATEMENTS = LAMBDA_STATEMENTS + INGESTER_STATEMENTS + API_STATEMENTS + statements(
    props("JobQueuePolicy")["PolicyDocument"])


def lambda_actions() -> dict[str, list[str]]:
    return {a: [resource_text(r) for r in as_list(st["Resource"])]
            for st in LAMBDA_STATEMENTS for a in as_list(st["Action"])}


# ----------------------------------------------------------------------------- IAM

@pytest.mark.parametrize("statement", ALL_STATEMENTS, ids=lambda s: s.get("Sid", "?"))
def test_no_wildcard_actions_or_resources(statement):
    for action in as_list(statement["Action"]):
        assert "*" not in action, f"wildcard action {action}"
    for resource in as_list(statement["Resource"]):
        text = resource_text(resource)
        assert text != "*" and not text.endswith(":*:*"), f"wildcard resource {text}"
        if text.startswith("arn:aws:s3:::") and "/" in text:
            prefix = text.split("/", 1)[1]
            assert prefix != "*", "S3 object permissions must be scoped to a prefix"


def test_lambda_role_is_least_privilege():
    actions = lambda_actions()
    assert set(actions) == {
        "logs:CreateLogStream", "logs:PutLogEvents",
        "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes",
        "sqs:ChangeMessageVisibility",
        "s3:GetObject", "s3:GetObjectVersion", "s3:PutObject",
        "dynamodb:GetItem", "dynamodb:UpdateItem",
    }
    assert all(r.endswith("/raw/*") for r in actions["s3:GetObject"])
    assert sorted(r.rsplit("/", 2)[-2] for r in actions["s3:PutObject"]) == [
        "curated", "processed", "rejected", "reports"]
    assert actions["dynamodb:UpdateItem"] == ["GetAtt:JobsTable.Arn"]
    assert "ManagedPolicyArns" not in props("EtlProcessorRole")  # no broad AWS managed policies


def test_lambda_cannot_read_its_own_outputs_or_delete():
    reads = lambda_actions()["s3:GetObject"]
    assert not any(p in r for r in reads for p in ("processed/", "reports/", "rejected/"))
    all_actions = {a for st in LAMBDA_STATEMENTS for a in as_list(st["Action"])}
    assert not any("Delete" in a and a.startswith("s3:") for a in all_actions)


def test_public_api_role_is_strictly_read_only():
    actions = {a for st in API_STATEMENTS for a in as_list(st["Action"])}
    writes = [(a, resource_text(r)) for st in API_STATEMENTS for a in as_list(st["Action"])
              for r in as_list(st["Resource"]) if a == "s3:PutObject"]
    assert [r.rsplit("/", 2)[-2] for _, r in writes] == ["athena-results"]  # Athena's own output
    assert not actions & {"dynamodb:Scan", "dynamodb:PutItem", "dynamodb:UpdateItem",
                          "dynamodb:DeleteItem", "sqs:SendMessage", "sqs:DeleteMessage",
                          "sqs:ReceiveMessage", "s3:DeleteObject", "lambda:InvokeFunction"}
    reads = [resource_text(r) for st in API_STATEMENTS if "s3:GetObject" in as_list(st["Action"])
             for r in as_list(st["Resource"])]
    assert not any("/raw/" in r or "/processed/" in r for r in reads)


def test_public_api_only_exposes_get_and_is_throttled():
    event = props("ApiFunction")["Events"]["ApiRoutes"]["Properties"]
    assert (event["Method"], event["Path"]) == ("GET", "/api/{proxy+}")
    throttle = props("HttpApi")["DefaultRouteSettings"]
    assert throttle["ThrottlingRateLimit"] <= 10 and throttle["ThrottlingBurstLimit"] <= 20


def test_site_proxies_api_same_origin_then_spa_routes():
    rules = props("SiteApp")["CustomRules"]
    api, spa = rules[0], rules[1]  # order matters: the API rule must win over the SPA rule
    assert api["Source"] == "/api/<*>" and api["Status"] == "200"
    assert api["Target"]["Fn::Sub"].startswith("https://${HttpApi}.execute-api.")
    assert spa["Target"] == "/index.html" and spa["Status"] == "200"
    headers = props("SiteApp")["CustomHeaders"]
    assert "Strict-Transport-Security" in headers and "immutable" in headers


def test_site_branch_is_deployed_manually():
    branch = props("SiteBranch")
    assert branch["EnableAutoBuild"] is False and branch["Stage"] == "PRODUCTION"
    assert not any(r["Type"].startswith("AWS::CloudFront::") for r in RES.values())


def test_conditioned_list_bucket_does_not_swallow_other_actions():
    """An s3:prefix condition denies any action that has no prefix (e.g. GetBucketLocation)."""
    for st in API_STATEMENTS:
        if "s3:prefix" in str(st.get("Condition", {})):
            assert as_list(st["Action"]) == ["s3:ListBucket"]


def test_ingester_can_only_touch_raw():
    actions = {a: [resource_text(r) for r in as_list(st["Resource"])]
               for st in INGESTER_STATEMENTS for a in as_list(st["Action"])}
    assert set(actions) == {"logs:CreateLogStream", "logs:PutLogEvents", "s3:GetObject",
                            "s3:PutObject"}
    assert all(r.endswith("/raw/*") for r in actions["s3:GetObject"] + actions["s3:PutObject"])


def test_ingestion_is_scheduled_and_alarmed():
    event = props("DatasetIngesterFunction")["Events"]["DailyIngest"]
    assert event["Type"] == "ScheduleV2"
    assert event["Properties"]["ScheduleExpression"] == {"Ref": "IngestSchedule"}
    assert TEMPLATE["Parameters"]["IngestSchedule"]["Default"] == "cron(30 6 * * ? *)"
    alarm = props("IngesterErrorsAlarm")
    assert alarm["Dimensions"][0]["Value"] == {"Ref": "DatasetIngesterFunction"}


def test_queue_policy_only_trusts_this_bucket_and_account():
    [st] = statements(props("JobQueuePolicy")["PolicyDocument"])
    assert st["Principal"] == {"Service": "s3.amazonaws.com"}
    assert st["Action"] == "sqs:SendMessage"
    assert "aws:SourceArn" in st["Condition"]["ArnLike"]
    assert "aws:SourceAccount" in st["Condition"]["StringEquals"]


# ----------------------------------------------------------------------------- data protection

def test_bucket_is_private_encrypted_and_versioned():
    bucket = props("DataBucket")
    assert all(bucket["PublicAccessBlockConfiguration"].values())
    assert len(bucket["PublicAccessBlockConfiguration"]) == 4
    assert bucket["VersioningConfiguration"]["Status"] == "Enabled"
    sse = bucket["BucketEncryption"]["ServerSideEncryptionConfiguration"][0]
    assert sse["ServerSideEncryptionByDefault"]["SSEAlgorithm"] == "AES256"
    assert bucket["OwnershipControls"]["Rules"][0]["ObjectOwnership"] == "BucketOwnerEnforced"


def test_lifecycle_never_expires_raw_data():
    rules = props("DataBucket")["LifecycleConfiguration"]["Rules"]
    expiring = {r.get("Prefix") for r in rules if "ExpirationInDays" in r}
    assert expiring == {"rejected/", "reports/", "athena-results/"}


def test_notification_cannot_retrigger_on_outputs():
    [config] = props("DataBucket")["NotificationConfiguration"]["QueueConfigurations"]
    rules = {r["Name"]: r["Value"] for r in config["Filter"]["S3Key"]["Rules"]}
    assert rules == {"prefix": "raw/", "suffix": ".csv"}


def test_queues_are_encrypted():
    assert props("JobQueue")["SqsManagedSseEnabled"] is True
    assert props("JobDLQ")["SqsManagedSseEnabled"] is True


# ----------------------------------------------------------------------------- reliability

def test_retry_timing_is_consistent():
    timeout = props("EtlProcessorFunction")["Timeout"]
    visibility = props("JobQueue")["VisibilityTimeout"]
    assert visibility >= 6 * timeout, "AWS guidance: visibility >= 6x the function timeout"
    assert timeout < DEFAULT_LEASE_SECONDS < visibility, (
        "lease must outlive one attempt and expire before SQS redelivers")


def test_event_source_mapping():
    event = props("EtlProcessorFunction")["Events"]["JobQueueEvent"]["Properties"]
    assert event["BatchSize"] == 1
    assert event["FunctionResponseTypes"] == ["ReportBatchItemFailures"]
    assert event["ScalingConfig"]["MaximumConcurrency"] == 2


def test_handler_knows_the_redrive_threshold():
    env = props("EtlProcessorFunction")["Environment"]["Variables"]
    redrive = props("JobQueue")["RedrivePolicy"]
    assert env["MAX_RECEIVE_COUNT"] == redrive["maxReceiveCount"] == {"Ref": "MaxReceiveCount"}


def test_every_alarm_notifies():
    alarms = [r for r in RES.values() if r["Type"] == "AWS::CloudWatch::Alarm"]
    assert len(alarms) == 4  # processor errors, DLQ depth, queue age, ingester errors
    for alarm in alarms:
        p = alarm["Properties"]
        assert p["AlarmActions"] == [{"Ref": "AlertsTopic"}]
        assert p["OKActions"] == [{"Ref": "AlertsTopic"}]
        assert p["TreatMissingData"] == "notBreaching"


# ----------------------------------------------------------------------------- free plan

def test_stays_inside_always_free_limits():
    table = props("JobsTable")
    capacity = [table["ProvisionedThroughput"]] + [
        g["ProvisionedThroughput"] for g in table["GlobalSecondaryIndexes"]]
    assert table["BillingMode"] == "PROVISIONED"
    assert sum(c["ReadCapacityUnits"] for c in capacity) <= 25
    assert sum(c["WriteCapacityUnits"] for c in capacity) <= 25
    assert props("EtlProcessorLogGroup")["RetentionInDays"] == 14
    assert props("IngesterLogGroup")["RetentionInDays"] == 14
    assert props("ApiLogGroup")["RetentionInDays"] == 14
    types = {r["Type"] for r in RES.values()}
    assert not types & {"AWS::EC2::NatGateway", "AWS::EC2::VPCEndpoint", "AWS::EC2::Instance",
                        "AWS::RDS::DBInstance", "AWS::Glue::Crawler"}
    assert sum(t == "AWS::CloudWatch::Dashboard" for t in
               (r["Type"] for r in RES.values())) == 1


def test_athena_scan_is_capped():
    config = props("AthenaWorkgroup")["WorkGroupConfiguration"]
    assert config["EnforceWorkGroupConfiguration"] is True
    assert config["BytesScannedCutoffPerQuery"] == 100 * 1024 * 1024


def test_glue_tables_use_partition_projection():
    for name in ("SalesTable", "SalesCsvTable"):
        params = props(name)["TableInput"]["Parameters"]
        assert params["projection.enabled"] == "true"
        assert "${!ingest_date}" in params["storage.location.template"]["Fn::Sub"]
