"""
缓存层模块 — 高性能键值对缓存（内存 + Redis 可选）

设计来源：docs/modules/storage/cache.md

职责：
- CacheProtocol 统一接口
- MemoryCache：基于 dict + TTL + LRU 的内存缓存
- RedisCache：可选的 Redis 实现（分布式部署）
- 自动降级：Redis 不可用时回退到内存缓存
- 惰性过期 + 定期清理双策略

设计决策：
- Redis 是可选增强，不是硬依赖
- 运行时不自动切换回 Redis（避免缓存状态丢失）
- OrderedDict 实现 LRU 淘汰
"""

from __future__ import annotations

import fnmatch
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ─── 协议接口 ────────────────────────────────────────────────


@runtime_checkable
class CacheProtocol(Protocol):
    """缓存协议接口（cache.md 2.1 节）"""

    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl: int | None = None) -> None: ...
    def delete(self, key: str) -> bool: ...
    def exists(self, key: str) -> bool: ...
    def increment(self, key: str, amount: int = 1) -> int: ...
    def clear(self, prefix: str | None = None) -> int: ...
    def keys(self, pattern: str) -> list[str]: ...
    def size(self) -> int: ...


# ─── 内存缓存 ────────────────────────────────────────────────


class MemoryCache:
    """内存缓存（cache.md 2.2 节 / 3.2 节）

    特性：
    - TTL 支持（惰性过期 + 定期清理）
    - LRU 淘汰（OrderedDict）
    - 最大容量限制
    - 后台清理线程
    """

    def __init__(
        self,
        max_size: int = 10000,
        cleanup_interval: int = 60,
    ) -> None:
        self._store: OrderedDict[str, tuple[Any, float | None]] = OrderedDict()
        self._max_size = max_size
        self._cleanup_interval = cleanup_interval
        self._lock = threading.Lock()

        # 后台清理线程
        self._cleanup_thread: threading.Thread | None = None
        self._running = False
        self._start_cleanup_thread()

    # ─── 公开接口 ──────────────────────────────────────────────

    def get(self, key: str) -> Any | None:
        """获取缓存值，过期条目惰性删除"""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expire_at = entry
            if expire_at is not None and time.time() > expire_at:
                del self._store[key]
                return None
            # LRU：移到末尾
            self._store.move_to_end(key)
            return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """设置缓存值"""
        expire_at = (time.time() + ttl) if ttl is not None else None
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (value, expire_at)
            self._enforce_max_size()

    def delete(self, key: str) -> bool:
        """删除缓存条目"""
        with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False

    def exists(self, key: str) -> bool:
        """检查缓存条目是否存在（未过期）"""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False
            _, expire_at = entry
            if expire_at is not None and time.time() > expire_at:
                del self._store[key]
                return False
            return True

    def increment(self, key: str, amount: int = 1) -> int:
        """原子递增"""
        with self._lock:
            entry = self._store.get(key)
            current = 0
            expire_at = None
            if entry is not None:
                val, exp = entry
                if exp is not None and time.time() > exp:
                    del self._store[key]
                else:
                    current = val if isinstance(val, int) else 0
                    expire_at = exp
            new_value = current + amount
            self._store[key] = (new_value, expire_at)
            return new_value

    def clear(self, prefix: str | None = None) -> int:
        """清除指定前缀或全部缓存条目"""
        with self._lock:
            if prefix is None:
                count = len(self._store)
                self._store.clear()
                return count
            keys_to_remove = [k for k in self._store if k.startswith(prefix)]
            for k in keys_to_remove:
                del self._store[k]
            return len(keys_to_remove)

    def clear_by_substring(self, substring: str) -> int:
        """清除包含指定子串的所有条目"""
        with self._lock:
            keys_to_remove = [k for k in self._store if substring in k]
            for k in keys_to_remove:
                del self._store[k]
            return len(keys_to_remove)

    def keys(self, pattern: str) -> list[str]:
        """通配符匹配查询 key"""
        with self._lock:
            return [k for k in self._store if fnmatch.fnmatch(k, pattern)]

    def size(self) -> int:
        """返回有效条目数（不清理过期条目）"""
        with self._lock:
            return len(self._store)

    def close(self) -> None:
        """停止清理线程"""
        self._running = False
        if self._cleanup_thread:
            self._cleanup_thread.join(timeout=2)
            self._cleanup_thread = None

    # ─── 内部方法 ──────────────────────────────────────────────

    def _start_cleanup_thread(self) -> None:
        """启动后台清理线程"""
        self._running = True
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            name="cache-cleanup",
            daemon=True,
        )
        self._cleanup_thread.start()

    def _cleanup_loop(self) -> None:
        """定期清理过期条目"""
        while self._running:
            time.sleep(self._cleanup_interval)
            if not self._running:
                break
            try:
                count = self._cleanup_expired()
                if count > 0:
                    logger.debug("cache_cleanup removed=%d", count)
            except Exception as e:
                logger.error("cache_cleanup_error error=%s", str(e))

    def _cleanup_expired(self) -> int:
        """清理所有过期条目"""
        now = time.time()
        with self._lock:
            expired = [
                k
                for k, (_, exp) in self._store.items()
                if exp is not None and now > exp
            ]
            for k in expired:
                del self._store[k]
            return len(expired)

    def _enforce_max_size(self) -> None:
        """强制执行最大容量限制（LRU 淘汰）"""
        while len(self._store) > self._max_size:
            self._store.popitem(last=False)


# ─── Redis 缓存 ──────────────────────────────────────────────


class RedisCache:
    """Redis 缓存实现（cache.md 2.3 节 / 3.3 节）

    可选依赖：需要 redis-py 库。
    所有操作包装在 try-except 中，失败时返回 None/False。
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        config = config or {}
        self._prefix = config.get("key_prefix", "va:")
        self._conn = None

        try:
            import redis

            pool = redis.ConnectionPool(
                host=config.get("host", "localhost"),
                port=config.get("port", 6379),
                db=config.get("db", 0),
                password=config.get("password"),
                socket_timeout=config.get("socket_timeout", 5),
                socket_connect_timeout=config.get("socket_timeout", 5),
                max_connections=config.get("max_connections", 20),
                retry_on_timeout=True,
            )
            self._conn = redis.Redis(connection_pool=pool)
        except ImportError:
            logger.warning("redis_not_installed action=skip")
        except Exception as e:
            logger.error("redis_connect_failed error=%s", str(e))

    def _key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def get(self, key: str) -> Any | None:
        if not self._conn:
            return None
        try:
            raw = self._conn.get(self._key(key))
            if raw is None:
                return None
            return self._deserialize(raw)
        except Exception as e:
            logger.warning("redis_get_error key=%s error=%s", key, str(e))
            return None

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        if not self._conn:
            return
        try:
            serialized = self._serialize(value)
            if ttl is not None and ttl > 0:
                self._conn.setex(self._key(key), ttl, serialized)
            else:
                self._conn.set(self._key(key), serialized)
        except Exception as e:
            logger.warning("redis_set_error key=%s error=%s", key, str(e))

    def delete(self, key: str) -> bool:
        if not self._conn:
            return False
        try:
            return bool(self._conn.delete(self._key(key)))
        except Exception as e:
            logger.warning("redis_delete_error key=%s error=%s", key, str(e))
            return False

    def exists(self, key: str) -> bool:
        if not self._conn:
            return False
        try:
            return bool(self._conn.exists(self._key(key)))
        except Exception as e:
            logger.warning("redis_exists_error key=%s error=%s", key, str(e))
            return False

    def increment(self, key: str, amount: int = 1) -> int:
        if not self._conn:
            return 0
        try:
            return int(self._conn.incrby(self._key(key), amount))
        except Exception as e:
            logger.warning("redis_incr_error key=%s error=%s", key, str(e))
            return 0

    def clear(self, prefix: str | None = None) -> int:
        if not self._conn:
            return 0
        try:
            pattern = self._key(prefix or "*")
            count = 0
            for batch_keys in self._scan_keys(pattern):
                if batch_keys:
                    count += self._conn.delete(*batch_keys)
            return count
        except Exception as e:
            logger.warning("redis_clear_error error=%s", str(e))
            return 0

    def keys(self, pattern: str) -> list[str]:
        if not self._conn:
            return []
        try:
            result = []
            for batch_keys in self._scan_keys(self._key(pattern)):
                result.extend(k.decode().removeprefix(self._prefix) for k in batch_keys)
            return result
        except Exception as e:
            logger.warning("redis_keys_error error=%s", str(e))
            return []

    def size(self) -> int:
        if not self._conn:
            return 0
        try:
            return int(self._conn.dbsize())
        except Exception:
            return 0

    def ping(self) -> bool:
        if not self._conn:
            return False
        try:
            return bool(self._conn.ping())
        except Exception:
            return False

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _scan_keys(self, pattern: str):
        """SCAN 分批迭代 key"""
        if not self._conn:
            return
        cursor = 0
        while True:
            cursor, keys = self._conn.scan(cursor, match=pattern, count=100)
            if keys:
                yield keys
            if cursor == 0:
                break

    @staticmethod
    def _serialize(value: Any) -> str:
        if isinstance(value, (str, int, float)):
            return json.dumps(value)
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _deserialize(raw: bytes) -> Any:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw.decode()


# ─── 工厂函数 ────────────────────────────────────────────────


def create_cache(config: dict[str, Any] | None = None) -> CacheProtocol:
    """根据配置创建缓存实例（cache.md 3.1 节）

    Redis 不可用时自动降级为内存缓存。

    Args:
        config: redis 配置段

    Returns:
        缓存实例
    """
    config = config or {}
    redis_config = config if config.get("enabled") else {}

    if not redis_config.get("enabled"):
        logger.info("cache_mode mode=memory reason=redis_not_enabled")
        return MemoryCache()

    # 尝试创建 Redis 缓存
    try:
        cache = RedisCache(redis_config)
        if cache.ping():
            logger.info(
                "cache_mode mode=redis host=%s", redis_config.get("host", "localhost")
            )
            return cache
        else:
            logger.warning("cache_fallback mode=memory reason=redis_ping_failed")
            return MemoryCache()
    except Exception as e:
        logger.warning("cache_fallback mode=memory reason=%s", str(e))
        return MemoryCache()
