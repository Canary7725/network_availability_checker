import json
import requests
import csv
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import os
import sys
from utils import get_config_path, get_file_path


def main(env):
    config_path = get_config_path()
    with open(config_path, 'r') as f:
        config = json.load(f)

    if env == "prod":
        secrets = config["prod_secrets"]
    else:
        secrets = config["dev_secrets"]

    source_file_name = config["file"]["source_file_path"]
    source_file = get_file_path(source_file_name)

    api_url = secrets["url"]
    header = {
        "X-Client-Key": secrets["client_key"],
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    pattern = r'^\d+(\.\d+)?%$'
    result = []

    with open(source_file, mode='r', newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            body={
                "additionalValues": [],
                "carrier": [
                    row["carrier"]
                ],
                "report": "networkComparison",
                "homeValue": row["network_name"],
                "reportType": "network",
                "totalWeightComparisonValue": {
                    "IPPS": "349",
                    "OPPS": "216",
                    "PFS": "266",
                    "DrugB": "148"
                },
                "percentageLimitMap": {
                    "IPPS": {
                        "lowerLimit": "70",
                        "upperLimit": "1000"
                    },
                    "OPPS": {
                        "lowerLimit": "50",
                        "upperLimit": "1500"
                    },
                    "PFS": {
                        "lowerLimit": "70",
                        "upperLimit": "1000"
                    },
                    "DrugB": {
                        "lowerLimit": "70",
                        "upperLimit": "1000"
                    }
                }
            }
            
            response = requests.post(api_url, headers=header, json=body)
            output_dict = {"carrier": row["carrier"], "network_name": row["network_name"]}
            if response.status_code == 200:
                array = ['percentage_medicare', 'percentage_discount', 'percentage_completeness']
                for value in array:
                    data = response.json()['data'][value]
                    for values in data:
                        if re.match(pattern, values[row['network_name']]):
                            output_dict['isAvailable'] = 'true'
                            output_dict['Remarks'] = 'OK'
                        else:
                            output_dict['isAvailable'] = 'false'
                            output_dict['Remarks'] = 'Unavailable'
                result.append(output_dict)
            else:
                output_dict['isAvailable'] = 'false'
                output_dict['Remarks'] = 'No response from API'
                result.append(output_dict)

    output_file_name = secrets["output_file_path"]
    output_file = get_file_path(output_file_name, is_source=False)
    if not os.path.isabs(output_file):
        output_file = os.path.abspath(output_file)

    fieldnames = ["carrier", "network_name", "isAvailable", "Remarks"]
    with open(output_file, mode="w", newline='') as outputFile:
        writer = csv.DictWriter(outputFile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(result)

    print("Written into", output_file)

    recipient_email = ["gkc@deerhold.com","rsedhai@deerhold.com"]
    subject = "Latest Network Availability Response csv"
    body = "Please find the latest response from Network Availability"

    sendEmail(recipient_email, subject, body, output_file, config)
    return 0


def sendEmail(recipient_email, subject, body, output_file, config):
    smtp_server = "smtp.gmail.com"
    port = 587
    senderEmail = config["email"]["sender_email"]
    senderPassword = config["email"]["password"]

    msg = MIMEMultipart()
    msg['From'] = senderEmail
    msg['To'] = ", ".join(recipient_email)
    msg['Subject'] = subject

    msg.attach(MIMEText(body, 'plain'))
    with open(output_file, mode='rb') as attachment:
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(attachment.read())
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename= {os.path.basename(output_file)}')

    msg.attach(part)
    try:
        with smtplib.SMTP(smtp_server, int(port)) as server:
            server.starttls()
            server.login(senderEmail, senderPassword)
            server.send_message(msg)
        print("Email sent successfully")
    except Exception as e:
        print("Error", e)


def cli():
    import argparse

    parser = argparse.ArgumentParser(description="Run script for DEV or PROD environment")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dev", action="store_true", help="Use DEV environment")
    group.add_argument("--prod", action="store_true", help="Use PROD environment")
    args = parser.parse_args()

    env = "prod" if args.prod else "dev"
    sys.exit(main(env))

if __name__ == "__main__":
    cli()
