# Network Availability Checker

This project validates API response availability and generates Excel reports for:

- Network report
- Carrier report

The current entrypoint is `main.py`, which orchestrates selected reports from config and sends one email with all generated files.

## Project Structure

- `main.py`: orchestrator (loads config, runs selected reports, sends email)
- `network_report.py`: network report generation logic
- `carrier_report.py`: carrier report generation logic
- `report_utils.py`: shared helpers (config/file path helpers, normalization, retry logic, email sender)
- `backend.py`: legacy reference implementation (kept intentionally)

## Prerequisites

- Python 3.9+
- A virtual environment (recommended)

## Installation

1. Create a virtual environment:

```bash
python3 -m venv venv
```

2. Activate it:

```bash
source venv/bin/activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

## Source Input File Requirement

The source input file should ideally be a `.csv` file with **two columns**:

- `carrier`
- `network_name`

Example:

```csv
carrier,network_name
Aetna,Aetna Choice POS II
Alliance,Alliance Comprehensive
Cigna,Cigna Localplus
```

Set this file path in config under:

- `file.source_file_path`

## Configuration

Update `config.json` before running.

### Required sections

- `reports_to_generate`: list of reports to run
- `file.source_file_path`: source CSV file path
- `dev_secrets` and `uat_secrets`:
  - `url`
  - `client_key`
  - `output_file_path` (network report output)
  - `carrier_output_file_path` (carrier report output)
- `email`:
  - `sender_email`
  - `password`
  - `recipients` (list)

### Optional/runtime sections

- `api.timeout_seconds`
- `api.max_retries`
- `run.dedupe_input`
- `logging.log_file`
- `logging.level`
- `report.sheet_names`

## Run Instructions

### Run against UAT

```bash
python3 main.py --uat
```

### Run against DEV

```bash
python3 main.py --dev
```

What happens on each run:

1. Config is loaded from `config.json`
2. Logging is initialized (`filemode="w"`, so previous log file is overwritten)
3. Reports listed in `reports_to_generate` are generated
4. Existing output files are overwritten
5. One email is sent with all generated report attachments

## Outputs

By default (based on current config):

- Network report workbook -> from `output_file_path`
- Carrier report workbook -> from `carrier_output_file_path`

## Notes

- If SMTP/network is unavailable, report generation still completes locally; email send may fail.
