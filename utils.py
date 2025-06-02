import os

def get_config_path():
    """Get config.json path from the same directory as main.py"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.json')

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found at {config_path}")

    return config_path


def get_file_path(filename, is_source=True):
    """Get file path from the same directory as main.py"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(script_dir, filename)

    if is_source and not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    return file_path
