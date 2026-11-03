"""Checks the responses the tests receive against openapi.yaml."""

from pathlib import Path

import httpx
from openapi_core import OpenAPI
from openapi_core.datatypes import RequestParameters
from werkzeug.datastructures import Headers, ImmutableMultiDict

SPEC = OpenAPI.from_file_path(str(Path(__file__).parent.parent / "openapi.yaml"))
# The first server of openapi.yaml, so that its paths match without a prefix
HOST_URL = "http://localhost:8000"


class SpecRequest:
    def __init__(self, request: httpx.Request):
        self._request = request
        self.parameters = RequestParameters(
            query=ImmutableMultiDict(request.url.params.multi_items()),
            header=Headers(request.headers.multi_items()),
            cookie=ImmutableMultiDict(),
        )

    host_url = HOST_URL

    @property
    def path(self) -> str:
        # Encoded, so a file path is one segment like in openapi.yaml; FastAPI serves the
        # collection routes with a trailing slash and redirects to it
        return self._request.url.raw_path.split(b"?")[0].decode().rstrip("/") or "/"

    @property
    def method(self) -> str:
        return self._request.method.lower()

    @property
    def body(self) -> bytes | None:
        return self._request.content or None

    @property
    def content_type(self) -> str:
        return self._request.headers.get("content-type", "").lower()


class SpecResponse:
    def __init__(self, response: httpx.Response):
        self._response = response

    @property
    def status_code(self) -> int:
        return self._response.status_code

    @property
    def content_type(self) -> str:
        return self._response.headers.get("content-type", "").lower()

    @property
    def headers(self) -> Headers:
        return Headers(self._response.headers.multi_items())

    @property
    def data(self) -> bytes | None:
        return self._response.content


def validate_response(response: httpx.Response) -> None:
    """Raises if the response doesn't match openapi.yaml; redirects are FastAPI's and not part of the API."""
    if response.is_redirect:
        return
    response.read()
    SPEC.validate_response(SpecRequest(response.request), SpecResponse(response))
