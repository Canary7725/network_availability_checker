import json
import logging
import os
from typing import Dict, List

import pandas as pd
import requests

from report_utils import (
    DETAIL_COLUMNS,
    MEDICARE_TYPES,
    METRIC_TYPES,
    execute_request_with_retries,
    get_file_path,
    normalize_data_rows,
)


def build_reporting_entity_request_body(carrier: str, reporting_entity_name: str) -> Dict:
    return {
        "additionalValues": [],
        "carrier": [carrier],
        "report": "networkComparison",
        "homeValue": reporting_entity_name,
        "reportType": "reportingEntityName",
        "totalWeightComparisonValue": {
            "IPPS": "241.3",
            "OPPS": "218.35",
            "PFS": "266.13",
            "DrugB": "168.48",
            "Anesthesia": "7.17",
            "CLFS": "13.9",
            "ASC": "21.18",
            "DMEPOS": "28.69",
        },
        "consolidatePercentageLimitMap": {
            "IPPS": {"lowerLimit": "70", "upperLimit": "1000"},
            "OPPS": {"lowerLimit": "70", "upperLimit": "1000"},
            "PFS": {"lowerLimit": "70", "upperLimit": "1000"},
            "DrugB": {"lowerLimit": "70", "upperLimit": "1000"},
            "Anesthesia": {"lowerLimit": "70", "upperLimit": "1000"},
            "CLFS": {"lowerLimit": "70", "upperLimit": "1000"},
            "ASC": {"lowerLimit": "70", "upperLimit": "1000"},
            "DMEPOS": {"lowerLimit": "70", "upperLimit": "1000"},
        },
        "consolidateMedicareWeight": {
            "IPPS": "241.3",
            "OPPS": "239.53",
            "PFS": "484.37",
        },
        "percentageLimitMap": {
            "IPPS": {"lowerLimit": "70", "upperLimit": "1000"},
            "OPPS": {"lowerLimit": "70", "upperLimit": "1000"},
            "PFS": {"lowerLimit": "70", "upperLimit": "1000"},
            "DrugB": {"lowerLimit": "70", "upperLimit": "1000"},
            "Anesthesia": {"lowerLimit": "70", "upperLimit": "1000"},
            "CLFS": {"lowerLimit": "70", "upperLimit": "1000"},
            "ASC": {"lowerLimit": "70", "upperLimit": "1000"},
            "DMEPOS": {"lowerLimit": "70", "upperLimit": "1000"},
        },
        "consolidateIntoProfessional": True,
        "medicareImputedPercentageReportEnabled": True,
        "applyProviderWeight": True,
    }


def read_source_rows(source_file: str, dedupe_input: bool) -> List[Dict]:
    source_df = pd.read_csv(source_file, usecols=["carrier", "reporting_entity_name"], dtype=str)
    source_df["carrier"] = source_df["carrier"].fillna("").str.strip()
    source_df["reporting_entity_name"] = source_df["reporting_entity_name"].fillna("").str.strip()
    source_df = source_df[(source_df["carrier"] != "") & (source_df["reporting_entity_name"] != "")]

    if dedupe_input:
        source_df = source_df.drop_duplicates(subset=["carrier", "reporting_entity_name"], keep="first")

    return source_df[["carrier", "reporting_entity_name"]].to_dict("records")


def build_reporting_entity_reports(detail_df: pd.DataFrame):
    if detail_df.empty:
        empty_detail = pd.DataFrame(columns=DETAIL_COLUMNS)
        summary_df = pd.DataFrame(
            columns=[
                "carrier",
                "reporting_entity_name",
                "total_checks",
                "failed_checks",
                "failed_medicare_type_array",
                "pass_rate_percent",
                "overall_available",
            ]
        )
        return empty_detail, empty_detail.copy(), summary_df

    failures_df = detail_df[detail_df["is_available"] == "False"].copy()

    summary_df = (
        detail_df.groupby(["carrier", "reporting_entity_name"], as_index=False)
        .agg(
            total_checks=("is_available", "size"),
            failed_checks=("is_available", lambda s: (s == "False").sum()),
        )
        .sort_values(["carrier", "reporting_entity_name"])
    )

    medicare_order = {medicare_type: idx for idx, medicare_type in enumerate(MEDICARE_TYPES)}
    failed_types_df = (
        detail_df[detail_df["is_available"] == "False"][["carrier", "reporting_entity_name", "medicare_type"]]
        .drop_duplicates()
        .copy()
    )
    if not failed_types_df.empty:
        failed_types_df["sort_key"] = failed_types_df["medicare_type"].map(medicare_order).fillna(999)
        failed_types_df = failed_types_df.sort_values(
            ["carrier", "reporting_entity_name", "sort_key", "medicare_type"]
        )
        failed_types_by_key = (
            failed_types_df.groupby(["carrier", "reporting_entity_name"])["medicare_type"]
            .agg(",".join)
            .rename("failed_medicare_type_array")
            .reset_index()
        )
        summary_df = summary_df.merge(
            failed_types_by_key,
            on=["carrier", "reporting_entity_name"],
            how="left",
        )
    else:
        summary_df["failed_medicare_type_array"] = ""

    summary_df["failed_medicare_type_array"] = summary_df["failed_medicare_type_array"].fillna("")
    summary_df["pass_rate_percent"] = (
        ((summary_df["total_checks"] - summary_df["failed_checks"]) / summary_df["total_checks"]) * 100
    ).round(2)
    summary_df["pass_rate_percent"] = summary_df["pass_rate_percent"].map(lambda v: f"{v:.2f}")
    summary_df["overall_available"] = summary_df["failed_checks"].eq(0).map({True: "True", False: "False"})

    return detail_df, failures_df, summary_df


def generate_reporting_entity_report(config: Dict, env: str) -> str:
    secrets = config["uat_secrets"] if env == "uat" else config["dev_secrets"]
    source_file = get_file_path(config["file"]["source_file_path"])

    output_file_name = secrets.get("reporting_entity_output_file_path", f"{env}_reporting_entity_report.xlsx")
    output_base = get_file_path(output_file_name, is_source=False)
    xlsx_path = os.path.abspath(output_base)
    output_dir = os.path.dirname(xlsx_path) or os.getcwd()
    os.makedirs(output_dir, exist_ok=True)

    timeout_seconds = int(config.get("api", {}).get("timeout_seconds", 60))
    max_retries = int(config.get("api", {}).get("max_retries", 0))
    dedupe_enabled = bool(config.get("run", {}).get("dedupe_input", True))

    sheet_name_config = config.get("report", {}).get("sheet_names", {})
    sheet_names = {
        "summary": str(sheet_name_config.get("reporting_entity_summary", "reporting_entity_summary")),
        "detail": str(sheet_name_config.get("detail", "detail")),
        "failures": str(sheet_name_config.get("failures", "failures")),
    }

    headers = {
        "X-Client-Key": secrets["client_key"],
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Role": "NetworkComparisonAccess",
    }

    source_rows = read_source_rows(source_file, dedupe_input=dedupe_enabled)
    detail_rows: List[Dict] = []
    api_url = secrets["url"]

    with requests.Session() as session:
        for row in source_rows:
            carrier = row["carrier"]
            reporting_entity_name = row["reporting_entity_name"]
            payload = build_reporting_entity_request_body(carrier, reporting_entity_name)

            response, response_json, elapsed_ms, request_exception = execute_request_with_retries(
                session=session,
                api_url=api_url,
                headers=headers,
                payload=payload,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            )

            if response is not None:
                logging.info(
                    "Carrier=%s | ReportingEntity=%s | Status=%s | Data=%s",
                    carrier,
                    reporting_entity_name,
                    response.status_code,
                    json.dumps(response_json.get("data"), default=str),
                )
                if response.status_code == 200:
                    normalized = normalize_data_rows(
                        response_json=response_json,
                        carrier=carrier,
                        network_name=reporting_entity_name,
                        http_status=response.status_code,
                        elapsed_ms=elapsed_ms,
                        response_key_candidates=[reporting_entity_name],
                        force_normalized_key_match=True,
                        missing_key_reason="response_key_missing",
                    )
                else:
                    normalized = [
                        {
                            "carrier": carrier,
                            "network_name": reporting_entity_name,
                            "metric_type": metric_type,
                            "medicare_type": medicare_type,
                            "raw_value": "",
                            "is_available": "False",
                            "reason": f"http_status_{response.status_code}",
                            "http_status": response.status_code,
                            "elapsed_ms": elapsed_ms,
                        }
                        for metric_type in METRIC_TYPES
                        for medicare_type in MEDICARE_TYPES
                    ]
                detail_rows.extend(normalized)
            else:
                logging.error(
                    "Carrier=%s | ReportingEntity=%s | RequestException=%s",
                    carrier,
                    reporting_entity_name,
                    str(request_exception),
                )
                detail_rows.extend(
                    [
                        {
                            "carrier": carrier,
                            "network_name": reporting_entity_name,
                            "metric_type": metric_type,
                            "medicare_type": medicare_type,
                            "raw_value": "",
                            "is_available": "False",
                            "reason": f"request_exception:{type(request_exception).__name__}",
                            "http_status": 0,
                            "elapsed_ms": 0,
                        }
                        for metric_type in METRIC_TYPES
                        for medicare_type in MEDICARE_TYPES
                    ]
                )

    detail_df = pd.DataFrame(detail_rows, columns=DETAIL_COLUMNS).rename(
        columns={"network_name": "reporting_entity_name"}
    )
    detail_df, failures_df, summary_df = build_reporting_entity_reports(detail_df)

    with pd.ExcelWriter(xlsx_path, mode="w") as writer:
        summary_df.to_excel(
            writer,
            sheet_name=sheet_names["summary"],
            index=False,
            columns=[
                "carrier",
                "reporting_entity_name",
                "total_checks",
                "failed_checks",
                "failed_medicare_type_array",
                "pass_rate_percent",
                "overall_available",
            ],
        )
        failures_df.to_excel(writer, sheet_name=sheet_names["failures"], index=False)
        detail_df.to_excel(writer, sheet_name=sheet_names["detail"], index=False)

    print("Report written:")
    print(xlsx_path)
    return xlsx_path
