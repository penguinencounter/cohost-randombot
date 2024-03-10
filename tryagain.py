# Rate limiting.
from datetime import datetime, timedelta
import re

_month = {
    'Jan': '01',
    'Feb': '02',
    'Mar': '03',
    'Apr': '04',
    'May': '05',
    'Jun': '06',
    'Jul': '07',
    'Aug': '08',
    'Sep': '09',
    'Oct': '10',
    'Nov': '11',
    'Dec': '12'
}
minimum = 0
backoff_base = 2


def backoff(failures: int) -> timedelta:
    """
    Exponential backoff impl.
    """
    return timedelta(seconds=backoff_base ** failures + minimum)


def parse_retry_after(retry_str: str, failures: int) -> datetime:
    # it's a Number of Seconds, or an HTTP date.
    all_numbers = r'^\d+$'
    match = re.match(all_numbers, retry_str)
    if not match:
        # try to interpret it as a date
        datematch = re.match(
            r'^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), (\d\d) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) '
            r'(\d{4}) (\d\d):(\d\d):(\d\d) GMT$', retry_str
        )
        if datematch:
            month_of_year = _month[datematch.group(2)]
            rebuild = ' '.join([datematch.groups()[0], *datematch.groups()[2:], month_of_year])
            # DD YYYY HH MM SS Mo
            parsed = datetime.strptime(rebuild, '%d %Y %H %M %S %m')
            if parsed < datetime.now():
                return datetime.now() + backoff(failures)
            else:
                return parsed + timedelta(seconds=1)  # just in case
    else:
        duration = timedelta(seconds=int(match.group(0)))
        return datetime.now() + duration


if __name__ == '__main__':
    parse_retry_after('Mon, 01 Jan 2023 01:02:03 GMT')
