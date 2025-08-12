import os


def get_file_media_type(file_path):
    _, file_extension = os.path.splitext(file_path)
    media_types = {
        ".yaml": "text/yaml",
        ".yml": "text/yaml",
        ".json": "application/json",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".csv": "text/csv",
        ".html": "text/html",
        ".pdf": "application/pdf",
    }
    return media_types.get(file_extension, "text/plain")
