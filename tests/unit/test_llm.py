"""Unit-Tests für valar.llm: Kostenrechnung und der echte HTTP-Weg gegen einen Server auf dem eigenen Rechner."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from valar import llm


def test_cost_is_rounded_up_in_millionths_of_a_euro():
    paid = llm.ProviderSettings(
        name="p", base_url="https://p.example", model="m", key_env="P_KEY",
        input_eur_per_million=0.13, output_eur_per_million=0.26,
    )
    assert llm.cost_micro_eur(paid, 1000, 100) == 156  # 130 + 26
    assert llm.cost_micro_eur(paid, 1, 0) == 1  # 0,13 aufgerundet
    free = llm.ProviderSettings(name="f", base_url="https://f.example", model="m", key_env="F_KEY")
    assert llm.cost_micro_eur(free, 10**9, 10**9) == 0


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # Name aus http.server
        length = int(self.headers["Content-Length"])
        body = self.rfile.read(length)
        if self.path == "/langsam":
            threading.Event().wait(1)
        if self.path == "/weiter":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.2:9/fremd")
            self.end_headers()
            return
        status = 429 if self.path == "/voll" else 200
        self.send_response(status)
        self.end_headers()
        self.wfile.write(self.headers["Authorization"].encode() + b"|" + body)

    def log_message(self, *args):  # still
        pass


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


def test_urllib_transport_posts_and_reads(server):
    response = llm.UrllibTransport().post(f"{server}/ok", {"Authorization": "Bearer x"}, b'{"a": 1}', 5)
    assert (response.status, response.body) == (200, b'Bearer x|{"a": 1}')


def test_urllib_transport_returns_http_errors_as_status(server):
    assert llm.UrllibTransport().post(f"{server}/voll", {"Authorization": "x"}, b"{}", 5).status == 429


def test_urllib_transport_timeout_and_no_server(server):
    with pytest.raises(llm.TransportError):
        llm.UrllibTransport().post(f"{server}/langsam", {"Authorization": "x"}, b"{}", 0.2)
    with pytest.raises(llm.TransportError):
        llm.UrllibTransport().post("http://127.0.0.1:9/", {"Authorization": "x"}, b"{}", 1)


def test_urllib_transport_never_follows_a_redirect(server):
    # Bei einer Weiterleitung ginge der Schlüssel im Kopf an eine andere Adresse; der Client nimmt sie als Fehler.
    response = llm.UrllibTransport().post(f"{server}/weiter", {"Authorization": "Bearer geheim"}, b"{}", 5)
    assert response.status == 302
