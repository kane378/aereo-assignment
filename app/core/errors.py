"""Exception hierarchy.

`AppError` subclasses are *client* errors: they map to a 4xx response with a
safe, human-readable message. `ProcessingError` is raised while parsing or
measuring an already-accepted upload and ends up stored on the file record as
`status=FAILED` instead of failing the HTTP request.
"""


class AppError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidUploadError(AppError):
    status_code = 400


class UnsupportedFileTypeError(AppError):
    status_code = 415


class PayloadTooLargeError(AppError):
    status_code = 413


class UnsafeArchiveError(AppError):
    """ZIP / XML content that looks malicious (zip-slip, zip bomb, XXE...)."""

    status_code = 422


class ProcessingError(Exception):
    """A readable file that cannot be processed (no CRS, too many features...)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message
