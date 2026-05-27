import argparse
import json
import logging
import sys
from typing import Callable, Dict, List

from carrier_report import generate_carrier_report
from network_report import generate_network_report
from product_report import generate_product_report
from reporting_entity_report import generate_reporting_entity_report
from report_utils import DEFAULT_LOG_FILE, get_config_path, send_email


def load_config() -> Dict:
    config_path = get_config_path()
    with open(config_path, "r", encoding="utf-8") as config_file:
        return json.load(config_file)


def configure_logging(config: Dict) -> None:
    logging_config = config.get("logging", {})
    log_file = logging_config.get("log_file", DEFAULT_LOG_FILE)
    log_level_name = str(logging_config.get("level", "INFO")).upper()
    log_level = getattr(logging, log_level_name, logging.INFO)
    logging.basicConfig(
        filename=log_file,
        level=log_level,
        filemode="w",
        format="%(asctime)s - %(levelname)s - %(message)s",
    )


def run_selected_reports(config: Dict, env: str) -> List[str]:
    registry: Dict[str, Callable[[Dict, str], str]] = {
        "network": generate_network_report,
        "carrier": generate_carrier_report,
        "reporting_entity": generate_reporting_entity_report,
        "product": generate_product_report,
    }

    reports_to_generate = config.get(
        "reports_to_generate",
        ["network", "carrier", "reporting_entity", "product"],
    )
    if not isinstance(reports_to_generate, list) or not reports_to_generate:
        raise ValueError("config.json reports_to_generate must be a non-empty list")

    output_files: List[str] = []
    for report_name in reports_to_generate:
        if report_name not in registry:
            raise ValueError(f"Unsupported report '{report_name}' in reports_to_generate")
        output_files.append(registry[report_name](config, env))

    return output_files


def cli() -> None:
    parser = argparse.ArgumentParser(description="Network/Carrier report orchestrator")
    env_group = parser.add_mutually_exclusive_group(required=True)
    env_group.add_argument("--dev", action="store_true", help="Use DEV environment")
    env_group.add_argument("--uat", action="store_true", help="Use UAT environment")
    args = parser.parse_args()

    env = "uat" if args.uat else "dev"
    config = load_config()
    configure_logging(config)

    output_files = run_selected_reports(config, env)

    recipients = config.get("email", {}).get("recipients")
    if not isinstance(recipients, list) or not recipients:
        raise ValueError("Missing required email.recipients in config.json")

    subject = "Latest Network and Carrier Availability Reports"
    body = "Please find the latest network and carrier reports from Network Availability."
    # send_email(recipients, subject, body, output_files, config)

    sys.exit(0)


if __name__ == "__main__":
    cli()
