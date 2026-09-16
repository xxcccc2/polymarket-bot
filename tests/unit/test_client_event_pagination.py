from requests import HTTPError, Response

from src.client import PolymarketClient


def test_event_pagination_keeps_pages_before_gamma_offset_cap():
    client = object.__new__(PolymarketClient)
    client.is_connected = True
    response = Response()
    response.status_code = 422
    calls = iter([[{"id": "first"}], HTTPError(response=response)])
    client._retry_call = lambda *_: next(calls)

    assert client.get_events(limit=1) == [{"id": "first"}]
