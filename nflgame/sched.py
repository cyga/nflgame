try:
    from collections import OrderedDict
except:
    from ordereddict import OrderedDict  # from PyPI
import datetime
import json
import os.path
import re

__pdoc__ = {}

_sched_json_file = os.path.join(os.path.dirname(__file__), 'schedule.json')


def _create_schedule(jsonf=None):
    """
    Returns an ordered dict of schedule data from the schedule.json
    file, where games are ordered by the date and time that they
    started. Keys in the dictionary are GSIS ids and values are
    dictionaries with the following keys: week, month, year, home,
    away, wday, gamekey, season_type, time.
    """
    day = 60 * 60 * 24
    if jsonf is None:
        jsonf = _sched_json_file
    try:
        jsons = open(jsonf).read()
    except IOError:
        return OrderedDict()

    attempt = 1
    json_changed = False
    while attempt == 1 or json_changed:
        json_changed = False
        try:
            data = json.loads(jsons)
        except Exception as e:
            # try to auto-fix common problem in JSON file
            # additional "}"
            # there are number of issues on github and users say about it
            re_fig = re.compile("\}\s*\}\s*$")
            if re_fig.search(jsons):
                jsons = re_fig.sub("}", jsons)
                json_changed = True

            if not json_changed:
                raise Exception("can't parse JSON from file: "+jsonf+"\nerror:\n"+str(e))
        finally:
            attempt += 1

    d = OrderedDict()
    for gsis_id, info in data.get('games', []):
        d[gsis_id] = info
    last_updated = datetime.datetime.utcfromtimestamp(data.get('time', 0))

    if (datetime.datetime.utcnow() - last_updated).total_seconds() >= day:
        # Only try to update if we can write to the schedule file.
        if os.access(jsonf, os.W_OK):
            import nflgame.live
            import nflgame.update_sched
            year, week = nflgame.live.current_year_and_week()
            phase = nflgame.live._cur_season_phase
            nflgame.update_sched.update_week(d, year, phase, week)
            nflgame.update_sched.write_schedule(jsonf, d)
            last_updated = datetime.datetime.now()
    return d, last_updated

games, last_updated = _create_schedule()

__pdoc__['nflgame.sched.games'] = """
An ordered dict of schedule data, where games are ordered by the date
and time that they started. Keys in the dictionary are GSIS ids and
values are dictionaries with the following keys: week, month, year,
home, away, wday, gamekey, season_type, time.
"""

__pdoc__['nflgame.sched.last_updated'] = """
A `datetime.datetime` object representing the last time the schedule
was updated.
"""
