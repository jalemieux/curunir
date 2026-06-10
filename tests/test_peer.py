# tests/test_peer.py
import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
import websockets

# peer.py lives in a skill dir (not a package), so load it by path.
_PEER_PATH = Path(__file__).resolve().parent.parent / "skills" / "talk-to-peer" / "peer.py"
_spec = importlib.util.spec_from_file_location("peer", _PEER_PATH)
peer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(peer)


def test_parse_peers_empty():
    assert peer.parse_peers(None) == {}
    assert peer.parse_peers("   ") == {}


def test_parse_peers_valid():
    raw = '{"bob": {"url": "ws://b:8765", "token": "x"}}'
    peers = peer.parse_peers(raw)
    assert peers["bob"]["url"] == "ws://b:8765"
    assert peers["bob"]["token"] == "x"


def test_parse_peers_malformed_json():
    with pytest.raises(peer.PeerError, match="not valid JSON"):
        peer.parse_peers("{not json")


def test_parse_peers_wrong_top_shape():
    with pytest.raises(peer.PeerError, match="must be a JSON object"):
        peer.parse_peers('["bob"]')


def test_parse_peers_peer_missing_url():
    with pytest.raises(peer.PeerError, match="'url'"):
        peer.parse_peers('{"bob": {"token": "x"}}')


def test_peer_names_sorted():
    peers = peer.parse_peers('{"zed": {"url": "ws://z"}, "abe": {"url": "ws://a"}}')
    assert peer.peer_names(peers) == ["abe", "zed"]


def test_resolve_unknown_peer_lists_available():
    peers = peer.parse_peers('{"bob": {"url": "ws://b"}}')
    with pytest.raises(peer.PeerError, match="available: bob"):
        peer.resolve_peer(peers, "alice")
