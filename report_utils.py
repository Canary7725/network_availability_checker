import os
import re
import smtplib
import time
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Optional, Tuple

import requests

METRIC_TYPES = [
    "percentage_medicare",
    "percentage_discount",
    "percentage_confidence",
]

MEDICARE_TYPES = [
    "IPPS",
    "OPPS",
    "PFS",
    "DrugB",
    "Anesthesia",
    "CLFS",
    "ASC",
    "DMEPOS",
]

DETAIL_COLUMNS = [
    "carrier",
    "network_name",
    "metric_type",
    "medicare_type",
    "raw_value",
    "is_available",
    "reason",
    "http_status",
    "elapsed_ms",
]

CARRIER_DETAIL_COLUMNS = [
    "carrier",
    "metric_type",
    "medicare_type",
    "raw_value",
    "is_available",
    "reason",
    "http_status",
    "elapsed_ms",
]

PERCENT_PATTERN = re.compile(r"^\d+(\.\d+)?%$")
MEDICARE_IN_CATEGORY_PATTERN = re.compile(r"\(([^)]+)\)")
DEFAULT_LOG_FILE = "backend_api_response_log.txt"


def get_config_path() -> str:
    """Get config.json path from the same directory as this module."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "config.json")
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found at {config_path}")
    return config_path


def get_file_path(filename: str, is_source: bool = True) -> str:
    """Get file path from the same directory as this module."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(script_dir, filename)
    if is_source and not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    return file_path


def normalize_label(value: str) -> str:
    """Normalize labels for resilient key matching (e.g., AmericasPPO vs America's PPO)."""
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def parse_medicare_type(category: str) -> Optional[str]:
    """Extract canonical medicare type from a Category value like 'Inpatient (IPPS)'."""
    if not category:
        return None

    match = MEDICARE_IN_CATEGORY_PATTERN.search(category)
    if not match:
        return None

    extracted = match.group(1).strip()
    for medicare_type in MEDICARE_TYPES:
        if extracted.lower() == medicare_type.lower():
            return medicare_type

    return None


def normalize_data_rows(
    *,
    response_json: Dict,
    carrier: str,
    network_name: str,
    http_status: int,
    elapsed_ms: int,
    response_key_candidates: Optional[List[str]] = None,
    force_normalized_key_match: bool = False,
    missing_key_reason: str = "network_key_missing",
) -> List[Dict]:
    """
    Normalize API response into one row per metric_type x medicare_type.
    Always returns exactly 24 rows with a reason for missing/invalid values.
    """
    data = response_json.get("data") or {}
    parsed: Dict[Tuple[str, str], Dict] = {}

    for metric_type in METRIC_TYPES:
        entries = data.get(metric_type)
        if not isinstance(entries, list):
            continue

        for item in entries:
            if not isinstance(item, dict):
                continue

            category = str(item.get("Category", "")).strip()
            medicare_type = parse_medicare_type(category)
            if not medicare_type:
                continue

            candidate_keys = response_key_candidates or [network_name]
            raw_value = None

            if force_normalized_key_match:
                normalized_candidates = {normalize_label(k) for k in candidate_keys}
                for key in item.keys():
                    if key == "Category":
                        continue
                    if normalize_label(key) in normalized_candidates:
                        raw_value = item.get(key)
                        break
            else:
                for key in candidate_keys:
                    if key in item:
                        raw_value = item.get(key)
                        break

                if raw_value is None:
                    normalized_candidates = {normalize_label(k) for k in candidate_keys}
                    for key in item.keys():
                        if key == "Category":
                            continue
                        if normalize_label(key) in normalized_candidates:
                            raw_value = item.get(key)
                            break

            if raw_value is None:
                is_available = False
                reason = missing_key_reason
                normalized_value = ""
            else:
                normalized_value = str(raw_value).strip()
                is_available = bool(PERCENT_PATTERN.match(normalized_value))
                reason = "OK" if is_available else "invalid_percent_format"

            parsed[(metric_type, medicare_type)] = {
                "carrier": carrier,
                "network_name": network_name,
                "metric_type": metric_type,
                "medicare_type": medicare_type,
                "raw_value": normalized_value,
                "is_available": "True" if is_available else "False",
                "reason": reason,
                "http_status": http_status,
                "elapsed_ms": elapsed_ms,
            }

    rows: List[Dict] = []
    for metric_type in METRIC_TYPES:
        for medicare_type in MEDICARE_TYPES:
            key = (metric_type, medicare_type)
            if key in parsed:
                rows.append(parsed[key])
            else:
                rows.append(
                    {
                        "carrier": carrier,
                        "network_name": network_name,
                        "metric_type": metric_type,
                        "medicare_type": medicare_type,
                        "raw_value": "",
                        "is_available": "False",
                        "reason": "combination_missing_in_response",
                        "http_status": http_status,
                        "elapsed_ms": elapsed_ms,
                    }
                )

    return rows


def execute_request_with_retries(
    *,
    session: requests.Session,
    api_url: str,
    headers: Dict,
    payload: Dict,
    timeout_seconds: int,
    max_retries: int,
):
    response = None
    response_json = {}
    elapsed_ms = 0
    request_exception = None

    for attempt in range(max_retries + 1):
        try:
            response = session.post(api_url, headers=headers, json=payload, timeout=timeout_seconds)
            elapsed_ms = int(response.elapsed.total_seconds() * 1000)

            try:
                response_json = response.json()
            except ValueError:
                response_json = {}

            if response.status_code >= 500 and attempt < max_retries:
                time.sleep(min(1.5, 0.5 * (2 ** attempt)))
                continue

            return response, response_json, elapsed_ms, None
        except requests.RequestException as error:
            request_exception = error
            if attempt < max_retries:
                time.sleep(min(1.5, 0.5 * (2 ** attempt)))
                continue
            return None, {}, 0, request_exception

    return response, response_json, elapsed_ms, request_exception


def send_email(recipient_email, subject, body, output_files, config):
    smtp_server = "smtp.gmail.com"
    port = 587
    sender_email = config["email"]["sender_email"]
    sender_password = config["email"]["password"]

    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = ", ".join(recipient_email)
    msg["Subject"] = subject

    msg.attach(MIMEText(body, "plain"))
    for output_file in output_files:
        with open(output_file, mode="rb") as attachment:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f"attachment; filename={os.path.basename(output_file)}",
            )
        msg.attach(part)

    try:
        with smtplib.SMTP(smtp_server, int(port)) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)
        print("Email sent successfully")
    except Exception as error:
        print("Error", error)
