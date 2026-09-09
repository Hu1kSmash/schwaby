"""Keeping an unattended process running against a seven-day token.

Schwab's refresh token is valid for seven days from the *original*
authorization, and refreshing it does not extend that. So every long-running
program built on this library stops at some point in the next seven days, and
the only question is whether it stops at a moment you chose.

This library never updates the token's creation timestamp on refresh, which is
what makes `client.token_age()` mean what you want: seconds since the clock
started, not seconds since the last refresh.

Nothing here places orders. It is the supervision loop that sits beside
whatever does.
"""

import datetime
import time

import schwaby

API_KEY = 'XXXXXX'
APP_SECRET = 'XXXXXX'
TOKEN_PATH = './token.json'

# Schwab's documented term. It is not a promise -- the token may go earlier --
# so treat this as the outside edge rather than a deadline to run up to.
REFRESH_TOKEN_LIFETIME = datetime.timedelta(days=7).total_seconds()

# Re-authenticate with this much left. A day is enough to notice an alert,
# clear a calendar, and sit down at a browser without it being an emergency.
WARN_WITH_REMAINING = datetime.timedelta(days=1).total_seconds()

CHECK_EVERY = datetime.timedelta(hours=1).total_seconds()


def alert(message):
    """Replace with whatever actually reaches you: a page, an email, a webhook.

    Printing is the one thing that will not reach you at 03:00 on a Sunday,
    which is when the window tends to close.
    """
    print('[token] %s' % message)


def main():
    # client_from_token_file rather than easy_client on purpose. easy_client
    # would notice the token is old and open a browser -- which is right at a
    # desk and wrong in a container, where it blocks forever on a login nobody
    # can complete.
    client = schwaby.auth.client_from_token_file(
            TOKEN_PATH, api_key=API_KEY, app_secret=APP_SECRET)

    while True:
        age = client.token_age()
        remaining = REFRESH_TOKEN_LIFETIME - age

        if remaining <= 0:
            alert('token is past its seven-day term; re-authenticate now')
        elif remaining <= WARN_WITH_REMAINING:
            alert('token expires in %.1f hours; re-authenticate' %
                  (remaining / 3600))
        else:
            print('[token] %.1f days remaining' % (remaining / 86400))

        time.sleep(CHECK_EVERY)


if __name__ == '__main__':
    main()
