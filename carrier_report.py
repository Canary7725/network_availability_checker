import logging
import os
from typing import Dict, List

import pandas as pd
import requests

from report_utils import (
    CARRIER_DETAIL_COLUMNS,
    DETAIL_COLUMNS,
    MEDICARE_TYPES,
    METRIC_TYPES,
    execute_request_with_retries,
    get_file_path,
    normalize_data_rows,
)


def build_carrier_request_body(carrier: str) -> Dict:
    return {
        "additionalValues": [],
        "carrier": [carrier],
        "report": "networkComparison",
        "homeValue": carrier,
        "reportType": "carrier",
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
            "IPPS": "309.1",
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
        "consolidateIntoProfessional": True,
        "medicareImputedPercentageReportEnabled": True,
        "applyProviderWeight": True,
    }


def read_unique_carriers(source_file: str) -> List[str]:
    source_df = pd.read_csv(source_file, usecols=["carrier"], dtype=str)
    source_df["carrier"] = source_df["carrier"].fillna("").str.strip()
    source_df = source_df[source_df["carrier"] != ""]
    return source_df["carrier"].drop_duplicates().tolist()


def get_carrier_response_key_candidates(response_json: Dict, carrier: str) -> List[str]:
    candidates = [carrier]
    index_display_name_pair = response_json.get("indexDisplayNamePair") or {}
    if isinstance(index_display_name_pair, dict):
        for display_name, internal_name in index_display_name_pair.items():
            if str(internal_name) == carrier:
                candidates.append(str(display_name))

    deduped = []
    seen = set()
    for key in candidates:
        if key not in seen:
            deduped.append(key)
            seen.add(key)
    return deduped


def build_carrier_reports(detail_df):
    if detail_df.empty:
        empty_detail = pd.DataFrame(columns=CARRIER_DETAIL_COLUMNS)
        carrier_summary_df = pd.DataFrame(
            columns=[
                "carrier",
                "total_checks",
                "failed_checks",
                "failed_medicare_type_array",
                "pass_rate_percentage",
            ]
        )
        return empty_detail, empty_detail.copy(), carrier_summary_df

    carrier_detail_df = detail_df[CARRIER_DETAIL_COLUMNS].copy()
    failures_df = carrier_detail_df[carrier_detail_df["is_available"] == "False"].copy()

    carrier_summary_df = (
        carrier_detail_df.groupby("carrier", as_index=False)
        .agg(
            total_checks=("is_available", "size"),
            failed_checks=("is_available", lambda s: (s == "False").sum()),
        )
        .sort_values(["carrier"])
    )

    medicare_order = {medicare_type: idx for idx, medicare_type in enumerate(MEDICARE_TYPES)}
    failed_types_df = (
        carrier_detail_df[carrier_detail_df["is_available"] == "False"][["carrier", "medicare_type"]]
        .drop_duplicates()
        .copy()
    )
    if not failed_types_df.empty:
        failed_types_df["sort_key"] = failed_types_df["medicare_type"].map(medicare_order).fillna(999)
        failed_types_df = failed_types_df.sort_values(["carrier", "sort_key", "medicare_type"])
        failed_types_by_carrier = (
            failed_types_df.groupby("carrier")["medicare_type"]
            .agg(",".join)
            .rename("failed_medicare_type_array")
            .reset_index()
        )
        carrier_summary_df = carrier_summary_df.merge(
            failed_types_by_carrier,
            on="carrier",
            how="left",
        )
    else:
        carrier_summary_df["failed_medicare_type_array"] = ""

    carrier_summary_df["failed_medicare_type_array"] = carrier_summary_df["failed_medicare_type_array"].fillna("")
    carrier_summary_df["pass_rate_percentage"] = (
        ((carrier_summary_df["total_checks"] - carrier_summary_df["failed_checks"]) / carrier_summary_df["total_checks"]) * 100
    ).round(2)
    carrier_summary_df["pass_rate_percentage"] = carrier_summary_df["pass_rate_percentage"].map(lambda v: f"{v:.2f}")

    return carrier_detail_df, failures_df, carrier_summary_df


def generate_carrier_report(config: Dict, env: str) -> str:
    secrets = config["uat_secrets"] if env == "uat" else config["dev_secrets"]
    source_file = get_file_path(config["file"]["source_file_path"])

    output_file_name = secrets.get("carrier_output_file_path", f"{env}_carrier_report.xlsx")
    output_base = get_file_path(output_file_name, is_source=False)
    xlsx_path = os.path.abspath(output_base)
    output_dir = os.path.dirname(xlsx_path) or os.getcwd()
    os.makedirs(output_dir, exist_ok=True)

    timeout_seconds = int(config.get("api", {}).get("timeout_seconds", 60))
    max_retries = int(config.get("api", {}).get("max_retries", 0))

    sheet_name_config = config.get("report", {}).get("sheet_names", {})
    sheet_names = {
        "carrier_summary": str(sheet_name_config.get("carrier_summary", "carrier_summary")),
        "detail": str(sheet_name_config.get("detail", "detail")),
        "failures": str(sheet_name_config.get("failures", "failures")),
    }

    headers = {
        "X-Client-Key": secrets["client_key"],
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Role": "NetworkComparisonAccess",
    }

    source_rows = [{"carrier": carrier, "network_name": carrier} for carrier in read_unique_carriers(source_file)]

    detail_rows: List[Dict] = []
    api_url = secrets["url"]

    with requests.Session() as session:
        for row in source_rows:
            carrier = row["carrier"]
            network_name = row["network_name"]
            payload = build_carrier_request_body(carrier)

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
                    str(response_json.get("data")),
                )
                if response.status_code == 200:
                    key_candidates = get_carrier_response_key_candidates(response_json, carrier)
                    normalized = normalize_data_rows(
                        response_json=response_json,
                        carrier=carrier,
                        network_name=network_name,
                        http_status=response.status_code,
                        elapsed_ms=elapsed_ms,
                        response_key_candidates=key_candidates,
                        force_normalized_key_match=True,
                        missing_key_reason="response_key_missing",
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
    carrier_detail_df, failures_df, carrier_summary_df = build_carrier_reports(detail_df)
    with pd.ExcelWriter(xlsx_path, mode="w") as writer:
        carrier_summary_df.to_excel(
            writer,
            sheet_name=sheet_names["carrier_summary"],
            index=False,
            columns=[
                "carrier",
                "total_checks",
                "failed_checks",
                "failed_medicare_type_array",
                "pass_rate_percentage",
            ],
        )
        failures_df.to_excel(writer, sheet_name=sheet_names["failures"], index=False, columns=CARRIER_DETAIL_COLUMNS)
        carrier_detail_df.to_excel(writer, sheet_name=sheet_names["detail"], index=False, columns=CARRIER_DETAIL_COLUMNS)

    print("Report written:")
    print(xlsx_path)
    return xlsx_path
