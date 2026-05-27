import json
import logging
import os
import re
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


def parse_multi_value_field(raw_value: str) -> List[str]:
    if raw_value is None:
        return []
    text = str(raw_value).strip()
    if not text:
        return []
    parts = re.split(r"[|,;]", text)
    values = []
    for part in parts:
        val = part.strip()
        if val and val not in values:
            values.append(val)
    return values


def build_product_request_body(home_value: str, carrier_values: List[str]) -> Dict:
    return {
        "additionalValues": [],
        "carrier": carrier_values,
        "report": "networkComparison",
        "homeValue": home_value,
        "reportType": "product",
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


def build_product_labels(carrier: str, product_values: List[str]) -> List[str]:
    labels: List[str] = []
    for product in product_values:
        label = f"{carrier} - {product}".strip()
        if label not in labels:
            labels.append(label)
    return labels


def read_source_rows(source_file: str, dedupe_input: bool) -> List[Dict]:
    source_df = pd.read_csv(source_file, usecols=["carrier", "product"], dtype=str)
    source_df["carrier"] = source_df["carrier"].fillna("").str.strip()
    source_df["product"] = source_df["product"].fillna("").str.strip()
    source_df = source_df[(source_df["carrier"] != "") & (source_df["product"] != "")]

    source_df["carrier_values"] = source_df["carrier"].apply(parse_multi_value_field)
    source_df["product_values"] = source_df["product"].apply(parse_multi_value_field)
    source_df = source_df[source_df["carrier_values"].map(len) > 0]
    source_df = source_df[source_df["product_values"].map(len) > 0]

    source_df["home_value"] = source_df.apply(
        lambda row: build_product_labels(row["carrier_values"][0], row["product_values"])[0],
        axis=1,
    )
    source_df["product_for_summary"] = source_df["home_value"]

    if dedupe_input:
        source_df["dedupe_key"] = source_df.apply(
            lambda row: (
                tuple(row["carrier_values"]),
                row["home_value"],
            ),
            axis=1,
        )
        source_df = source_df.drop_duplicates(subset=["dedupe_key"], keep="first")

    return source_df[["carrier_values", "home_value", "product_for_summary"]].to_dict("records")


def build_product_reports(detail_df: pd.DataFrame):
    if detail_df.empty:
        empty_detail = pd.DataFrame(columns=DETAIL_COLUMNS)
        summary_df = pd.DataFrame(
            columns=[
                "carrier",
                "product",
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
        detail_df.groupby(["carrier", "product"], as_index=False)
        .agg(
            total_checks=("is_available", "size"),
            failed_checks=("is_available", lambda s: (s == "False").sum()),
        )
        .sort_values(["carrier", "product"])
    )

    medicare_order = {medicare_type: idx for idx, medicare_type in enumerate(MEDICARE_TYPES)}
    failed_types_df = (
        detail_df[detail_df["is_available"] == "False"][["carrier", "product", "medicare_type"]]
        .drop_duplicates()
        .copy()
    )
    if not failed_types_df.empty:
        failed_types_df["sort_key"] = failed_types_df["medicare_type"].map(medicare_order).fillna(999)
        failed_types_df = failed_types_df.sort_values(["carrier", "product", "sort_key", "medicare_type"])
        failed_types_by_key = (
            failed_types_df.groupby(["carrier", "product"])["medicare_type"]
            .agg(",".join)
            .rename("failed_medicare_type_array")
            .reset_index()
        )
        summary_df = summary_df.merge(failed_types_by_key, on=["carrier", "product"], how="left")
    else:
        summary_df["failed_medicare_type_array"] = ""

    summary_df["failed_medicare_type_array"] = summary_df["failed_medicare_type_array"].fillna("")
    summary_df["pass_rate_percent"] = (
        ((summary_df["total_checks"] - summary_df["failed_checks"]) / summary_df["total_checks"]) * 100
    ).round(2)
    summary_df["pass_rate_percent"] = summary_df["pass_rate_percent"].map(lambda v: f"{v:.2f}")
    summary_df["overall_available"] = summary_df["failed_checks"].eq(0).map({True: "True", False: "False"})

    return detail_df, failures_df, summary_df


def generate_product_report(config: Dict, env: str) -> str:
    secrets = config["uat_secrets"] if env == "uat" else config["dev_secrets"]
    source_file = get_file_path(config["file"]["source_file_path"])

    output_file_name = secrets.get("product_output_file_path", f"{env}_product_report.xlsx")
    output_base = get_file_path(output_file_name, is_source=False)
    xlsx_path = os.path.abspath(output_base)
    output_dir = os.path.dirname(xlsx_path) or os.getcwd()
    os.makedirs(output_dir, exist_ok=True)

    timeout_seconds = int(config.get("api", {}).get("timeout_seconds", 60))
    max_retries = int(config.get("api", {}).get("max_retries", 0))
    dedupe_enabled = bool(config.get("run", {}).get("dedupe_input", True))

    sheet_name_config = config.get("report", {}).get("sheet_names", {})
    sheet_names = {
        "summary": str(sheet_name_config.get("product_summary", "product_summary")),
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
            carrier_values = row["carrier_values"]
            home_value = row["home_value"]
            product_for_summary = row["product_for_summary"]
            payload = build_product_request_body(home_value, carrier_values)

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
                    "Carriers=%s | Product=%s | Status=%s | Data=%s",
                    ",".join(carrier_values),
                    home_value,
                    response.status_code,
                    json.dumps(response_json.get("data"), default=str),
                )
                for carrier in carrier_values:
                    if response.status_code == 200:
                        normalized = normalize_data_rows(
                            response_json=response_json,
                            carrier=carrier,
                            network_name=product_for_summary,
                            http_status=response.status_code,
                            elapsed_ms=elapsed_ms,
                            response_key_candidates=[
                                home_value,
                                home_value.replace(" - ", "-"),
                                home_value.replace("-", " - "),
                            ],
                            force_normalized_key_match=True,
                            missing_key_reason="response_key_missing",
                        )
                    else:
                        normalized = [
                            {
                                "carrier": carrier,
                                "network_name": product_for_summary,
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
                    "Carriers=%s | Product=%s | RequestException=%s",
                    ",".join(carrier_values),
                    home_value,
                    str(request_exception),
                )
                for carrier in carrier_values:
                    detail_rows.extend(
                        [
                            {
                                "carrier": carrier,
                                "network_name": product_for_summary,
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

    detail_df = pd.DataFrame(detail_rows, columns=DETAIL_COLUMNS).rename(columns={"network_name": "product"})
    detail_df, failures_df, summary_df = build_product_reports(detail_df)

    with pd.ExcelWriter(xlsx_path, mode="w") as writer:
        summary_df.to_excel(
            writer,
            sheet_name=sheet_names["summary"],
            index=False,
            columns=[
                "carrier",
                "product",
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
