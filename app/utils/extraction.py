import mimetypes


def get_file_media_type(file_path):
    return mimetypes.guess_type(file_path)[0] or "text/plain"
