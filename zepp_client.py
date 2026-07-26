"""Zepp API client.

There is no official personal-data API. This talks to the app's own endpoints
with an app_token lifted from a browser session cookie -- your own account, your
own data. The token is obtained by hand (see section 1 of the integration
brief); nothing here logs in, and nothing here prints the token.

Three failure modes are encoded deliberately, because each one masquerades as
something else:

  401                     -> TokenExpired, not a bug in the request
  REDIRECTION + region    -> WrongRegion, not a bad token
  HTTP 200, ~30-byte body -> EmptyDetail, not "no streams for this workout"

The third cost an afternoon. workout_detail() takes the whole history record
rather than a trackid and a source, so passing the generic source is not
expressible.
"""
import time
import requests

MIN_INTERVAL = 0.6      # community reports of 429s; pace every call
TIMEOUT = 60            # a run detail is ~170 KB, a month of band data more
GENERIC_SOURCE = "run.mifit.huami.com"
SUSPICIOUS_BODY = 200   # a real detail payload is orders of magnitude larger


class ZeppError(RuntimeError):
    pass


class TokenExpired(ZeppError):
    """The app_token is no longer valid. Repeat the browser cookie grab."""


class WrongRegion(ZeppError):
    """Account lives on a different host. Read the region out of the response."""


class EmptyDetail(ZeppError):
    """200 with no payload -- almost always the wrong source value."""


class Zepp:
    def __init__(self, token=None, uid=None, host=None):
        if token is None or uid is None or host is None:
            try:
                import secrets_cm as creds
            except ImportError:
                creds = None
            token = token or getattr(creds, "ZEPP_TOKEN", None)
            uid = uid or getattr(creds, "ZEPP_UID", None)
            host = host or getattr(creds, "ZEPP_HOST", None)
        missing = [n for n, v in (("ZEPP_TOKEN", token), ("ZEPP_UID", uid),
                                  ("ZEPP_HOST", host)) if not v]
        if missing:
            raise ZeppError(
                "missing " + ", ".join(missing) + " in secrets_cm.py. Log in at "
                "https://watchface.zepp.com/, read the hm-user-login-info cookie, "
                "URL-decode it, and copy token_info.app_token and "
                "token_info.user_id. Do not click log out -- that voids the token.")
        self.token = token
        self.uid = str(uid)
        self.host = host
        self.calls = 0
        self._last = 0.0

    def __repr__(self):
        return "<Zepp uid=%s host=%s calls=%s>" % (self.uid, self.host, self.calls)

    def _headers(self):
        return {"apptoken": self.token,
                "appPlatform": "web",
                "appname": "com.xiaomi.hm.health"}

    def _pace(self):
        gap = time.time() - self._last
        if gap < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - gap)
        self._last = time.time()

    def _get(self, path, params, expect_body=0):
        self._pace()
        url = "https://" + self.host + path
        r = requests.get(url, params=params, headers=self._headers(),
                         timeout=TIMEOUT)
        self.calls += 1

        if r.status_code == 401:
            raise TokenExpired("401 from " + path + " -- token no longer valid")
        if r.status_code == 429:
            raise ZeppError("429 rate limited on " + path
                            + "; raise MIN_INTERVAL and resume")
        if r.status_code != 200:
            raise ZeppError("HTTP " + str(r.status_code) + " from " + path
                            + ": " + r.text[:200])

        body = r.content or b""
        if b"REDIRECTION" in body:
            region = ""
            try:
                region = str(r.json().get("region") or "")
            except ValueError:
                pass
            raise WrongRegion(
                "account is not on " + self.host
                + (" -- region says " + region if region else "")
                + ". Set ZEPP_HOST accordingly (api-mifit-us3.zepp.com, "
                  "api-mifit-de2.huami.com, ...). The token is fine.")
        if expect_body and len(body) < expect_body:
            raise EmptyDetail(
                "200 from " + path + " but only " + str(len(body))
                + " bytes. This is what the wrong `source` looks like -- it is a "
                  "success code with no data, not an absence of streams.")
        try:
            return r.json()
        except ValueError:
            raise ZeppError("non-JSON from " + path + ": " + r.text[:200])

    # -- endpoints -----------------------------------------------------------

    def workout_history(self):
        """Every workout summary. Follows pagination rather than assuming one page.

        This account returns all 97 in a single call with next == -1, but the
        brief is explicit that this should not be assumed to hold.
        """
        out = []
        seen_cursors = set()
        cursor = None
        while True:
            params = {"source": GENERIC_SOURCE}
            if cursor is not None:
                params["trackid"] = cursor
            d = self._get("/v1/sport/run/history.json", params)
            data = d.get("data") or {}
            batch = data.get("summary") or []
            out.extend(batch)
            nxt = data.get("next")
            if nxt is None or str(nxt) == "-1" or not batch:
                break
            if nxt in seen_cursors:      # server repeating itself; stop rather
                break                    # than loop forever
            seen_cursors.add(nxt)
            cursor = nxt
        return out

    def workout_detail(self, rec):
        """Streams for one workout. Takes the history record, not loose ids.

        `source` must be the value from this workout's own record. Passing the
        generic one returns 200 with an empty body, which reads like "no data
        available" and is not.
        """
        trackid = rec.get("trackid")
        source = rec.get("source")
        if not trackid:
            raise ZeppError("history record has no trackid: " + repr(rec)[:120])
        if not source:
            raise ZeppError("history record for trackid " + str(trackid)
                            + " has no source; cannot fetch detail safely")
        d = self._get("/v1/sport/run/detail.json",
                      {"trackid": trackid, "source": source},
                      expect_body=SUSPICIOUS_BODY)
        return (d.get("data") or {})

    def band_data(self, from_date, to_date):
        """Per-day records between two YYYY-MM-DD dates, inclusive.

        query_type must be `detail`. summary, hr and raw all omit data_hr, which
        is the minute-level heart rate and the whole reason for this call.
        """
        d = self._get("/v1/data/band_data.json",
                      {"query_type": "detail",
                       "device_type": "android_phone",
                       "userid": self.uid,
                       "from_date": from_date,
                       "to_date": to_date})
        return d.get("data") or []

    def check(self):
        """Cheapest call that proves the token and host are right."""
        recs = self.workout_history()
        types = {}
        for r in recs:
            types[r.get("type")] = types.get(r.get("type"), 0) + 1
        return {"workouts": len(recs), "by_type": types,
                "with_source": sum(1 for r in recs if r.get("source")),
                "calls": self.calls}


if __name__ == "__main__":
    try:
        z = Zepp()
    except ZeppError as e:
        print("Not configured:")
        print(" ", e)
        raise SystemExit(1)
    print(z)
    try:
        info = z.check()
    except TokenExpired as e:
        print("Token expired:", e)
        raise SystemExit(2)
    except WrongRegion as e:
        print("Wrong region:", e)
        raise SystemExit(3)
    print("Workouts visible:", info["workouts"])
    print("With a usable source field:", info["with_source"])
    print("By type:", dict(sorted((k, v) for k, v in info["by_type"].items()
                                  if k is not None)))
