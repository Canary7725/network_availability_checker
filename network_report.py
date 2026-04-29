import logging
import json
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


def build_network_request_body(carrier: str, network_name: str) -> Dict:
    return {
        "additionalValues": [],
        "carrier": [carrier],
        "report": "networkComparison",
        "homeValue": network_name,
        "reportType": "network",
        "totalWeightComparisonValue": {
            "IPPS": "309.1",
            "OPPS": "197.9",
            "PFS": "241.2",
            "DrugB": "152.7",
            "Anesthesia": "6.5",
            "CLFS": "12.6",
            "ASC": "19.2",
            "DMEPOS": "26.0",
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
            "IPPS": "332",
            "OPPS": "217.1",
            "PFS": "439",
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
        "consolidateIntoProfessional": False,
        "medicareImputedPercentageReportEnabled": True,
        "applyProviderWeight": True,
    }


def read_source_rows(source_file: str, dedupe_input: bool) -> List[Dict]:
    source_df = pd.read_csv(source_file, usecols=["carrier", "network_name"], dtype=str)
    source_df["carrier"] = source_df["carrier"].fillna("").str.strip()
    source_df["network_name"] = source_df["network_name"].fillna("").str.strip()
    source_df = source_df[(source_df["carrier"] != "") & (source_df["network_name"] != "")]

    if dedupe_input:
        source_df = source_df.drop_duplicates(subset=["carrier", "network_name"], keep="first")

    return source_df[["carrier", "network_name"]].to_dict("records")


def build_network_reports(detail_df):
    if detail_df.empty:
        empty_detail = pd.DataFrame(columns=DETAIL_COLUMNS)
        network_summary_df = pd.DataFrame(
            columns=[
                "carrier",
                "network_name",
                "total_checks",
                "failed_checks",
                "failed_medicare_type_array",
                "pass_rate_percent",
                "overall_available",
            ]
        )
        return empty_detail, empty_detail.copy(), network_summary_df

    failures_df = detail_df[detail_df["is_available"] == "False"].copy()

    network_summary_df = (
        detail_df.groupby(["carrier", "network_name"], as_index=False)
        .agg(
            total_checks=("is_available", "size"),
            failed_checks=("is_available", lambda s: (s == "False").sum()),
        )
        .sort_values(["carrier", "network_name"])
    )

    medicare_order = {medicare_type: idx for idx, medicare_type in enumerate(MEDICARE_TYPES)}
    failed_types_df = (
        detail_df[detail_df["is_available"] == "False"][["carrier", "network_name", "medicare_type"]]
        .drop_duplicates()
        .copy()
    )
    if not failed_types_df.empty:
        failed_types_df["sort_key"] = failed_types_df["medicare_type"].map(medicare_order).fillna(999)
        failed_types_df = failed_types_df.sort_values(["carrier", "network_name", "sort_key", "medicare_type"])
        failed_types_by_network = (
            failed_types_df.groupby(["carrier", "network_name"])["medicare_type"]
            .agg(",".join)
            .rename("failed_medicare_type_array")
            .reset_index()
        )
        network_summary_df = network_summary_df.merge(
            failed_types_by_network,
            on=["carrier", "network_name"],
            how="left",
        )
    else:
        network_summary_df["failed_medicare_type_array"] = ""

    network_summary_df["failed_medicare_type_array"] = network_summary_df["failed_medicare_type_array"].fillna("")
    network_summary_df["pass_rate_percent"] = (
        ((network_summary_df["total_checks"] - network_summary_df["failed_checks"]) / network_summary_df["total_checks"]) * 100
    ).round(2)
    network_summary_df["pass_rate_percent"] = network_summary_df["pass_rate_percent"].map(lambda v: f"{v:.2f}")
    network_summary_df["overall_available"] = network_summary_df["failed_checks"].eq(0).map({True: "True", False: "False"})

    return detail_df, failures_df, network_summary_df


def generate_network_report(config: Dict, env: str) -> str:
    secrets = config["uat_secrets"] if env == "uat" else config["dev_secrets"]
    source_file = get_file_path(config["file"]["source_file_path"])

    output_base = get_file_path(secrets["output_file_path"], is_source=False)
    xlsx_path = os.path.abspath(output_base)
    output_dir = os.path.dirname(xlsx_path) or os.getcwd()
    os.makedirs(output_dir, exist_ok=True)

    timeout_seconds = int(config.get("api", {}).get("timeout_seconds", 60))
    max_retries = int(config.get("api", {}).get("max_retries", 0))
    dedupe_enabled = bool(config.get("run", {}).get("dedupe_input", True))

    sheet_name_config = config.get("report", {}).get("sheet_names", {})
    sheet_names = {
        "detail": str(sheet_name_config.get("detail", "detail")),
        "failures": str(sheet_name_config.get("failures", "failures")),
        "network_summary": str(sheet_name_config.get("network_summary", "network_summary")),
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
            network_name = row["network_name"]
            payload = build_network_request_body(carrier, network_name)

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
                    "Carrier=%s | Network=%s | Status=%s | Data=%s",
                    carrier,
                    network_name,
                    response.status_code,
                    json.dumps(response_json.get("data"), default=str),
                )
                if response.status_code == 200:
                    normalized = normalize_data_rows(
                        response_json=response_json,
                        carrier=carrier,
                        network_name=network_name,
                        http_status=response.status_code,
                        elapsed_ms=elapsed_ms,
                    )
                else:
                    normalized = [
                        {
                            "carrier": carrier,
                            "network_name": network_name,
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
                    "Carrier=%s | Network=%s | RequestException=%s",
                    carrier,
                    network_name,
                    str(request_exception),
                )
                detail_rows.extend(
                    [
                        {
                            "carrier": carrier,
                            "network_name": network_name,
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

    detail_df = pd.DataFrame(detail_rows, columns=DETAIL_COLUMNS)
    detail_df, failures_df, network_summary_df = build_network_reports(detail_df)
    with pd.ExcelWriter(xlsx_path, mode="w") as writer:
        network_summary_df.to_excel(
            writer,
            sheet_name=sheet_names["network_summary"],
            index=False,
            columns=[
                "carrier",
                "network_name",
                "total_checks",
                "failed_checks",
                "failed_medicare_type_array",
                "pass_rate_percent",
                "overall_available",
            ],
        )
        failures_df.to_excel(writer, sheet_name=sheet_names["failures"], index=False, columns=DETAIL_COLUMNS)
        detail_df.to_excel(writer, sheet_name=sheet_names["detail"], index=False, columns=DETAIL_COLUMNS)

    print("Report written:")
    print(xlsx_path)
    return xlsx_path
