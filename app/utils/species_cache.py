"""
A small get/set cache for identify_plant_by_name's results, keyed by the
normalized plant name — see that function's own docstring for why this is
safe to cache at all: the cached value is Gemini's best-effort species
reference facts (water_frequency_days, light_needs, soil_type,
fun_facts, ...), the same "no user control to change the state" class of
data plans.py and this app's PLANT COLLECTION RULES already treat
differently from anything a user can edit. It is never keyed by user or
plant id, and never holds anything a user typed that wasn't already a
plant name — so a cache hit for "Money Plant" is identical, correct
reference data for every user who ever types that name, forever (until
the TTL below expires it, purely to let the wording quietly improve over
time, not because a stale entry would ever become *wrong*).

Two backends, same tiny interface, chosen by whether REDIS_URL is set:

- Redis (real infra: self-hosted, or a free-tier managed one). "Self-
  hosted" is the honest option, not necessarily the practical one for a
  single Render web service — Render's own free tier doesn't include a
  Redis instance any more, and running a second always-on process next
  to the API on a free plan isn't realistic. Upstash's free tier
  (REST-based, serverless, no server to manage) is the actual "free if
  self-hosted"-equivalent option worth pointing at instead, and this
  module's redis.asyncio client works with it exactly like any other
  Redis URL — no special-casing needed. Shared across every backend
  instance, and survives a redeploy.
- In-process dict (the default, REDIS_URL unset). Works immediately,
  zero setup, zero cost. Only lives as long as the current process — a
  redeploy or restart empties it, which just means the next lookup of
  each name costs one more Gemini call, exactly as if this cache didn't
  exist yet. Never a correctness risk, only a smaller savings window.

Every call is wrapped so a cache backend problem (Redis unreachable,
whatever) degrades to "treat as a miss" rather than ever breaking the
actual identify-by-name flow — this is a pure optimization, and must
never be able to turn into an outage.
"""
import json
import time
from typing import Any, Dict, Optional

from app.core.config import get_settings

settings = get_settings()

# Long TTL on purpose: this is stable reference data (a plant's typical
# watering interval doesn't change), not a live value — the point of the
# TTL is only to let entries quietly refresh if the prompt/model improves
# later, not to protect against staleness in any normal sense.
_DEFAULT_TTL_SECONDS = 30 * 24 * 3600  # 30 days

_redis_client = None  # lazily created; None forever if redis_url is unset


def _normalize_key(plant_name: str) -> str:
    return "species_cache:" + " ".join(plant_name.strip().lower().split())


def _get_redis():
    global _redis_client
    if not settings.redis_url:
        return None
    if _redis_client is None:
        import redis.asyncio as redis  # imported lazily so the in-process fallback needs no package at runtime

        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


# In-process fallback store: key -> (expires_at_epoch_seconds, value)
_memory_store: Dict[str, tuple[float, Dict[str, Any]]] = {}


def _memory_get(key: str) -> Optional[Dict[str, Any]]:
    entry = _memory_store.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if time.time() >= expires_at:
        del _memory_store[key]
        return None
    return value


def _memory_set(key: str, value: Dict[str, Any], ttl_seconds: int) -> None:
    _memory_store[key] = (time.time() + ttl_seconds, value)


async def get_cached_species(plant_name: str) -> Optional[Dict[str, Any]]:
    key = _normalize_key(plant_name)
    client = _get_redis()
    if client is not None:
        try:
            raw = await client.get(key)
            return json.loads(raw) if raw else None
        except Exception:
            return None  # cache problem -> treat as a miss, never fail the request
    return _memory_get(key)


async def set_cached_species(plant_name: str, value: Dict[str, Any], ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
    key = _normalize_key(plant_name)
    client = _get_redis()
    if client is not None:
        try:
            await client.set(key, json.dumps(value), ex=ttl_seconds)
            return
        except Exception:
            pass  # fall through to the in-process store rather than losing the write entirely
    _memory_set(key, value, ttl_seconds)
