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


@pytest.mark.asyncio
async def test_send_to_peer_streams_then_final():
    received = {}

    async def handler(ws):
        received["hello"] = json.loads(await ws.recv())
        received["msg"] = json.loads(await ws.recv())
        # Echo a hello (server normally does), then stream, then final.
        await ws.send(json.dumps(
            {"type": "hello", "session_id": received["hello"].get("session_id")}
        ))
        await ws.send(json.dumps({"delta": True, "content": "Hello "}))
        await ws.send(json.dumps({"delta": True, "content": "there"}))
        await ws.send(json.dumps({"final": True, "content": "Hello there"}))

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        reply = await peer.send_to_peer(
            url=f"ws://127.0.0.1:{port}",
            token="s3cret",
            session_id="peer:alice",
            message="hi",
            timeout=5,
        )

    assert reply == "Hello there"
    assert received["hello"]["token"] == "s3cret"
    assert received["hello"]["session_id"] == "peer:alice"
    assert received["msg"]["content"] == "hi"


@pytest.mark.asyncio
async def test_send_to_peer_non_streaming_uses_final_content():
    async def handler(ws):
        await ws.recv()
        await ws.recv()
        await ws.send(json.dumps({"final": True, "content": "whole reply"}))

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        reply = await peer.send_to_peer(
            f"ws://127.0.0.1:{port}", None, "peer:x", "hi", timeout=5
        )
    assert reply == "whole reply"


@pytest.mark.asyncio
async def test_send_to_peer_times_out_without_final():
    async def handler(ws):
        await ws.recv()
        await ws.recv()
        await ws.send(json.dumps({"delta": True, "content": "..."}))
        await asyncio.sleep(5)  # never sends final

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(peer.PeerError, match="no final reply"):
            await peer.send_to_peer(
                f"ws://127.0.0.1:{port}", None, "peer:x", "hi", timeout=0.3
            )


@pytest.mark.asyncio
async def test_send_to_peer_connection_refused():
    # Port 1 is not listening; connect should fail fast as a PeerError.
    with pytest.raises(peer.PeerError, match="failed"):
        await peer.send_to_peer(
            "ws://127.0.0.1:1", None, "peer:x", "hi", timeout=2
        )
