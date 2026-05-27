# Network Availability Checker

This project validates API response availability and generates Excel reports for configured report types:

- Network report
- Carrier report
- Reporting Entity report
- Product report

The entrypoint is `main.py`, and recommended delivery runners are:

- `run.sh` for macOS/Linux
- `run.bat` for Windows

## Python Compatibility

- Supported: **Python 3.9 to 3.13**
- Recommended: latest stable Python 3.x

Warning:
- Python versions below 3.9 are not supported.
- Always run inside a virtual environment (`venv`) to avoid package conflicts.

## Project Structure

- `main.py`: orchestrator (loads config, runs selected reports)
- `network_report.py`: network report generation
- `carrier_report.py`: carrier report generation
- `reporting_entity_report.py`: reporting entity report generation
- `product_report.py`: product report generation
- `report_utils.py`: shared helpers (normalization, retries, paths, email helper)
- `config.json`: runtime configuration
- `run.sh`: one-command runner for macOS/Linux
- `run.bat`: one-command runner for Windows

## Source Input File

The source file should be in CSV format and is expected to exist in project folder.

Default file: `network_list.csv`

Expected headers:

- `carrier`
- `reporting_entity_name`
- `product`
- `network_name`

Sample:

```csv
carrier,reporting_entity_name,product,network_name
Cigna,Cigna Health Life Insurance Company,OAP,Cigna National OAP
BCBS,Blue Cross Blue Shield,BCBS-HMO,BCBS National
```

Set path in:

- `file.source_file_path`

## Configuration

Update `config.json` before running.

Required sections:

- `reports_to_generate`: list of report modules to run
- `file.source_file_path`
- `dev_secrets` and `uat_secrets`:
  - `url`
  - `client_key`
  - `network_output_file_path`
  - `carrier_output_file_path`
  - `reporting_entity_output_file_path`
  - `product_output_file_path`
- `email`:
  - `sender_email`
  - `password`
  - `recipients`

Optional runtime sections:

- `api.timeout_seconds`
- `api.max_retries`
- `run.dedupe_input`
- `logging.log_file`
- `logging.level`
- `report.sheet_names`

## How To Run

### macOS / Linux

From project root:

```bash
chmod +x run.sh
./run.sh
```

Environment override:

```bash
./run.sh uat
./run.sh dev
```

### Windows

From project root in Command Prompt:

```bat
run.bat
```

Environment override:

```bat
run.bat uat
run.bat dev
```

## What Runner Scripts Do

Both `run.sh` and `run.bat` automatically:

1. Validate required files (`config.json`, `network_list.csv`, `requirements.txt`)
2. Create `venv` if missing
3. Activate virtual environment
4. Install/update dependencies from `requirements.txt`
5. Run `main.py` with selected environment (`uat` default)

## Output and Logging Behavior

- Logs overwrite each run (`filemode="w"` in logging setup)
- Report files overwrite each run (Excel writer uses write mode)
- Output file names are controlled in `config.json` per environment

## Notes

- If API/SMTP connectivity is unavailable, run can fail at request/email stages.
- Keep `config.json` and credentials secure and out of public repositories.
