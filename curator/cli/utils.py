import click
import sys
import re
import time
import logging
import json
from .utils import *

import elasticsearch
from ..api import *

logger = logging.getLogger(__name__)

# Elasticsearch versions supported
version_max  = (2, 0, 0)
version_min = (1, 0, 0)

REGEX_MAP = {
    'timestring': r'^.*{0}.*$',
    'newer_than': r'(?P<date>{0})',
    'older_than': r'(?P<date>{0})',
    'prefix': r'^{0}.*$',
    'suffix': r'^.*{0}$',
}

class LogstashFormatter(logging.Formatter):
    # The LogRecord attributes we want to carry over to the Logstash message,
    # mapped to the corresponding output key.
    WANTED_ATTRS = {'levelname': 'loglevel',
                    'funcName': 'function',
                    'lineno': 'linenum',
                    'message': 'message',
                    'name': 'name'}

    def converter(self, timevalue):
        return time.gmtime(timevalue)

    def format(self, record):
        timestamp = '%s.%03dZ' % (
            self.formatTime(record, datefmt='%Y-%m-%dT%H:%M:%S'), record.msecs)
        result = {'message': record.getMessage(),
                  '@timestamp': timestamp}
        for attribute in set(self.WANTED_ATTRS).intersection(record.__dict__):
            result[self.WANTED_ATTRS[attribute]] = getattr(record, attribute)
        return json.dumps(result, sort_keys=True)

class Whitelist(logging.Filter):
    def __init__(self, *whitelist):
        self.whitelist = [logging.Filter(name) for name in whitelist]

    def filter(self, record):
        return any(f.filter(record) for f in self.whitelist)

def check_version(client):
    """
    Verify version is within acceptable range.  Exit with error if it is not.

    :arg client: The Elasticsearch client connection
    """
    version_number = get_version(client)
    logger.debug('Detected Elasticsearch version {0}'.format(".".join(map(str,version_number))))
    if version_number >= version_max or version_number < version_min:
        click.echo(click.style('Expected Elasticsearch version range > {0} < {1}'.format(".".join(map(str,version_min)),".".join(map(str,version_max))), fg='red'))
        click.echo(click.style('ERROR: Incompatible with version {0} of Elasticsearch.  Exiting.'.format(".".join(map(str,version_number))), fg='red', bold=True))
        sys.exit(1)

def check_master(client, master_only=False):
    """
    Check if master node.  If not, exit with error code
    """
    if master_only and not is_master_node(client):
        logger.info('Master-only flag detected. Connected to non-master node. Aborting.')
        sys.exit(9)

def get_client(**kwargs):
    """Return an Elasticsearch client using the provided parameters

    """
    kwargs['master_only'] = False if not 'master_only' in kwargs else kwargs['master_only']
    logger.debug("kwargs = {0}".format(kwargs))
    master_only = kwargs.pop('master_only')
    try:
        client = elasticsearch.Elasticsearch(**kwargs)
        # Verify the version is acceptable.
        check_version(client)
        # Verify "master_only" status, if applicable
        check_master(client, master_only=master_only)
        return client
    except Exception:
        click.echo(click.style('ERROR: Connection failure.', fg='red', bold=True))
        sys.exit(1)

def filter_callback(ctx, param, value):
    """
    Append a dict to ctx.obj['filters'] based on the arguments
    """
    # Stop here if None or empty value
    if not value:
        return value
    else:
        argdict = {}

    if param.name in ['older_than', 'newer_than']:
        if not ctx.params['time_unit'] :
            click.echo(click.style("Parameters --older-than and --newer-than require the --time-unit parameter", fg='red', bold=True))
            sys.exit(1)
        if not ctx.params['timestring']:
            click.echo(click.style("Parameters --older-than and --newer-than require the --timestring parameter", fg='red', bold=True))
            sys.exit(1)
        argdict = {  "groupname":'date', "time_unit":ctx.params["time_unit"],
                    "timestring": ctx.params['timestring'], "value": value,
                    "method": param.name }
        date_regex = get_date_regex(ctx.params['timestring'])
        regex = REGEX_MAP[param.name].format(date_regex)
    elif param.name == 'regex':
        regex = r'{0}'.format(value)
    elif param.name in ['prefix', 'suffix']:
        regex = REGEX_MAP[param.name].format(value)

    if param.name == 'exclude':
        for e in value:
            argdict = {}
            argdict['pattern'] = '{0}'.format(e)
            argdict['exclude'] = True
            ctx.obj['filters'].append(argdict)
            logger.debug("Added filter: {0}".format(argdict))
    else:
        logger.debug("REGEX = {0}".format(regex))
        argdict['pattern'] = regex
        ctx.obj['filters'].append(argdict)
        logger.debug("Added filter: {0}".format(argdict))
    logger.debug("New list of filters: {0}".format(ctx.obj['filters']))
    return value

def in_list(values, source_list):
    """
    Return a list of values found inside source_list.

    While a list comprehension is faster, it doesn't log failures.

    :arg values: A list of items to compare to the ``source_list``
    :arg source_list: A list of items
    """
    retval = []
    for v in values:
        if v in source_list:
            logger.info('Adding {0} from command-line argument'.format(v))
            retval.append(v)
        else:
            logger.warn('{0} not found!'.format(v))
    return retval

def do_command(client, command, indices, params=None):
    """
    Do the command.
    """
    if command == "alias":
        alias(client, indices, alias=params['name'], remove=params['remove'])
    if command == "allocation":
        allocation(client, indices, rule=params['rule'])
    if command == "bloom":
        bloom(client, indices, delay=params['delay'])
    if command == "close":
        close(client, indices)
    if command == "delete":
        click.echo("params = {0}, indices = {1}".format(params, indices))
        delete(
            client, indices, disk_space=params['disk_space'],
            reverse=params['reverse']
        )
    if command == "open":
        opener(client, indices)
    if command == "optimize":
        optimize(
            client, indices, max_num_segments=params['max_num_segments'],
            delay=params['delay'], request_timeout=params['request_timeout']
        )
    if command == "replicas":
        replicas(client, indices, replicas=params['count'])
    if command == "snapshot":
        create_snapshot(
            client, indices=indices, name=params['name'],
            prefix=params['prefix'], repository=params['repository'],
            ignore_unavailable=params['ignore_unavailable'],
            include_global_state=params['include_global_state'],
            partial=params['partial'],
            wait_for_completion=params['wait_for_completion'],
            request_timeout=params['request_timeout'],
        )
