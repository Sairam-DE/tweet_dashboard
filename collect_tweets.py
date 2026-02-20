import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"


class snscrape:
    def __init__(self, query, since, until, till, event):
        self.query = query
        self.since = since
        self.until = until
        self.event = event
        self.till = till

    @staticmethod
    def _parse_date(date_value):
        try:
            return datetime.strptime(date_value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise RuntimeError(
                f"Invalid date '{date_value}'. Use format YYYY-MM-DD."
            ) from exc

    @staticmethod
    def _iso_z(dt_value):
        return dt_value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _to_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return value

    @staticmethod
    def _get_token():
        for env_name in ("X_BEARER_TOKEN", "BEARER_TOKEN", "TWITTER_BEARER_TOKEN"):
            token = os.getenv(env_name)
            if token:
                return token.strip(), env_name
        return None, None

    @staticmethod
    def _pick_mode(start_time_utc):
        forced_mode = os.getenv("X_SEARCH_ENDPOINT", "").strip().lower()
        if forced_mode in {"recent", "all"}:
            return forced_mode

        seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
        return "all" if start_time_utc < seven_days_ago else "recent"

    @staticmethod
    def _extract_error(response):
        try:
            body = response.json()
        except ValueError:
            return response.text.strip()

        errors = body.get("errors")
        if isinstance(errors, list) and errors:
            parts = []
            for err in errors:
                if not isinstance(err, dict):
                    parts.append(str(err))
                    continue
                message = err.get("detail") or err.get("message") or err.get("title")
                if message:
                    parts.append(message)
            if parts:
                return " | ".join(parts)
        return json.dumps(body, ensure_ascii=False)

    def _build_legacy_tweet(self, tweet, users_by_id):
        tweet_id = self._to_int(tweet.get("id"))
        author_id = tweet.get("author_id")
        user = users_by_id.get(author_id, {})

        username = user.get("username") or "unknown"
        display_name = user.get("name") or username
        user_id = self._to_int(user.get("id", author_id))

        user_metrics = user.get("public_metrics") or {}
        tweet_metrics = tweet.get("public_metrics") or {}
        entities = tweet.get("entities") or {}

        source_label = tweet.get("source") or "X"
        source_html = f'<a href="https://x.com" rel="nofollow">{source_label}</a>'

        mentions = []
        for mention in entities.get("mentions") or []:
            mention_name = mention.get("username")
            if mention_name:
                mentions.append(
                    {
                        "_type": "snscrape.modules.twitter.User",
                        "username": mention_name,
                        "id": self._to_int(mention.get("id")),
                        "displayname": mention_name,
                        "description": None,
                        "verified": None,
                        "created": None,
                        "followersCount": None,
                        "friendsCount": None,
                        "statusesCount": None,
                        "favouritesCount": None,
                        "location": None,
                        "protected": None,
                        "linkUrl": None,
                        "linkTcourl": None,
                        "profileImageUrl": None,
                        "profileBannerUrl": None,
                        "url": f"https://x.com/{mention_name}",
                    }
                )

        hashtags = [h.get("tag") for h in (entities.get("hashtags") or []) if h.get("tag")]

        links = []
        for link in entities.get("urls") or []:
            expanded = link.get("expanded_url") or link.get("url")
            if expanded:
                links.append(expanded)

        user_obj = {
            "_type": "snscrape.modules.twitter.User",
            "username": username,
            "id": user_id,
            "displayname": display_name,
            "description": user.get("description", ""),
            "rawDescription": user.get("description", ""),
            "descriptionUrls": None,
            "verified": user.get("verified"),
            "created": user.get("created_at"),
            "followersCount": user_metrics.get("followers_count"),
            "friendsCount": user_metrics.get("following_count"),
            "statusesCount": user_metrics.get("tweet_count"),
            "favouritesCount": 0,
            "listedCount": user_metrics.get("listed_count"),
            "mediaCount": None,
            "location": user.get("location", ""),
            "protected": user.get("protected"),
            "linkUrl": user.get("url"),
            "linkTcourl": None,
            "profileImageUrl": user.get("profile_image_url"),
            "profileBannerUrl": user.get("profile_banner_url"),
            "url": f"https://x.com/{username}",
        }

        text = tweet.get("text") or ""
        conversation_id = self._to_int(tweet.get("conversation_id", tweet_id))

        return {
            "_type": "snscrape.modules.twitter.Tweet",
            "url": f"https://x.com/{username}/status/{tweet_id}",
            "date": tweet.get("created_at"),
            "content": text,
            "renderedContent": text,
            "id": tweet_id,
            "user": user_obj,
            "replyCount": tweet_metrics.get("reply_count", 0),
            "retweetCount": tweet_metrics.get("retweet_count", 0),
            "likeCount": tweet_metrics.get("like_count", 0),
            "quoteCount": tweet_metrics.get("quote_count", 0),
            "conversationId": conversation_id,
            "lang": tweet.get("lang"),
            "source": source_html,
            "sourceUrl": "https://x.com",
            "sourceLabel": source_label,
            "outlinks": links if links else None,
            "tcooutlinks": None,
            "media": None,
            "retweetedTweet": None,
            "quotedTweet": None,
            "inReplyToTweetId": None,
            "inReplyToUser": None,
            "mentionedUsers": mentions if mentions else None,
            "coordinates": None,
            "place": None,
            "hashtags": hashtags if hashtags else None,
            "cashtags": None,
        }

    def collect_tweets(self):
        since_date = self._parse_date(self.since)
        until_date = self._parse_date(self.until)

        if until_date < since_date:
            raise RuntimeError("'until' must be greater than or equal to 'since'.")

        start_time = datetime.combine(since_date, datetime.min.time(), tzinfo=timezone.utc)
        end_time = datetime.combine(until_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

        token, token_env = self._get_token()
        if not token:
            raise RuntimeError(
                "Missing X API Bearer token. Set X_BEARER_TOKEN (or BEARER_TOKEN / TWITTER_BEARER_TOKEN)."
            )

        mode = self._pick_mode(start_time)
        endpoint = f"https://api.x.com/2/tweets/search/{mode}"
        max_results = 500 if mode == "all" else 100

        params = {
            "query": self.query,
            "start_time": self._iso_z(start_time),
            "end_time": self._iso_z(end_time),
            "max_results": str(max_results),
            "tweet.fields": "id,text,created_at,lang,source,author_id,public_metrics,conversation_id,entities",
            "expansions": "author_id",
            "user.fields": "id,name,username,created_at,description,verified,location,public_metrics,profile_image_url,protected,url",
        }

        output_path = DATA_DIR / self.event / "jsons" / f"{self.since}_{self.until}_{self.till}.json"
        log_path = DATA_DIR / self.event / "jsons" / f"{self.since}_{self.until}_{self.till}.log"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "v1_demo_x_api_collector",
        }

        total = 0
        page = 0
        next_token = None

        print("Collecting ...")
        with requests.Session() as session, output_path.open("w", encoding="utf-8") as out_fp, log_path.open(
            "w", encoding="utf-8"
        ) as log_fp:
            log_fp.write(f"endpoint={endpoint}\n")
            log_fp.write(f"query={self.query}\n")
            log_fp.write(f"start_time={params['start_time']}\n")
            log_fp.write(f"end_time={params['end_time']}\n")
            log_fp.write(f"token_source={token_env}\n")

            while True:
                page += 1
                if next_token:
                    params["next_token"] = next_token
                else:
                    params.pop("next_token", None)

                response = session.get(endpoint, params=params, headers=headers, timeout=60)

                if response.status_code >= 400:
                    detail = self._extract_error(response)
                    log_fp.write(f"page={page} status={response.status_code} error={detail}\n")

                    if mode == "all" and response.status_code in (401, 403):
                        raise RuntimeError(
                            "Full-archive search (/all) is not enabled for this token. "
                            "Use a token with full-archive access or query the recent 7-day window."
                        )
                    if response.status_code == 402:
                        raise RuntimeError(
                            "X API credits are depleted for this account. "
                            "Add credits in the X Developer portal billing page, then retry."
                        )
                    if response.status_code == 429:
                        reset_at = response.headers.get("x-rate-limit-reset")
                        if total > 0:
                            log_fp.write(
                                f"rate_limited=true partial_results={total} reset_at={reset_at}\n"
                            )
                            print(f"Rate limit reached after {total} tweets; keeping partial results.")
                            break
                        reset_info = reset_at
                        if reset_at and reset_at.isdigit():
                            reset_dt = datetime.fromtimestamp(int(reset_at), tz=timezone.utc)
                            reset_info = f"{reset_at} ({reset_dt.isoformat()})"
                        raise RuntimeError(
                            f"X API rate limit reached. Retry after reset: {reset_info}."
                        )
                    raise RuntimeError(f"X API request failed ({response.status_code}): {detail}")

                payload = response.json()
                data = payload.get("data") or []
                users = payload.get("includes", {}).get("users", []) or []
                users_by_id = {u.get("id"): u for u in users if u.get("id")}

                for tweet in data:
                    legacy = self._build_legacy_tweet(tweet, users_by_id)
                    out_fp.write(json.dumps(legacy, ensure_ascii=False) + "\n")
                    total += 1

                meta = payload.get("meta") or {}
                next_token = meta.get("next_token")
                result_count = meta.get("result_count", len(data))
                log_fp.write(
                    f"page={page} status=200 result_count={result_count} next_token={'yes' if next_token else 'no'}\n"
                )

                if not next_token:
                    break

        print(f"Collection done: {total} tweets")
        return f"{self.event}_{self.since}_{self.until}_{self.till}"
