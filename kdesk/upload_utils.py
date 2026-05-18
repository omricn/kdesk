import os

ALLOWED_UPLOAD_EXTENSIONS = {
    '.pdf', '.txt', '.csv', '.log', '.rtf',
    '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.odt', '.ods', '.odp',
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp',
    '.zip', '.7z', '.sh',
    '.eml', '.msg',
}

_EXT_DISPLAY = ', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))


def allowed_upload(filename: str) -> str | None:
    """Return an error message if the file extension is not permitted, else None."""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        label = ext if ext else '(no extension)'
        return f'File type "{label}" is not allowed. Permitted types: {_EXT_DISPLAY}.'
    return None
