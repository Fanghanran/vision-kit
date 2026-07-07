"""Tests for vision_agent.storage.cache"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from vision_agent.storage.cache import MemoryCache, RedisCache, create_cache


# ─── Helpers ──────────────────────────────────────────────────


def _make_cache(max_size: int = 100, cleanup_interval: int = 9999) -> MemoryCache:
    """Create a MemoryCache with a very long cleanup interval to avoid interference."""
    cache = MemoryCache(max_size=max_size, cleanup_interval=cleanup_interval)
    yield cache  # type: ignore[misc]
    cache.close()


@pytest.fixture()
def cache() -> MemoryCache:
    """Each test gets a fresh MemoryCache; cleaned up after the test."""
    c = MemoryCache(max_size=100, cleanup_interval=9999)
    yield c
    c.close()


# ─── MemoryCache.get / set ────────────────────────────────────


class TestMemoryCacheGetSet:
    def test_get_missing_key_returns_none(self, cache: MemoryCache):
        assert cache.get("nonexistent") is None

    def test_set_then_get(self, cache: MemoryCache):
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_set_overwrite(self, cache: MemoryCache):
        cache.set("key1", "first")
        cache.set("key1", "second")
        assert cache.get("key1") == "second"

    def test_set_different_types(self, cache: MemoryCache):
        cache.set("str", "hello")
        cache.set("int", 42)
        cache.set("float", 3.14)
        cache.set("list", [1, 2, 3])
        cache.set("dict", {"a": 1})
        cache.set("none_val", None)

        assert cache.get("str") == "hello"
        assert cache.get("int") == 42
        assert cache.get("float") == 3.14
        assert cache.get("list") == [1, 2, 3]
        assert cache.get("dict") == {"a": 1}
        assert cache.get("none_val") is None  # None stored, but also None for missing

    def test_set_none_value_stored(self, cache: MemoryCache):
        """None is a valid stored value; exists() should return True."""
        cache.set("n", None)
        assert cache.exists("n") is True


# ─── MemoryCache TTL ──────────────────────────────────────────


class TestMemoryCacheTTL:
    def test_ttl_not_expired_returns_value(self, cache: MemoryCache, monkeypatch):
        monkeypatch.setattr(time, "time", lambda: 1000.0)
        cache.set("k", "v", ttl=10)
        # still within TTL
        monkeypatch.setattr(time, "time", lambda: 1005.0)
        assert cache.get("k") == "v"

    def test_ttl_expired_returns_none(self, cache: MemoryCache, monkeypatch):
        monkeypatch.setattr(time, "time", lambda: 1000.0)
        cache.set("k", "v", ttl=10)
        # past expiration
        monkeypatch.setattr(time, "time", lambda: 1011.0)
        assert cache.get("k") is None

    def test_ttl_exact_boundary_returns_none(self, cache: MemoryCache, monkeypatch):
        """time.time() > expire_at is strict; equal is not expired."""
        monkeypatch.setattr(time, "time", lambda: 1000.0)
        cache.set("k", "v", ttl=10)
        # exactly at expire_at: 1000+10 = 1010, time.time() returns 1010
        # time.time() > expire_at -> 1010 > 1010 is False, so not expired
        monkeypatch.setattr(time, "time", lambda: 1010.0)
        assert cache.get("k") == "v"

    def test_no_ttl_never_expires(self, cache: MemoryCache, monkeypatch):
        monkeypatch.setattr(time, "time", lambda: 1000.0)
        cache.set("k", "v")
        monkeypatch.setattr(time, "time", lambda: 999999999.0)
        assert cache.get("k") == "v"


# ─── MemoryCache.delete ───────────────────────────────────────


class TestMemoryCacheDelete:
    def test_delete_existing(self, cache: MemoryCache):
        cache.set("k", "v")
        assert cache.delete("k") is True
        assert cache.get("k") is None

    def test_delete_nonexistent(self, cache: MemoryCache):
        assert cache.delete("missing") is False

    def test_delete_only_target_key(self, cache: MemoryCache):
        cache.set("a", 1)
        cache.set("b", 2)
        cache.delete("a")
        assert cache.get("a") is None
        assert cache.get("b") == 2


# ─── MemoryCache.exists ───────────────────────────────────────


class TestMemoryCacheExists:
    def test_exists_present(self, cache: MemoryCache):
        cache.set("k", "v")
        assert cache.exists("k") is True

    def test_exists_missing(self, cache: MemoryCache):
        assert cache.exists("nope") is False

    def test_exists_expired(self, cache: MemoryCache, monkeypatch):
        monkeypatch.setattr(time, "time", lambda: 1000.0)
        cache.set("k", "v", ttl=5)
        monkeypatch.setattr(time, "time", lambda: 1006.0)
        assert cache.exists("k") is False

    def test_exists_expired_removes_entry(self, cache: MemoryCache, monkeypatch):
        monkeypatch.setattr(time, "time", lambda: 1000.0)
        cache.set("k", "v", ttl=5)
        monkeypatch.setattr(time, "time", lambda: 1006.0)
        cache.exists("k")  # triggers lazy removal
        assert cache.size() == 0


# ─── MemoryCache.increment ────────────────────────────────────


class TestMemoryCacheIncrement:
    def test_increment_existing(self, cache: MemoryCache):
        cache.set("counter", 10)
        assert cache.increment("counter") == 11
        assert cache.get("counter") == 11

    def test_increment_nonexistent_creates_key(self, cache: MemoryCache):
        assert cache.increment("new_counter") == 1
        assert cache.get("new_counter") == 1

    def test_increment_with_amount(self, cache: MemoryCache):
        cache.set("c", 5)
        assert cache.increment("c", amount=3) == 8

    def test_increment_negative_amount(self, cache: MemoryCache):
        cache.set("c", 10)
        assert cache.increment("c", amount=-3) == 7

    def test_increment_preserves_ttl(self, cache: MemoryCache, monkeypatch):
        monkeypatch.setattr(time, "time", lambda: 1000.0)
        cache.set("c", 1, ttl=30)
        cache.increment("c")
        # still within TTL
        monkeypatch.setattr(time, "time", lambda: 1020.0)
        assert cache.get("c") == 2
        # expired
        monkeypatch.setattr(time, "time", lambda: 1031.0)
        assert cache.get("c") is None

    def test_increment_non_int_value_treated_as_zero(self, cache: MemoryCache):
        cache.set("c", "not_a_number")
        assert cache.increment("c") == 1

    def test_increment_expired_key_resets(self, cache: MemoryCache, monkeypatch):
        monkeypatch.setattr(time, "time", lambda: 1000.0)
        cache.set("c", 10, ttl=5)
        monkeypatch.setattr(time, "time", lambda: 1010.0)
        # expired entry -> starts from 0
        assert cache.increment("c") == 1


# ─── MemoryCache.clear ────────────────────────────────────────


class TestMemoryCacheClear:
    def test_clear_all(self, cache: MemoryCache):
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        assert cache.clear() == 3
        assert cache.size() == 0

    def test_clear_by_prefix(self, cache: MemoryCache):
        cache.set("rule:a", 1)
        cache.set("rule:b", 2)
        cache.set("camera:c", 3)
        removed = cache.clear(prefix="rule:")
        assert removed == 2
        assert cache.size() == 1
        assert cache.get("camera:c") == 3

    def test_clear_prefix_no_match(self, cache: MemoryCache):
        cache.set("a", 1)
        assert cache.clear(prefix="zzz:") == 0
        assert cache.size() == 1

    def test_clear_empty_cache(self, cache: MemoryCache):
        assert cache.clear() == 0


# ─── MemoryCache.keys ─────────────────────────────────────────


class TestMemoryCacheKeys:
    def test_keys_wildcard(self, cache: MemoryCache):
        cache.set("rule:intrusion:cam1", 1)
        cache.set("rule:loitering:cam1", 2)
        cache.set("camera:cam1", 3)
        matched = cache.keys("rule:*")
        assert sorted(matched) == ["rule:intrusion:cam1", "rule:loitering:cam1"]

    def test_keys_question_mark(self, cache: MemoryCache):
        cache.set("k1", 1)
        cache.set("k2", 2)
        cache.set("abc", 3)
        matched = cache.keys("k?")
        assert sorted(matched) == ["k1", "k2"]

    def test_keys_no_match(self, cache: MemoryCache):
        cache.set("a", 1)
        assert cache.keys("zzz*") == []

    def test_keys_empty_cache(self, cache: MemoryCache):
        assert cache.keys("*") == []


# ─── MemoryCache.size ─────────────────────────────────────────


class TestMemoryCacheSize:
    def test_size_initial(self, cache: MemoryCache):
        assert cache.size() == 0

    def test_size_after_sets(self, cache: MemoryCache):
        cache.set("a", 1)
        cache.set("b", 2)
        assert cache.size() == 2

    def test_size_after_delete(self, cache: MemoryCache):
        cache.set("a", 1)
        cache.set("b", 2)
        cache.delete("a")
        assert cache.size() == 1


# ─── MemoryCache LRU ──────────────────────────────────────────


class TestMemoryCacheLRU:
    def test_lru_eviction(self, cache: MemoryCache):
        """When max_size is exceeded, the oldest (least recently used) entry is evicted."""
        cache._max_size = 3
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        # cache full; adding a new entry should evict "a"
        cache.set("d", 4)
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3
        assert cache.get("d") == 4

    def test_lru_get_moves_to_end(self, cache: MemoryCache):
        """Accessing an entry moves it to the end (most recently used)."""
        cache._max_size = 3
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        # access "a" so it becomes most recently used
        cache.get("a")
        # now adding "d" should evict "b" (the actual LRU entry)
        cache.set("d", 4)
        assert cache.get("a") == 1
        assert cache.get("b") is None
        assert cache.get("c") == 3
        assert cache.get("d") == 4

    def test_lru_overwrite_moves_to_end(self, cache: MemoryCache):
        """Overwriting an existing key also moves it to the end."""
        cache._max_size = 3
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        cache.set("a", 10)  # overwrite "a", moves to end
        cache.set("d", 4)  # should evict "b"
        assert cache.get("a") == 10
        assert cache.get("b") is None


# ─── RedisCache (mocked) ──────────────────────────────────────


class TestRedisCache:
    def _make_mocked_redis_cache(self):
        """Create a RedisCache with a mocked redis connection."""
        cache = RedisCache.__new__(RedisCache)
        cache._prefix = "va:"
        cache._conn = MagicMock()
        return cache

    def test_get_hit(self):
        cache = self._make_mocked_redis_cache()
        cache._conn.get.return_value = b'"hello"'
        assert cache.get("k") == "hello"
        cache._conn.get.assert_called_once_with("va:k")

    def test_get_miss(self):
        cache = self._make_mocked_redis_cache()
        cache._conn.get.return_value = None
        assert cache.get("k") is None

    def test_get_no_connection(self):
        cache = self._make_mocked_redis_cache()
        cache._conn = None
        assert cache.get("k") is None

    def test_get_exception_returns_none(self):
        cache = self._make_mocked_redis_cache()
        cache._conn.get.side_effect = Exception("connection lost")
        assert cache.get("k") is None

    def test_set_without_ttl(self):
        cache = self._make_mocked_redis_cache()
        cache.set("k", "v")
        cache._conn.set.assert_called_once_with("va:k", '"v"')

    def test_set_with_ttl(self):
        cache = self._make_mocked_redis_cache()
        cache.set("k", "v", ttl=60)
        cache._conn.setex.assert_called_once_with("va:k", 60, '"v"')

    def test_set_no_connection(self):
        cache = self._make_mocked_redis_cache()
        cache._conn = None
        cache.set("k", "v")  # should not raise

    def test_delete_hit(self):
        cache = self._make_mocked_redis_cache()
        cache._conn.delete.return_value = 1
        assert cache.delete("k") is True

    def test_delete_miss(self):
        cache = self._make_mocked_redis_cache()
        cache._conn.delete.return_value = 0
        assert cache.delete("k") is False

    def test_exists(self):
        cache = self._make_mocked_redis_cache()
        cache._conn.exists.return_value = 1
        assert cache.exists("k") is True
        cache._conn.exists.assert_called_once_with("va:k")

    def test_exists_no_connection(self):
        cache = self._make_mocked_redis_cache()
        cache._conn = None
        assert cache.exists("k") is False

    def test_increment(self):
        cache = self._make_mocked_redis_cache()
        cache._conn.incrby.return_value = 5
        assert cache.increment("c", amount=2) == 5
        cache._conn.incrby.assert_called_once_with("va:c", 2)

    def test_increment_no_connection(self):
        cache = self._make_mocked_redis_cache()
        cache._conn = None
        assert cache.increment("c") == 0

    def test_size(self):
        cache = self._make_mocked_redis_cache()
        cache._conn.dbsize.return_value = 42
        assert cache.size() == 42

    def test_size_no_connection(self):
        cache = self._make_mocked_redis_cache()
        cache._conn = None
        assert cache.size() == 0

    def test_ping_success(self):
        cache = self._make_mocked_redis_cache()
        cache._conn.ping.return_value = True
        assert cache.ping() is True

    def test_ping_no_connection(self):
        cache = self._make_mocked_redis_cache()
        cache._conn = None
        assert cache.ping() is False

    def test_close(self):
        cache = self._make_mocked_redis_cache()
        mock_conn = cache._conn
        cache.close()
        mock_conn.close.assert_called_once()
        assert cache._conn is None

    def test_close_no_connection(self):
        cache = self._make_mocked_redis_cache()
        cache._conn = None
        cache.close()  # should not raise

    def test_keys(self):
        cache = self._make_mocked_redis_cache()
        # simulate scan returning bytes with prefix
        cache._conn.scan.side_effect = [
            (0, [b"va:rule:a", b"va:rule:b"]),
        ]
        result = cache.keys("rule:*")
        assert sorted(result) == ["rule:a", "rule:b"]

    def test_keys_no_connection(self):
        cache = self._make_mocked_redis_cache()
        cache._conn = None
        assert cache.keys("*") == []

    def test_clear(self):
        cache = self._make_mocked_redis_cache()
        cache._conn.scan.side_effect = [
            (0, [b"va:rule:a", b"va:rule:b"]),
        ]
        cache._conn.delete.return_value = 2
        assert cache.clear(prefix="rule:") == 2

    def test_clear_no_connection(self):
        cache = self._make_mocked_redis_cache()
        cache._conn = None
        assert cache.clear() == 0

    def test_serialize_primitives(self):
        assert RedisCache._serialize("hello") == '"hello"'
        assert RedisCache._serialize(42) == "42"
        assert RedisCache._serialize(3.14) == "3.14"

    def test_serialize_complex(self):
        result = RedisCache._serialize({"a": 1})
        assert '"a"' in result
        assert "1" in result

    def test_deserialize_json(self):
        assert RedisCache._deserialize(b'"hello"') == "hello"
        assert RedisCache._deserialize(b"42") == 42

    def test_deserialize_fallback(self):
        """Non-JSON bytes fall back to decoded string."""
        assert RedisCache._deserialize(b"not-json") == "not-json"


# ─── create_cache ─────────────────────────────────────────────


class TestCreateCache:
    def test_create_cache_no_config(self):
        cache = create_cache()
        assert isinstance(cache, MemoryCache)
        cache.close()

    def test_create_cache_redis_not_enabled(self):
        cache = create_cache({"enabled": False})
        assert isinstance(cache, MemoryCache)
        cache.close()

    def test_create_cache_empty_config(self):
        cache = create_cache({})
        assert isinstance(cache, MemoryCache)
        cache.close()

    @patch("vision_agent.storage.cache.RedisCache")
    def test_create_cache_redis_enabled_ping_ok(self, mock_cls):
        mock_instance = MagicMock()
        mock_instance.ping.return_value = True
        mock_cls.return_value = mock_instance

        cache = create_cache({"enabled": True, "host": "localhost", "port": 6379})
        assert cache is mock_instance

    @patch("vision_agent.storage.cache.RedisCache")
    def test_create_cache_redis_ping_fails_fallback(self, mock_cls):
        mock_instance = MagicMock()
        mock_instance.ping.return_value = False
        mock_cls.return_value = mock_instance

        cache = create_cache({"enabled": True})
        assert isinstance(cache, MemoryCache)
        cache.close()

    @patch("vision_agent.storage.cache.RedisCache")
    def test_create_cache_redis_exception_fallback(self, mock_cls):
        mock_cls.side_effect = Exception("connection refused")

        cache = create_cache({"enabled": True})
        assert isinstance(cache, MemoryCache)
        cache.close()


# ─── CacheProtocol conformance ────────────────────────────────


class TestCacheProtocolConformance:
    def test_memory_cache_satisfies_protocol(self, cache: MemoryCache):
        from vision_agent.storage.cache import CacheProtocol

        assert isinstance(cache, CacheProtocol)
