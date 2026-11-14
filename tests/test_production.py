"""Production configuration: proxy-aware client IPs and the static build."""

from pathlib import Path

import pytest
from django.conf import settings
from django.test import RequestFactory, override_settings

from apps.core.net import client_ip


def request(remote="10.0.0.2", xff=None):
    meta = {"REMOTE_ADDR": remote}
    if xff is not None:
        meta["HTTP_X_FORWARDED_FOR"] = xff
    return RequestFactory().get("/", **meta)


@override_settings(TRUSTED_PROXY_HOPS=0)
def test_without_proxy_uses_remote_addr_and_ignores_header():
    assert client_ip(request(xff="1.2.3.4")) == "10.0.0.2"


@override_settings(TRUSTED_PROXY_HOPS=1)
@pytest.mark.parametrize(
    ("xff", "expected"),
    [
        ("203.0.113.7", "203.0.113.7"),
        # A client forging the header can't choose its bucket: our proxy's entry wins.
        ("6.6.6.6, 203.0.113.7", "203.0.113.7"),
        (" 6.6.6.6 ,  203.0.113.7 ", "203.0.113.7"),
        ("", "10.0.0.2"),
        (None, "10.0.0.2"),
    ],
)
def test_behind_one_proxy_uses_rightmost_forwarded_address(xff, expected):
    assert client_ip(request(xff=xff)) == expected


def test_prod_settings(monkeypatch):
    """Import prod settings with a domain and check what's derived from it."""
    import importlib

    monkeypatch.setenv("DJANGO_SECRET_KEY", "x" * 50)
    monkeypatch.setenv("DOMAIN", "demo.example.com")
    monkeypatch.delenv("DJANGO_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("DJANGO_CSRF_TRUSTED_ORIGINS", raising=False)
    prod = importlib.import_module("config.settings.prod")
    prod = importlib.reload(prod)
    assert prod.ALLOWED_HOSTS == ["demo.example.com"]
    assert prod.CSRF_TRUSTED_ORIGINS == ["https://demo.example.com"]
    assert prod.TRUSTED_PROXY_HOPS == 1
    assert prod.SECURE_SSL_REDIRECT and prod.SESSION_COOKIE_SECURE
    assert prod.SECURE_REDIRECT_EXEMPT == [r"^health/$"]
    assert prod.DEBUG is False


@pytest.mark.django_db
def test_pages_use_compiled_css_not_play_cdn(client):
    html = client.get("/accounts/login/").content.decode()
    assert "cdn.tailwindcss.com" not in html
    assert 'href="/static/css/app.css"' in html


def test_tailwind_sources_cover_templates_and_python():
    css = (Path(settings.BASE_DIR) / "static/src/app.css").read_text()
    assert '@import "tailwindcss" source(none);' in css
    assert '@source "../../templates";' in css and '@source "../../apps";' in css
