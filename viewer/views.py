import importlib.util
import json
import os
import time
import csv
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from .forms import CollectQueryForm, RegistrationForm
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer


PROJECT_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_DIR = Path(__file__).resolve().parents[2]
V1_DEMO_DIR = WORKSPACE_DIR / "v1_demo"


def _resolve_data_dir():
    env_path = os.getenv("TWEET_DATA_DIR", "").strip()
    if env_path:
        return Path(env_path).expanduser()

    local_data_dir = PROJECT_DIR / "data"
    if local_data_dir.exists():
        return local_data_dir

    legacy_data_dir = V1_DEMO_DIR / "data"
    if legacy_data_dir.exists():
        return legacy_data_dir

    return local_data_dir


DATA_DIR = _resolve_data_dir()
EXAMPLE_DATA_DIR = Path(os.getenv("TWEET_EXAMPLE_DATA_DIR", str(PROJECT_DIR / "data"))).expanduser()
USER_SPACES_DIR = Path(os.getenv("TWEET_USER_SPACES_DIR", str(DATA_DIR / "__userspaces__"))).expanduser()
WORKSPACE_SEEDED_MARKER = ".workspace_seeded"
EXPORT_MAX_ROWS = 50000

_VADER = SentimentIntensityAnalyzer()
_RUN_SUMMARY_CACHE = {}
_RUN_SUMMARY_CACHE_MAX = 600
_RATE_LIMIT_RESET_RE = re.compile(r"Retry after reset:\s*([0-9]{9,})")


def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError, AttributeError):
        return int(default)


def _env_float(name, default):
    try:
        return float(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError, AttributeError):
        return float(default)


RATE_LIMIT_MAX_RETRIES = max(0, _env_int("X_RATE_LIMIT_MAX_RETRIES", 4))
RATE_LIMIT_MAX_WAIT_SECONDS = max(0, _env_int("X_RATE_LIMIT_MAX_WAIT_SECONDS", 300))
RATE_LIMIT_BASE_BACKOFF_SECONDS = max(1.0, _env_float("X_RATE_LIMIT_BACKOFF_SECONDS", 2.0))


class RateLimitDeferredError(RuntimeError):
    def __init__(self, message, reset_ts=None, retry_after=None, attempts=0, waited_seconds=0):
        super().__init__(message)
        self.reset_ts = reset_ts
        self.retry_after = retry_after
        self.attempts = attempts
        self.waited_seconds = waited_seconds


def _iter_event_dirs(data_dir):
    if not data_dir.exists() or not data_dir.is_dir():
        return []
    return sorted(
        (path for path in data_dir.iterdir() if path.is_dir() and not path.name.startswith("__")),
        key=lambda path: path.name.lower(),
    )


def _workspace_name(username):
    raw = (str(username or "")).strip().lower()
    safe = "".join(char if (char.isalnum() or char in {"_", "-"}) else "_" for char in raw)
    safe = safe.strip("_-")
    return safe or "user"


def _seed_workspace(workspace_dir):
    marker_path = workspace_dir / WORKSPACE_SEEDED_MARKER
    if marker_path.exists():
        return

    workspace_dir.mkdir(parents=True, exist_ok=True)
    has_user_data = any(path.is_dir() and not path.name.startswith("__") for path in workspace_dir.iterdir())
    if not has_user_data and EXAMPLE_DATA_DIR.exists() and EXAMPLE_DATA_DIR.is_dir():
        for src_event_dir in _iter_event_dirs(EXAMPLE_DATA_DIR):
            dst_event_dir = workspace_dir / src_event_dir.name
            if dst_event_dir.exists():
                continue
            try:
                shutil.copytree(src_event_dir, dst_event_dir)
            except OSError:
                continue

    try:
        marker_path.write_text(datetime.utcnow().isoformat() + "Z", encoding="utf-8")
    except OSError:
        pass


def _workspace_data_dir(user):
    if not getattr(user, "is_authenticated", False):
        return DATA_DIR

    workspace_dir = USER_SPACES_DIR / _workspace_name(getattr(user, "username", "user"))
    _seed_workspace(workspace_dir)
    return workspace_dir


def _sentiment_compound(text):
    if not text:
        return 0.0
    try:
        text_value = str(text)
    except Exception:
        return 0.0
    try:
        return float((_VADER.polarity_scores(text_value) or {}).get("compound", 0.0))
    except Exception:
        return 0.0


def _sentiment_label(compound):
    # Keep thresholds consistent with v1_demo/preprocess_tweets.py.
    if compound > 0.4:
        return "Positive"
    if compound < -0.4:
        return "Negative"
    return "Neutral"


def _normalize_sentiment(value):
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized or normalized in {"all", "any"}:
        return None
    if normalized in {"positive", "pos", "p", "+"}:
        return "Positive"
    if normalized in {"neutral", "neu", "n", "0"}:
        return "Neutral"
    if normalized in {"negative", "neg", "m", "-"}:
        return "Negative"
    return None


def _parse_bounded_int(value, default, min_value, max_value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, min_value), max_value)


def _normalize_text(text_value):
    try:
        text = str(text_value or "")
    except Exception:
        return ""
    text = text.replace("\r", " ").replace("\n", " ").strip()
    if len(text) > 320:
        text = text[:320] + "..."
    return text


def _run_summary_cache_key(json_path):
    try:
        stat = json_path.stat()
    except OSError:
        return None
    return str(json_path.resolve()), stat.st_mtime_ns, stat.st_size


def _empty_run_summary():
    return {
        "tweet_count": 0,
        "total_likes": 0,
        "total_retweets": 0,
        "parse_errors": 0,
        "language_counts": Counter(),
        "user_counts": Counter(),
        "daily_counts": Counter(),
        "sentiment_counts": Counter(),
    }


def _cache_run_summary(cache_key, summary):
    if cache_key is None:
        return summary
    _RUN_SUMMARY_CACHE[cache_key] = summary
    if len(_RUN_SUMMARY_CACHE) > _RUN_SUMMARY_CACHE_MAX:
        overflow = len(_RUN_SUMMARY_CACHE) - _RUN_SUMMARY_CACHE_MAX
        for stale_key in list(_RUN_SUMMARY_CACHE.keys())[:overflow]:
            _RUN_SUMMARY_CACHE.pop(stale_key, None)
    return summary


def _analyze_run_file(json_path):
    cache_key = _run_summary_cache_key(json_path)
    if cache_key is not None:
        cached = _RUN_SUMMARY_CACHE.get(cache_key)
        if cached is not None:
            return cached

    summary = _empty_run_summary()
    if not json_path.exists():
        return _cache_run_summary(cache_key, summary)

    file_size = json_path.stat().st_size
    if file_size == 0:
        return _cache_run_summary(cache_key, summary)

    for item in _iter_tweets(json_path):
        summary["tweet_count"] += 1
        summary["total_likes"] += _safe_int(item.get("likeCount"))
        summary["total_retweets"] += _safe_int(item.get("retweetCount"))

        lang = item.get("lang") or "unknown"
        summary["language_counts"][lang] += 1

        username = ((item.get("user") or {}).get("username")) or "unknown"
        summary["user_counts"][username] += 1

        day = (item.get("date") or "")[:10]
        if day:
            summary["daily_counts"][day] += 1

        text = item.get("renderedContent") or item.get("content") or ""
        summary["sentiment_counts"][_sentiment_label(_sentiment_compound(text))] += 1

    if summary["tweet_count"] == 0 and file_size > 0:
        summary["parse_errors"] = 1

    return _cache_run_summary(cache_key, summary)


def _collect_preview_tweets(event_names, selected_event, sentiment_filter, text_query, user_query, limit, offset, data_dir):
    if selected_event != "all" and selected_event not in event_names:
        return []

    scope_events = event_names if selected_event == "all" else [selected_event]
    candidate_runs = []
    for event_name in scope_events:
        candidate_runs.extend(_run_entries(data_dir / event_name))
    candidate_runs = sorted(candidate_runs, key=lambda run: run["updated_ts"], reverse=True)

    normalized_text_query = (text_query or "").strip().lower()
    normalized_user_query = (user_query or "").strip().lower()

    rows = []
    matches_seen = 0

    for run in candidate_runs:
        if run["is_empty"]:
            continue
        run_summary = _analyze_run_file(run["path"])
        if run_summary["tweet_count"] == 0:
            continue
        if sentiment_filter and run_summary["sentiment_counts"].get(sentiment_filter, 0) == 0:
            continue

        for item in _iter_tweets(run["path"]):
            user_obj = item.get("user") or {}
            username = (user_obj.get("username") or "unknown").strip()
            text = item.get("renderedContent") or item.get("content") or ""

            compound = _sentiment_compound(text)
            sentiment_label = _sentiment_label(compound)

            if sentiment_filter and sentiment_label != sentiment_filter:
                continue

            if normalized_text_query and normalized_text_query not in str(text).lower():
                continue

            if normalized_user_query and normalized_user_query not in username.lower():
                continue

            if matches_seen < offset:
                matches_seen += 1
                continue

            if len(rows) >= limit:
                return rows

            matches_seen += 1
            rows.append(
                {
                    "event": run["event"],
                    "run_file": run["file_name"],
                    "id": item.get("id"),
                    "date": item.get("date"),
                    "username": username,
                    "lang": item.get("lang"),
                    "likes": item.get("likeCount"),
                    "retweets": item.get("retweetCount"),
                    "sentiment": sentiment_label,
                    "compound": round(compound, 3),
                    "text": _normalize_text(text),
                }
            )

    return rows


def _csv_download_response(filename, header, rows):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return response


def _build_run_filter_state(request):
    try:
        limit = int(request.GET.get("limit", "120"))
    except ValueError:
        limit = 250
    limit = min(max(limit, 20), 1500)

    text_filter = (request.GET.get("q") or "").strip().lower()
    sentiment_filter = _normalize_sentiment(request.GET.get("sentiment"))
    return {
        "limit": limit,
        "text_filter": text_filter,
        "sentiment_filter": sentiment_filter,
    }


def _event_dir(event, data_dir):
    event_dir = data_dir / event
    if not event_dir.exists() or not event_dir.is_dir():
        raise Http404(f"Unknown event: {event}")
    return event_dir


def _run_entries(event_dir):
    json_dir = event_dir / "jsons"
    if not json_dir.exists():
        return []

    entries = []
    for path in sorted(json_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        stat = path.stat()
        entries.append(
            {
                "event": event_dir.name,
                "file_name": path.name,
                "stem": path.stem,
                "size": stat.st_size,
                "is_empty": stat.st_size == 0,
                "updated_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "updated_ts": stat.st_mtime,
                "path": path,
            }
        )
    return entries


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _read_log_tail(log_path, lines=80):
    if not log_path.exists():
        return ""
    with log_path.open("r", encoding="utf-8", errors="replace") as fp:
        data = fp.readlines()
    return "".join(data[-lines:])


def _csv_row_count(csv_path):
    if not csv_path.exists():
        return None
    with csv_path.open("r", encoding="utf-8", errors="replace") as fp:
        row_count = sum(1 for _ in fp)
    return max(row_count - 1, 0)


def _iter_tweets(json_path):
    with json_path.open("r", encoding="utf-8", errors="replace") as fp:
        for line in fp:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _dataset_snapshot(event_filter="all", data_dir=DATA_DIR):
    selected_event = event_filter or "all"
    event_names = [path.name for path in _iter_event_dirs(data_dir)]
    event_cards = []

    total_runs = 0
    non_empty_runs = 0
    total_tweets = 0
    total_likes = 0
    total_retweets = 0
    parse_errors = 0

    language_counts = Counter()
    user_counts = Counter()
    daily_counts = Counter()
    sentiment_counts = Counter()
    recent_runs = []

    for event_name in event_names:
        if selected_event != "all" and event_name != selected_event:
            continue

        event_dir = data_dir / event_name
        runs = _run_entries(event_dir)
        total_runs += len(runs)
        non_empty_runs += sum(1 for run in runs if not run["is_empty"])
        recent_runs.extend(runs)

        event_tweets = 0
        for run in runs:
            if run["is_empty"]:
                run["tweet_count"] = 0
                continue

            run_summary = _analyze_run_file(run["path"])
            run_tweets = run_summary["tweet_count"]
            run["tweet_count"] = run_tweets

            total_tweets += run_tweets
            event_tweets += run_tweets
            total_likes += run_summary["total_likes"]
            total_retweets += run_summary["total_retweets"]
            parse_errors += run_summary["parse_errors"]

            language_counts.update(run_summary["language_counts"])
            user_counts.update(run_summary["user_counts"])
            daily_counts.update(run_summary["daily_counts"])
            sentiment_counts.update(run_summary["sentiment_counts"])

        event_cards.append(
            {
                "name": event_name,
                "runs": runs[:8],
                "run_count": len(runs),
                "non_empty_count": sum(1 for run in runs if not run["is_empty"]),
                "tweet_count": event_tweets,
            }
        )

    recent_runs = sorted(recent_runs, key=lambda run: run["updated_ts"], reverse=True)[:18]
    top_languages = language_counts.most_common(8)
    top_users = user_counts.most_common(10)

    sentiment_total = sum(sentiment_counts.values())
    positive_count = sentiment_counts.get("Positive", 0)
    neutral_count = sentiment_counts.get("Neutral", 0)
    negative_count = sentiment_counts.get("Negative", 0)

    if sentiment_total:
        positive_pct = round((positive_count / sentiment_total) * 100, 1)
        neutral_pct = round((neutral_count / sentiment_total) * 100, 1)
        negative_pct = round((negative_count / sentiment_total) * 100, 1)
    else:
        positive_pct = neutral_pct = negative_pct = 0.0

    sentiment_pie = {
        "pos": positive_pct,
        "pos_neu": min(round(positive_pct + neutral_pct, 1), 100.0),
        "neu": neutral_pct,
        "neg": negative_pct,
    }
    sentiment_rows = [
        {"label": "Positive", "count": positive_count, "pct": positive_pct, "css": "pos"},
        {"label": "Neutral", "count": neutral_count, "pct": neutral_pct, "css": "neu"},
        {"label": "Negative", "count": negative_count, "pct": negative_pct, "css": "neg"},
    ]

    trend_points = []
    for day, count in sorted(daily_counts.items()):
        trend_points.append({"label": day, "value": count})
    max_trend = max((point["value"] for point in trend_points), default=1)
    for point in trend_points:
        point["pct"] = max(6, int((point["value"] / max_trend) * 100)) if max_trend else 0

    return {
        "event_names": event_names,
        "selected_event": selected_event,
        "event_cards": event_cards,
        "recent_runs": recent_runs,
        "total_events": len(event_cards),
        "total_runs": total_runs,
        "non_empty_runs": non_empty_runs,
        "total_tweets": total_tweets,
        "total_likes": total_likes,
        "total_retweets": total_retweets,
        "avg_likes": round(total_likes / total_tweets, 2) if total_tweets else 0,
        "avg_retweets": round(total_retweets / total_tweets, 2) if total_tweets else 0,
        "parse_errors": parse_errors,
        "top_languages": top_languages,
        "top_users": top_users,
        "trend_points": trend_points[-14:],
        "sentiment_total": sentiment_total,
        "sentiment_pie": sentiment_pie,
        "sentiment_rows": sentiment_rows,
        "data_dir": str(data_dir),
    }


def _collector_module_candidates():
    env_module_path = os.getenv("COLLECTOR_MODULE_PATH", "").strip()
    candidates = []
    if env_module_path:
        candidates.append(Path(env_module_path).expanduser())

    candidates.append(PROJECT_DIR / "collect_tweets.py")
    candidates.append(V1_DEMO_DIR / "collect_tweets.py")

    seen = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        yield candidate


def _load_collector_class():
    for module_path in _collector_module_candidates():
        if not module_path.exists() or not module_path.is_file():
            continue

        spec = importlib.util.spec_from_file_location("tweet_collector_module", module_path)
        if spec is None or spec.loader is None:
            continue

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        collector_cls = getattr(module, "snscrape", None)
        if collector_cls is not None:
            return collector_cls

    raise RuntimeError(
        "Could not load collector module. Set COLLECTOR_MODULE_PATH or add collect_tweets.py to project root."
    )


def _extract_rate_limit_reset_ts(error):
    message = str(error or "")
    match = _RATE_LIMIT_RESET_RE.search(message)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _is_rate_limit_error(error):
    return "rate limit" in str(error or "").lower()


def _rate_limit_reset_iso(reset_ts):
    try:
        return datetime.fromtimestamp(int(reset_ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _run_collection(query, since, until, event, token, data_dir):
    (data_dir / event / "jsons").mkdir(parents=True, exist_ok=True)
    (data_dir / event / "csv").mkdir(parents=True, exist_ok=True)

    run_token = str(time.time()).replace(".", "-")
    had_token = "X_BEARER_TOKEN" in os.environ
    previous_token = os.environ.get("X_BEARER_TOKEN")
    had_data_dir = "TWEET_DATA_DIR" in os.environ
    previous_data_dir = os.environ.get("TWEET_DATA_DIR")
    os.environ["TWEET_DATA_DIR"] = str(data_dir)
    if token:
        os.environ["X_BEARER_TOKEN"] = token

    try:
        collector_cls = _load_collector_class()
        collector = collector_cls(query, since, until, run_token, event)
        collector.collect_tweets()
        return f"{since}_{until}_{run_token}.json"
    finally:
        if had_data_dir:
            os.environ["TWEET_DATA_DIR"] = previous_data_dir or ""
        else:
            os.environ.pop("TWEET_DATA_DIR", None)

        if token:
            if had_token:
                os.environ["X_BEARER_TOKEN"] = previous_token or ""
            else:
                os.environ.pop("X_BEARER_TOKEN", None)


def _run_collection_with_retry(query, since, until, event, token, data_dir):
    attempts = 0
    waited_total = 0
    max_attempts = max(1, RATE_LIMIT_MAX_RETRIES + 1)

    while attempts < max_attempts:
        attempts += 1
        try:
            run_filename = _run_collection(query, since, until, event, token, data_dir)
            return run_filename, {"attempts": attempts, "waited_seconds": waited_total}
        except Exception as exc:
            if not _is_rate_limit_error(exc):
                raise

            reset_ts = _extract_rate_limit_reset_ts(exc)
            now_ts = int(time.time())
            retry_after = 0

            if reset_ts and reset_ts > now_ts:
                retry_after = max(1, reset_ts - now_ts + 1)
            else:
                retry_after = int(round(RATE_LIMIT_BASE_BACKOFF_SECONDS * (2 ** (attempts - 1))))
                retry_after = max(1, retry_after)

            if attempts >= max_attempts:
                raise RateLimitDeferredError(
                    f"X API rate limit persisted after {attempts} attempts.",
                    reset_ts=reset_ts,
                    retry_after=retry_after,
                    attempts=attempts,
                    waited_seconds=waited_total,
                ) from exc

            if waited_total + retry_after > RATE_LIMIT_MAX_WAIT_SECONDS:
                raise RateLimitDeferredError(
                    "X API rate limit window is longer than auto-retry max wait.",
                    reset_ts=reset_ts,
                    retry_after=retry_after,
                    attempts=attempts,
                    waited_seconds=waited_total,
                ) from exc

            time.sleep(retry_after)
            waited_total += retry_after


def _dashboard_response(request, query_form, data_dir):
    selected_event = request.GET.get("event", "all")
    snapshot = _dataset_snapshot(selected_event, data_dir=data_dir)
    rate_limit_reset_ts = _parse_bounded_int(request.GET.get("rate_limit_reset"), 0, 0, 4102444800)
    rate_limit_reset_iso = _rate_limit_reset_iso(rate_limit_reset_ts) if rate_limit_reset_ts else ""

    browser_sentiment = _normalize_sentiment(request.GET.get("sentiment"))
    browser_q = (request.GET.get("q") or "").strip()
    browser_user = (request.GET.get("user") or "").strip()
    browser_limit = _parse_bounded_int(request.GET.get("limit"), 120, 20, 800)
    browser_offset = _parse_bounded_int(request.GET.get("offset"), 0, 0, 50000)

    export_csv = (request.GET.get("export") or "").strip().lower() == "csv"
    if export_csv:
        export_rows = _collect_preview_tweets(
            snapshot["event_names"],
            snapshot["selected_event"],
            browser_sentiment,
            browser_q,
            browser_user,
            EXPORT_MAX_ROWS,
            0,
            data_dir,
        )
        export_header = [
            "event",
            "run_file",
            "tweet_id",
            "date",
            "username",
            "lang",
            "likes",
            "retweets",
            "sentiment",
            "compound",
            "text",
        ]
        export_body = [
            [
                row.get("event"),
                row.get("run_file"),
                row.get("id"),
                row.get("date"),
                row.get("username"),
                row.get("lang"),
                row.get("likes"),
                row.get("retweets"),
                row.get("sentiment"),
                row.get("compound"),
                row.get("text"),
            ]
            for row in export_rows
        ]
        export_scope = snapshot["selected_event"] or "all"
        export_filename = f"tweet_browser_{export_scope}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
        return _csv_download_response(export_filename, export_header, export_body)

    browser_rows = _collect_preview_tweets(
        snapshot["event_names"],
        snapshot["selected_event"],
        browser_sentiment,
        browser_q,
        browser_user,
        browser_limit,
        browser_offset,
        data_dir,
    )

    context = {
        **snapshot,
        "workspace_dir": str(data_dir),
        "shared_example_dir": str(EXAMPLE_DATA_DIR),
        "query_form": query_form,
        "browser_sentiment": browser_sentiment,
        "browser_q": browser_q,
        "browser_user": browser_user,
        "browser_limit": browser_limit,
        "browser_offset": browser_offset,
        "browser_prev_offset": max(browser_offset - browser_limit, 0),
        "browser_next_offset": browser_offset + browser_limit,
        "browser_has_next": len(browser_rows) >= browser_limit,
        "browser_rows": browser_rows,
        "rate_limit_reset_ts": rate_limit_reset_ts,
        "rate_limit_reset_iso": rate_limit_reset_iso,
    }
    return render(request, "viewer/dashboard.html", context)


def register_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            _workspace_data_dir(user)
            login(request, user)
            messages.success(request, "Account created. Welcome to the dashboard.")
            next_url = request.GET.get("next")
            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)
            return redirect("dashboard")
    else:
        form = RegistrationForm()

    return render(request, "viewer/register.html", {"form": form})


def logout_view(request):
    logout(request)
    messages.info(request, "You have been logged out.")
    return redirect("login")


def _home_event_run_rows(snapshot, limit=6):
    rows = []
    for card in snapshot.get("event_cards", []):
        rows.append(
            {
                "label": str(card.get("name") or "event"),
                "value": int(card.get("run_count") or 0),
            }
        )

    rows = sorted(rows, key=lambda row: row["value"], reverse=True)[:limit]
    max_value = max((row["value"] for row in rows), default=0)
    for row in rows:
        row["pct"] = max(10, int((row["value"] / max_value) * 100)) if max_value else 0
    return rows


def _home_freshness_rows(snapshot, limit=7):
    daily_counts = Counter()
    for run in snapshot.get("recent_runs", []):
        day_label = str(run.get("updated_at") or "")[:10]
        if day_label:
            daily_counts[day_label] += 1

    rows = [{"label": label, "value": count} for label, count in sorted(daily_counts.items())][-limit:]
    max_value = max((row["value"] for row in rows), default=0)
    for row in rows:
        row["pct"] = max(10, int((row["value"] / max_value) * 100)) if max_value else 0
    return rows


def _format_compact_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0

    absolute = abs(number)
    if absolute >= 1_000_000_000:
        return f"{number / 1_000_000_000:.1f}B"
    if absolute >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if absolute >= 1_000:
        return f"{number / 1_000:.1f}K"
    return f"{int(round(number)):,}"


def _scale_rows_for_pct(rows, value_key="value", pct_key="pct", min_pct=8):
    max_value = max((int(row.get(value_key) or 0) for row in rows), default=0)
    for row in rows:
        value = int(row.get(value_key) or 0)
        row[pct_key] = max(min_pct, int((value / max_value) * 100)) if max_value else 0
    return rows


def _safe_ratio(numerator, denominator, digits=2):
    try:
        num = float(numerator)
        den = float(denominator)
    except (TypeError, ValueError):
        return 0.0
    if den <= 0:
        return 0.0
    return round(num / den, digits)


def _delta_label(current, previous):
    try:
        curr = float(current)
        prev = float(previous)
    except (TypeError, ValueError):
        return "0.0%"
    if prev <= 0:
        return "new" if curr > 0 else "0.0%"
    delta = ((curr - prev) / prev) * 100
    return f"{'+' if delta >= 0 else ''}{delta:.1f}%"


def _parse_tweet_hour(date_value):
    raw = str(date_value or "").strip()
    if not raw:
        return None

    normalized = raw.replace("Z", "+00:00")
    parsed = None
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = datetime.strptime(raw[:19], fmt)
                break
            except ValueError:
                continue

    if parsed is None:
        return None
    return parsed.hour


def _build_twitter_analytics_context(data_dir):
    snapshot = _dataset_snapshot("all", data_dir=data_dir)
    preview_rows = _collect_preview_tweets(
        snapshot.get("event_names", []),
        snapshot.get("selected_event", "all"),
        None,
        "",
        "",
        1500,
        0,
        data_dir,
    )

    total_tweets = int(snapshot.get("total_tweets") or 0)
    total_likes = int(snapshot.get("total_likes") or 0)
    total_retweets = int(snapshot.get("total_retweets") or 0)
    total_runs = int(snapshot.get("total_runs") or 0)
    non_empty_runs = int(snapshot.get("non_empty_runs") or 0)
    avg_likes = float(snapshot.get("avg_likes") or 0.0)
    avg_retweets = float(snapshot.get("avg_retweets") or 0.0)
    engagement_actions = total_likes + total_retweets
    run_quality_pct = round((non_empty_runs / total_runs) * 100, 1) if total_runs else 0.0
    unique_users = len({str(row.get("username") or "").strip().lower() for row in preview_rows if row.get("username")})

    top_user_name = ""
    if snapshot.get("top_users"):
        top_user_name = str(snapshot["top_users"][0][0] or "").strip()
    account_handle = f"@{top_user_name}" if top_user_name and top_user_name != "unknown" else "@workspace"

    overview_cards = [
        {
            "label": "Tweets Collected",
            "value_display": _format_compact_number(total_tweets),
            "delta": f"{total_runs} runs",
            "tone": "pos",
        },
        {
            "label": "Total Likes",
            "value_display": _format_compact_number(total_likes),
            "delta": f"avg {avg_likes:.2f} / tweet",
            "tone": "pos",
        },
        {
            "label": "Total Retweets",
            "value_display": _format_compact_number(total_retweets),
            "delta": f"avg {avg_retweets:.2f} / tweet",
            "tone": "neu",
        },
        {
            "label": "Engagement Actions",
            "value_display": _format_compact_number(engagement_actions),
            "delta": f"{_safe_ratio(engagement_actions, total_tweets, 2):.2f} / tweet",
            "tone": "neu",
        },
        {
            "label": "Non-empty Runs",
            "value_display": _format_compact_number(non_empty_runs),
            "delta": f"{run_quality_pct:.1f}% run quality",
            "tone": "pos",
        },
        {
            "label": "Active Users",
            "value_display": _format_compact_number(unique_users),
            "delta": f"top {account_handle}",
            "tone": "pos",
        },
    ]

    trend_rows = [
        {"label": str(item.get("label") or "")[5:] or "day", "value": int(item.get("value") or 0)}
        for item in snapshot.get("trend_points", [])[-10:]
    ]
    _scale_rows_for_pct(trend_rows, "value", "pct", min_pct=14)

    sentiment_rows = [dict(row) for row in snapshot.get("sentiment_rows", [])]
    if not sentiment_rows:
        sentiment_rows = [
            {"label": "Positive", "count": 0, "pct": 0.0, "css": "pos"},
            {"label": "Neutral", "count": 0, "pct": 0.0, "css": "neu"},
            {"label": "Negative", "count": 0, "pct": 0.0, "css": "neg"},
        ]
    sentiment_pie = dict(snapshot.get("sentiment_pie", {"pos": 0.0, "pos_neu": 100.0, "neu": 100.0, "neg": 0.0}))
    sentiment_total = sum(int(row.get("count") or 0) for row in sentiment_rows)
    if sentiment_total == 0:
        sentiment_pie = {"pos": 0.0, "pos_neu": 100.0, "neu": 100.0, "neg": 0.0}

    hashtag_counts = Counter()
    for row in preview_rows:
        text = str(row.get("text") or "")
        for tag in re.findall(r"#([A-Za-z0-9_]{2,48})", text):
            hashtag_counts[f"#{tag.lower()}"] += 1

    hashtag_rows = [{"tag": tag, "count": count} for tag, count in hashtag_counts.most_common(8)]

    follower_trend = []
    for index, row in enumerate(trend_rows[-6:]):
        base = int(row.get("value") or 0)
        prev_value = int(trend_rows[-6:][index - 1].get("value") or 0) if index > 0 else 0
        gained = max(base - prev_value, 0)
        lost = max(prev_value - base, 0)
        follower_trend.append(
            {
                "label": str(row.get("label") or f"D{index + 1}"),
                "gained": gained,
                "lost": lost,
            }
        )

    max_follow_gained = max((row["gained"] for row in follower_trend), default=0)
    max_follow_lost = max((row["lost"] for row in follower_trend), default=0)
    for row in follower_trend:
        row["gained_pct"] = max(10, int((row["gained"] / max_follow_gained) * 100)) if max_follow_gained > 0 else 0
        row["lost_pct"] = max(10, int((row["lost"] / max_follow_lost) * 100)) if max_follow_lost > 0 else 0

    hour_counts = Counter()
    for row in preview_rows:
        hour = _parse_tweet_hour(row.get("date"))
        if hour is not None:
            hour_counts[hour] += 1

    hour_rows = [
        {"label": "Morning (6-11)", "value": sum(hour_counts[h] for h in range(6, 12))},
        {"label": "Afternoon (12-17)", "value": sum(hour_counts[h] for h in range(12, 18))},
        {"label": "Evening (18-23)", "value": sum(hour_counts[h] for h in range(18, 24))},
        {"label": "Night (0-5)", "value": sum(hour_counts[h] for h in range(0, 6))},
    ]
    _scale_rows_for_pct(hour_rows, "value", "pct", min_pct=16)

    language_rows = [
        {"label": str(lang or "unknown"), "value": int(count or 0)}
        for lang, count in snapshot.get("top_languages", [])
    ]
    _scale_rows_for_pct(language_rows, "value", "pct", min_pct=14)

    user_rows = [
        {
            "label": f"@{username}" if str(username or "").strip() and str(username).strip().lower() != "unknown" else "unknown",
            "value": int(count or 0),
        }
        for username, count in snapshot.get("top_users", [])
    ]
    _scale_rows_for_pct(user_rows, "value", "pct", min_pct=14)

    lang_country_map = {
        "en": "United States",
        "es": "Spain",
        "hi": "India",
        "pt": "Brazil",
        "fr": "France",
        "de": "Germany",
        "tr": "Turkey",
        "ja": "Japan",
    }
    country_counts = Counter()
    for row in language_rows:
        lang = str(row.get("label") or "").lower()
        country_counts[lang_country_map.get(lang, str(row.get("label") or "").upper())] += int(row.get("value") or 0)

    country_rows = [{"label": label, "value": value} for label, value in country_counts.most_common(6)]
    _scale_rows_for_pct(country_rows, "value", "pct", min_pct=14)

    event_rows = []
    for card in snapshot.get("event_cards", []):
        tweet_value = int(card.get("tweet_count") or 0)
        run_value = int(card.get("run_count") or 0)
        event_rows.append(
            {
                "label": str(card.get("name") or "event"),
                "value": tweet_value if tweet_value > 0 else run_value,
            }
        )
    event_rows = sorted(event_rows, key=lambda row: row.get("value", 0), reverse=True)[:8]
    _scale_rows_for_pct(event_rows, "value", "pct", min_pct=14)

    keyword_counts = Counter()
    stop_words = {
        "the", "and", "for", "with", "that", "this", "from", "your", "have", "will", "just", "about",
        "http", "https", "www", "com", "are", "was", "were", "you", "our", "they", "their", "them",
        "not", "but", "can", "all", "one", "two", "out", "into", "has", "had", "been", "more",
    }
    for row in preview_rows:
        text = str(row.get("text") or "").lower()
        for token in re.findall(r"[a-z][a-z0-9_]{2,30}", text):
            if token in stop_words:
                continue
            if token.startswith("http"):
                continue
            keyword_counts[token] += 1

    interest_rows = [
        f"{word} ({count})"
        for word, count in keyword_counts.most_common(8)
    ]

    type_counts = Counter()
    type_engagement = Counter()
    for row in preview_rows:
        text = str(row.get("text") or "").lower()
        if "thread" in text or re.search(r"\b1/\d+\b", text):
            tweet_type = "Threads"
        elif any(token in text for token in ("video", "watch", "clip", "reel", "youtube", "youtu.be")):
            tweet_type = "Videos"
        elif any(token in text for token in ("image", "photo", "pic", "jpg", "png")):
            tweet_type = "Images"
        elif "poll" in text:
            tweet_type = "Polls"
        else:
            tweet_type = "Text"

        likes = _safe_int(row.get("likes"))
        retweets = _safe_int(row.get("retweets"))
        type_counts[tweet_type] += 1
        type_engagement[tweet_type] += likes + retweets

    tweet_type_rows = []
    for tweet_type, count in type_counts.most_common():
        avg_engagement = round(type_engagement[tweet_type] / count, 1) if count else 0.0
        tweet_type_rows.append(
            {
                "label": tweet_type,
                "count": count,
                "avg_engagement": avg_engagement,
                "value": count,
            }
        )

    _scale_rows_for_pct(tweet_type_rows, "value", "pct", min_pct=12)

    top_tweets = sorted(
        preview_rows,
        key=lambda row: _safe_int(row.get("likes")) + _safe_int(row.get("retweets")),
        reverse=True,
    )[:8]

    for row in top_tweets:
        row["date_short"] = str(row.get("date") or "")[:16].replace("T", " ")
        row["engagement"] = _safe_int(row.get("likes")) + _safe_int(row.get("retweets"))

    insights = []
    if total_tweets > 0:
        recent_total = sum(int(row.get("value") or 0) for row in trend_rows[-3:])
        prior_total = sum(int(row.get("value") or 0) for row in trend_rows[-6:-3])
        if prior_total == 0 and len(trend_rows) > 3:
            prior_total = sum(int(row.get("value") or 0) for row in trend_rows[:-3])

        top_sentiment_row = max(sentiment_rows, key=lambda row: int(row.get("count") or 0), default=None)
        top_hour_row = max(hour_rows, key=lambda row: int(row.get("value") or 0), default=None)

        insights.append(
            f"{_format_compact_number(total_tweets)} tweets across {total_runs} runs "
            f"({non_empty_runs} non-empty)."
        )
        if recent_total or prior_total:
            insights.append(f"Recent activity trend: {_delta_label(recent_total, prior_total)} vs prior window.")
        if top_sentiment_row:
            insights.append(f"Top sentiment: {top_sentiment_row.get('label')} ({top_sentiment_row.get('pct')}%).")
        if top_hour_row and int(top_hour_row.get("value") or 0) > 0:
            insights.append(f"Peak posting window: {top_hour_row.get('label')} ({top_hour_row.get('value')} tweets).")
        if hashtag_rows:
            insights.append(f"Most used hashtag: {hashtag_rows[0]['tag']} ({hashtag_rows[0]['count']} uses).")
    else:
        insights = [
            "No scraped tweets found in this workspace yet.",
            "Run a new query from Dashboard to populate this page.",
            "All analytics blocks will update automatically from your collected runs.",
        ]

    last_run_at = ""
    if snapshot.get("recent_runs"):
        last_run_at = str(snapshot["recent_runs"][0].get("updated_at") or "")

    report_rows = [
        {
            "key": "dataset_snapshot",
            "title": "Dataset Snapshot",
            "description": (
                f"{_format_compact_number(total_tweets)} tweets, "
                f"{_format_compact_number(engagement_actions)} engagement actions, "
                f"{len(event_rows)} active events."
            ),
            "schedule": f"Last run: {last_run_at or 'n/a'}",
            "format": "CSV",
        },
        {
            "key": "sentiment_breakdown",
            "title": "Sentiment Breakdown",
            "description": (
                f"Positive {sentiment_rows[0]['pct']}%, "
                f"Neutral {sentiment_rows[1]['pct']}%, "
                f"Negative {sentiment_rows[2]['pct']}%."
                if len(sentiment_rows) >= 3
                else "Sentiment scores unavailable."
            ),
            "schedule": "On-demand",
            "format": "CSV",
        },
        {
            "key": "top_voices_topics",
            "title": "Top Voices And Topics",
            "description": (
                f"{len(user_rows)} ranked users, {len(hashtag_rows)} hashtags, "
                f"{len(language_rows)} language groups."
            ),
            "schedule": "On-demand",
            "format": "CSV",
        },
    ]

    return {
        "workspace_dir": str(data_dir),
        "sample_mode": total_tweets == 0,
        "account_handle": account_handle,
        "active_users_display": _format_compact_number(unique_users),
        "total_tweets_display": _format_compact_number(total_tweets),
        "delta_tweets_window": _delta_label(
            sum(int(row.get("value") or 0) for row in trend_rows[-3:]),
            sum(int(row.get("value") or 0) for row in trend_rows[-6:-3]),
        ),
        "overview_cards": overview_cards,
        "trend_rows": trend_rows,
        "sentiment_total": sentiment_total,
        "sentiment_rows": sentiment_rows,
        "sentiment_pie": sentiment_pie,
        "hashtag_rows": hashtag_rows,
        "follower_trend": follower_trend,
        "hour_rows": hour_rows,
        "insights": insights,
        "language_rows": language_rows,
        "user_rows": user_rows,
        "country_rows": country_rows,
        "event_rows": event_rows,
        "interest_rows": interest_rows,
        "tweet_type_rows": tweet_type_rows,
        "top_tweets": top_tweets,
        "report_rows": report_rows,
    }


def home(request):
    snapshot = _dataset_snapshot("all", data_dir=EXAMPLE_DATA_DIR)
    capability_cards = [
        {
            "title": "Collect From X API",
            "body": "Run keyword, date, and event-based collection directly from the UI with secure token handling.",
        },
        {
            "title": "Understand Sentiment Instantly",
            "body": "VADER scoring classifies tweets into positive, neutral, and negative with interactive charts.",
        },
        {
            "title": "Explore With Analyst Controls",
            "body": "Filter by sentiment, text, and user, inspect run files, and export focused CSV slices.",
        },
        {
            "title": "Onboard Users Faster",
            "body": "Each new user gets a private workspace auto-seeded with example data to learn before scraping.",
        },
    ]
    outcomes = [
        "Track public perception around people, products, and campaigns.",
        "Compare engagement and sentiment trends across date ranges.",
        "Spot top languages, top voices, and high-signal tweet samples.",
        "Move from raw tweets to dashboard-ready insights in one place.",
    ]
    built_stack = [
        "Django-based product with authentication and role-ready architecture.",
        "Responsive black-and-red experience optimized for desktop and mobile.",
        "Interactive sentiment dashboard with hover insights and click-through tweet filtering.",
        "Persistent deployment model for production hosting on Render.",
    ]

    total_runs = int(snapshot.get("total_runs") or 0)
    non_empty_runs = int(snapshot.get("non_empty_runs") or 0)
    run_quality_pct = round((non_empty_runs / total_runs) * 100, 1) if total_runs else 0.0

    if snapshot.get("sentiment_total"):
        home_sentiment_pie = snapshot.get("sentiment_pie") or {"pos": 0.0, "pos_neu": 0.0, "neu": 0.0, "neg": 0.0}
    else:
        home_sentiment_pie = {"pos": 0.0, "pos_neu": 100.0, "neu": 100.0, "neg": 0.0}

    context = {
        **snapshot,
        "example_data_dir": str(EXAMPLE_DATA_DIR),
        "capability_cards": capability_cards,
        "outcomes": outcomes,
        "built_stack": built_stack,
        "run_quality_pct": run_quality_pct,
        "home_sentiment_pie": home_sentiment_pie,
        "event_run_rows": _home_event_run_rows(snapshot),
        "freshness_rows": _home_freshness_rows(snapshot),
    }
    return render(request, "viewer/home.html", context)


@login_required
def dashboard(request):
    initial_event = request.GET.get("event")
    form = CollectQueryForm(initial={"event": initial_event} if initial_event and initial_event != "all" else None)
    user_data_dir = _workspace_data_dir(request.user)
    return _dashboard_response(request, form, user_data_dir)


@login_required
def twitter_analytics(request):
    user_data_dir = _workspace_data_dir(request.user)
    context = _build_twitter_analytics_context(user_data_dir)
    return render(request, "viewer/twitter_analytics.html", context)


@login_required
def twitter_report_download(request, report_key):
    user_data_dir = _workspace_data_dir(request.user)
    snapshot = _dataset_snapshot("all", data_dir=user_data_dir)
    context = _build_twitter_analytics_context(user_data_dir)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    if report_key == "dataset_snapshot":
        rows = _collect_preview_tweets(
            snapshot.get("event_names", []),
            snapshot.get("selected_event", "all"),
            None,
            "",
            "",
            EXPORT_MAX_ROWS,
            0,
            user_data_dir,
        )
        header = [
            "event",
            "run_file",
            "tweet_id",
            "date",
            "username",
            "lang",
            "likes",
            "retweets",
            "sentiment",
            "compound",
            "text",
        ]
        body = [
            [
                row.get("event"),
                row.get("run_file"),
                row.get("id"),
                row.get("date"),
                row.get("username"),
                row.get("lang"),
                row.get("likes"),
                row.get("retweets"),
                row.get("sentiment"),
                row.get("compound"),
                row.get("text"),
            ]
            for row in rows
        ]
        return _csv_download_response(f"twitter_dataset_snapshot_{timestamp}.csv", header, body)

    if report_key == "sentiment_breakdown":
        sentiment_rows = context.get("sentiment_rows", [])
        header = ["sentiment", "count", "pct"]
        body = [[row.get("label"), row.get("count"), row.get("pct")] for row in sentiment_rows]
        return _csv_download_response(f"twitter_sentiment_breakdown_{timestamp}.csv", header, body)

    if report_key == "top_voices_topics":
        header = ["section", "label", "value"]
        body = []
        for row in context.get("user_rows", []):
            body.append(["top_users", row.get("label"), row.get("value")])
        for row in context.get("hashtag_rows", []):
            body.append(["hashtags", row.get("tag"), row.get("count")])
        for row in context.get("language_rows", []):
            body.append(["languages", row.get("label"), row.get("value")])
        return _csv_download_response(f"twitter_voices_topics_{timestamp}.csv", header, body)

    raise Http404("Unknown report type")


@login_required
def collect_query(request):
    if request.method != "POST":
        return redirect("dashboard")

    user_data_dir = _workspace_data_dir(request.user)
    form = CollectQueryForm(request.POST)
    if not form.is_valid():
        selected_event = request.POST.get("event_filter", "all")
        snapshot = _dataset_snapshot(selected_event, data_dir=user_data_dir)
        context = {
            **snapshot,
            "workspace_dir": str(user_data_dir),
            "shared_example_dir": str(EXAMPLE_DATA_DIR),
            "query_form": form,
        }
        return render(request, "viewer/dashboard.html", context, status=400)

    query = form.cleaned_data["query"].strip()
    since = form.cleaned_data["since"].isoformat()
    until = form.cleaned_data["until"].isoformat()
    event = form.cleaned_data["event"]
    token = (form.cleaned_data.get("bearer_token") or "").strip()

    try:
        run_filename, retry_meta = _run_collection_with_retry(query, since, until, event, token, user_data_dir)
    except RateLimitDeferredError as exc:
        reset_ts = exc.reset_ts if exc.reset_ts and exc.reset_ts > int(time.time()) else int(time.time() + (exc.retry_after or 60))
        reset_iso = _rate_limit_reset_iso(reset_ts)
        messages.error(
            request,
            "Collection paused by X API rate limit. "
            f"Auto-retry waited {exc.waited_seconds}s over {exc.attempts} attempt(s). "
            f"Next retry window: {reset_iso or reset_ts} UTC."
        )
        query_params = urlencode({"event": event, "rate_limit_reset": str(reset_ts)})
        return redirect(f"{reverse('dashboard')}?{query_params}")
    except Exception as exc:
        messages.error(request, f"Collection failed: {exc}")
        return redirect(f"{reverse('dashboard')}?event={event}")

    if retry_meta.get("attempts", 1) > 1:
        messages.success(
            request,
            f"Collection completed after {retry_meta['attempts'] - 1} retry/retries "
            f"(waited {retry_meta.get('waited_seconds', 0)}s). New run: {run_filename}"
        )
    else:
        messages.success(request, f"Collection completed. New run: {run_filename}")
    return redirect("run_detail", event=event, filename=run_filename)


@login_required
def run_detail(request, event, filename):
    user_data_dir = _workspace_data_dir(request.user)
    event_dir = _event_dir(event, user_data_dir)
    json_path = event_dir / "jsons" / filename

    if json_path.suffix.lower() != ".json" or not json_path.exists() or not json_path.is_file():
        raise Http404(f"Run file not found: {filename}")

    filter_state = _build_run_filter_state(request)
    limit = filter_state["limit"]
    text_filter = filter_state["text_filter"]
    sentiment_filter = filter_state["sentiment_filter"]

    export_csv = (request.GET.get("export") or "").strip().lower() == "csv"
    if export_csv:
        export_header = [
            "event",
            "run_file",
            "tweet_id",
            "date",
            "username",
            "lang",
            "likes",
            "retweets",
            "sentiment",
            "compound",
            "text",
        ]
        export_rows = []
        for item in _iter_tweets(json_path):
            user_obj = item.get("user") or {}
            text = item.get("renderedContent") or item.get("content") or ""
            compound = _sentiment_compound(text)
            sentiment_label = _sentiment_label(compound)

            if sentiment_filter and sentiment_label != sentiment_filter:
                continue
            if text_filter and text_filter not in text.lower():
                continue

            export_rows.append(
                [
                    event,
                    filename,
                    item.get("id"),
                    item.get("date"),
                    user_obj.get("username"),
                    item.get("lang"),
                    item.get("likeCount"),
                    item.get("retweetCount"),
                    sentiment_label,
                    round(compound, 3),
                    _normalize_text(text),
                ]
            )
            if len(export_rows) >= EXPORT_MAX_ROWS:
                break

        export_filename = f"run_{event}_{json_path.stem}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
        return _csv_download_response(export_filename, export_header, export_rows)

    run_summary = _analyze_run_file(json_path)
    rows = []
    total_rows = run_summary["tweet_count"]
    parse_errors = run_summary["parse_errors"]
    sentiment_counts = run_summary["sentiment_counts"]

    if sentiment_filter or text_filter:
        for item in _iter_tweets(json_path):
            user_obj = item.get("user") or {}
            text = item.get("renderedContent") or item.get("content") or ""
            compound = _sentiment_compound(text)
            sentiment_label = _sentiment_label(compound)

            if sentiment_filter and sentiment_label != sentiment_filter:
                continue
            if text_filter and text_filter not in text.lower():
                continue

            rows.append(
                {
                    "id": item.get("id"),
                    "date": item.get("date"),
                    "username": user_obj.get("username"),
                    "lang": item.get("lang"),
                    "likes": item.get("likeCount"),
                    "retweets": item.get("retweetCount"),
                    "sentiment": sentiment_label,
                    "compound": round(compound, 3),
                    "text": _normalize_text(text),
                }
            )
            if len(rows) >= limit:
                break
    else:
        for item in _iter_tweets(json_path):
            if len(rows) >= limit:
                break
            user_obj = item.get("user") or {}
            text = item.get("renderedContent") or item.get("content") or ""
            compound = _sentiment_compound(text)
            sentiment_label = _sentiment_label(compound)

            rows.append(
                {
                    "id": item.get("id"),
                    "date": item.get("date"),
                    "username": user_obj.get("username"),
                    "lang": item.get("lang"),
                    "likes": item.get("likeCount"),
                    "retweets": item.get("retweetCount"),
                    "sentiment": sentiment_label,
                    "compound": round(compound, 3),
                    "text": _normalize_text(text),
                }
            )

    sentiment_total = sum(sentiment_counts.values())
    positive_count = sentiment_counts.get("Positive", 0)
    neutral_count = sentiment_counts.get("Neutral", 0)
    negative_count = sentiment_counts.get("Negative", 0)
    if sentiment_total:
        positive_pct = round((positive_count / sentiment_total) * 100, 1)
        neutral_pct = round((neutral_count / sentiment_total) * 100, 1)
        negative_pct = round((negative_count / sentiment_total) * 100, 1)
    else:
        positive_pct = neutral_pct = negative_pct = 0.0

    sentiment_pie = {
        "pos": positive_pct,
        "pos_neu": min(round(positive_pct + neutral_pct, 1), 100.0),
        "neu": neutral_pct,
        "neg": negative_pct,
    }
    sentiment_rows = [
        {"label": "Positive", "count": positive_count, "pct": positive_pct, "css": "pos"},
        {"label": "Neutral", "count": neutral_count, "pct": neutral_pct, "css": "neu"},
        {"label": "Negative", "count": negative_count, "pct": negative_pct, "css": "neg"},
    ]

    csv_path = event_dir / "csv" / f"{json_path.stem}.csv"
    log_path = event_dir / "jsons" / f"{json_path.stem}.log"
    run_list = _run_entries(event_dir)

    context = {
        "event": event,
        "filename": filename,
        "json_path": str(json_path),
        "json_size": json_path.stat().st_size,
        "csv_path": str(csv_path) if csv_path.exists() else None,
        "csv_rows": _csv_row_count(csv_path),
        "total_rows": total_rows,
        "parse_errors": parse_errors,
        "preview_rows": rows,
        "limit": limit,
        "text_filter": text_filter,
        "sentiment_filter": sentiment_filter or "",
        "sentiment_total": sentiment_total,
        "sentiment_pie": sentiment_pie,
        "sentiment_rows": sentiment_rows,
        "log_tail": _read_log_tail(log_path),
        "runs": run_list,
    }
    return render(request, "viewer/run_detail.html", context)
