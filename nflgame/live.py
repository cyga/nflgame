"""
The live module provides a mechanism of periodically checking which games are
being actively played.

It requires the third party library pytz to be
installed, which makes sure game times are compared properly with respect
to time zones. pytz can be downloaded from PyPI:
http://pypi.python.org/pypi/pytz/

It works by periodically downloading data from NFL.com for games that started
before the current time. Once a game completes, the live module stops asking
NFL.com for data for that game.

If there are no games being actively played (i.e., it's been more than N hours
since the last game started), then the live module sleeps for longer periods
of time.

Thus, the live module can switch between two different modes: active and
inactive.

In the active mode, the live module downloads data from NFL.com in
short intervals. A transition to an inactive mode occurs when no more games
are being played.

In the inactive mode, the live module only checks if a game is playing (or
about to play) every 15 minutes. If a game is playing or about to play, the
live module switches to the active mode. Otherwise, it stays in the inactive
mode.

With this strategy, if the live module is working properly, you could
theoretically keep it running for the entire season.

(N.B. Half-time is ignored. Games are either being actively played or not.)

Alpha status
------------
This module is emphatically in alpha status. I believe things will work OK for
the regular season, but the postseason brings new challenges. Moreover, it
will probably affect the API at least a little bit.
"""
import datetime
import time
import urllib2
import xml.dom.minidom as xml

try:
    import pytz
except ImportError:
    pass

import nflgame.game
import nflgame.schedule

# [00:21] <rasher> burntsushi: Alright, the schedule changes on Wednesday 7:00
# UTC during the regular season

_MAX_GAME_TIME = 60 * 60 * 6
"""
The assumed maximum time allowed for a game to complete. This is used to
determine whether a particular game that isn't over is currently active.
"""

_WEEK_INTERVAL = 60 * 60 * 12
"""
How often to check what the current week is. By default, it is twice a day.
"""

_CUR_SCHEDULE_URL = "http://www.nfl.com/liveupdate/scorestrip/ss.xml"
"""
Pinged infrequently to discover the current week number, year and week type.
The actual schedule of games is taken from the schedule module.
"""

# http://www.nfl.com/ajax/scorestrip?season=2009&seasonType=POST&week=22
_POST_URL = "http://static.nfl.com/liveupdate/scorestrip/postseason/ss.xml"
"""
The URL for the XML schedule of the post season. This is only used
during the post season.

TODO: How do we know if it's the post season?
"""

_EASTERN_TZ = pytz.timezone('US/Eastern')
"""Used to convert game times in EST to UTC."""

_cur_week = None
"""The current week. It is updated infrequently automatically."""

_cur_year = None
"""The current year. It is updated infrequently automatically."""

_preseason = False
"""True when it's the preseason."""

_regular = False
"""True when it's the regular season."""

_completed = []
"""
A list of game eids that have been completed since the live module started
checking for updated game stats.
"""


def current_year_and_week():
    """
    Returns a tuple (year, week) where year is the current year of the season
    and week is the current week number of games being played.
    i.e., (2012, 3).

    N.B. This always downloads the schedule XML data.
    """
    dom = xml.parse(urllib2.urlopen(_CUR_SCHEDULE_URL))
    gms = dom.getElementsByTagName('gms')[0]
    year = int(gms.getAttribute('y'))
    week = int(gms.getAttribute('w'))
    return (year, week)


def current_games(year=None, week=None,
                  regular=True, postseason=False, preseason=False):
    """
    Returns a list of game.Games of games that are currently playing.
    This fetches all current information from NFL.com.

    If either year or week is none, then the current year and week are
    fetched from the schedule on NFL.com. If they are *both* provided, then
    the schedule from NFL.com won't need to be downloaded, and thus saving
    time.

    So for example::

        year, week = nflgame.live.current_year_and_week()
        while True:
            games = nflgame.live.current_games(year, week)
            # Do something with games
            time.sleep(60)

    Finally, if the optional parameter postseason/preseason is True, then the
    week parameter will refer to the postseason/preseason week rather than the
    regular season week.
    """
    if year is None or week is None:
        year, week = current_year_and_week()

    games = []
    now = _now()
    gen = _games_in_week(year, week, regular=regular, postseason=postseason,
                         preseason=preseason)
    for info in gen:
        gametime = _game_datetime(info)
        if gametime >= now:
            if (gametime - now).total_seconds() <= 60 * 60 * 36:
                games.append(info['eid'])
        elif (now - gametime).total_seconds() <= _MAX_GAME_TIME:
            games.append(info['eid'])
    return games


def run(callback, active_interval=15, inactive_interval=900, stop=None):
    """
    Starts checking for games that are currently playing.

    Every time there is an update, callback will be called with two lists:
    active and completed. The active list is a list of game.Game that are
    currently being played. The completed list is a list of game.Game that
    have just finished. A game will appear in the completed list only once,
    after which that game will not be in either the active or completed lists.
    No game can ever be in both lists at the same time.

    It is possible that a game in the active list is not yet playing because
    it hasn't started yet. It ends up in the active list because the "pregame"
    has started on NFL.com's GameCenter web site, and sometimes game data is
    partially filled. When this is the case, the 'playing' method on
    a nflgame.game.Game will return False.

    When in the active mode (see live module description), active_interval
    specifies the number of seconds to wait between checking for updated game
    data. Please do not make this number too low to avoid angering NFL.com.
    If you anger them too much, it is possible that they could ban your IP
    address.

    Note that NFL.com's GameCenter page is updated every 15 seconds, so
    setting the active_interval much smaller than that is wasteful.

    When in the inactive mode (see live module description), inactive_interval
    specifies the number of seconds to wait between checking whether any games
    have started or are about to start.

    With the default parameters, run will never stop. However, you may set
    stop to a Python datetime.datetime value. After time passes the stopping
    point, run will quit. (Technically, it's possible that it won't quit until
    at most inactive_interval seconds after the stopping point is reached.)
    The stop value is compared against datetime.datetime.now().
    """
    active = False
    last_week_check = _update_week_number()

    # Before we start with the main loop, we make a first pass at what we
    # believe to be the active games. Of those, we check to see if any of
    # them are actually already over, and add them to _completed.
    for info in _active_games(inactive_interval):
        game = nflgame.game.Game(info['eid'])

        # If we couldn't get a game, that probably means the JSON feed
        # isn't available yet. (i.e., we're early.)
        if game is None:
            continue

        # Otherwise, if the game is over, add it to our list of completed
        # games and move on.
        if game.game_over():
            _completed.append(info['eid'])

    while True:
        if stop is not None and datetime.datetime.now() > stop:
            return

        if time.time() - last_week_check > _WEEK_INTERVAL:
            last_week_check = _update_week_number()

        games = _active_games(inactive_interval)
        if active:
            active = _run_active(callback, games)
            if not active:
                continue
            time.sleep(active_interval)
        else:
            active = not _run_inactive(games)
            if active:
                continue
            time.sleep(inactive_interval)


def _run_active(callback, games):
    """
    The active mode traverses each of the active games and fetches info for
    each from NFL.com.

    Then each game (that has info available on NFL.com---that is, the game
    has started) is added to one of two lists: active and completed, which
    are passed as the first and second parameters to callback. A game is
    put in the active list if it's still being played, and into the completed
    list if it has finished. In the latter case, it is added to a global store
    of completed games and will never be passed to callback again.
    """
    # There are no active games, so just quit and return False. Which means
    # we'll transition to inactive mode.
    if len(games) == 0:
        return False

    active, completed = [], []
    for info in games:
        game = nflgame.game.Game(info['eid'])

        # If no JSON was retrieved, then we're probably just a little early.
        # So just ignore it for now---but we'll keep trying!
        if game is None:
            continue

        # If the game is over, added it to completed and _completed.
        if game.game_over():
            completed.append(game)
            _completed.append(info['eid'])
        else:
            active.append(game)

    callback(active, completed)
    return True


def _run_inactive(games):
    """
    The inactive mode simply checks if there are any active games. If there
    are, inactive mode needs to stop and transition to active mode---thus
    we return False. If there aren't any active games, then the inactive
    mode should continue, where we return True.

    That is, so long as there are no active games, we go back to sleep.
    """
    return len(games) == 0


def _active_games(inactive_interval):
    """
    Returns a list of all active games. In this case, an active game is a game
    that will start within inactive_interval seconds, or has started within
    _MAX_GAME_TIME seconds in the past.
    """
    gen = _games_in_week(_cur_year, _cur_week,
                         regular=_regular,
                         postseason=False,
                         preseason=_preseason)
    games = []
    for info in gen:
        if not _game_is_active(info, inactive_interval):
            continue
        games.append(info)
    return games


def _games_in_week(year, week,
                   regular=True, postseason=False, preseason=False):
    """
    A generator for the games matching the year/week/preseason parameters.
    """
    for (y, t, w, _, _), info in nflgame.schedule.games:
        if y != year:
            continue
        if w != week:
            continue
        if t == 'PRE' and not preseason:
            continue
        if t == 'REG' and not regular:
            continue
        yield info


def _game_is_active(gameinfo, inactive_interval):
    """
    Returns true if the game is active. A game is considered active if the
    game start time is in the past and not in the completed list (which is
    a private module level variable that is populated automatically) or if the
    game start time is within inactive_interval seconds from starting.
    """
    gametime = _game_datetime(gameinfo)
    now = _now()
    if gametime >= now:
        return (gametime - now).total_seconds() <= inactive_interval
    return gameinfo['eid'] not in _completed


def _game_datetime(gameinfo):
    hour, minute = gameinfo['time'].strip().split(':')
    d = datetime.datetime(gameinfo['year'], gameinfo['month'], gameinfo['day'],
                          (int(hour) + 12) % 24, int(minute))
    return _EASTERN_TZ.localize(d).astimezone(pytz.utc)


def _now():
    return datetime.datetime.now(pytz.utc)


def _update_week_number():
    global _cur_week, _cur_year, _preseason, _regular

    dom = xml.parse(urllib2.urlopen(_CUR_SCHEDULE_URL))
    gms = dom.getElementsByTagName('gms')[0]
    _cur_week = int(gms.getAttribute('w'))
    _cur_year = int(gms.getAttribute('y'))
    _preseason = gms.getAttribute('t').strip() == 'P'
    _regular = gms.getAttribute('t').strip() == 'R'
    return time.time()
