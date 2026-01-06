#!/usr/bin/env python3
"""Python port of dx_get_cpu.pl - Nagios check for CPU utilization

This script provides parity with the original Perl `dx_get_cpu.pl` and uses
existing Python analytics helpers (Engine, Analytics, toolkit_helpers).
"""
import argparse
import sys
import os

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, 'lib', 'py'))

# Prefer mock engine when explicitly requested or if real engine import fails
try:
    from engine import Engine as _RealEngine
except Exception:
    _RealEngine = None
try:
    import mock_engine as _mock
except Exception:
    _mock = None
Engine = _mock.MockEngine if (os.environ.get('DXTOOLKIT_USE_MOCK') == '1' and _mock) else (_RealEngine or (_mock.MockEngine if _mock else None))
from analytics import Analytics
from formater import Formater
import toolkit_helpers

ALLOWED_RES = {
    '1': 'S', 'S': 'S',
    '60': 'M', 'M': 'M',
    '3600': 'H', 'H': 'H'
}


def metric_desc(analytic, metric):
    # Minimal mapping mirroring Perl metric_desc
    names = {
        'utilization': f"{analytic.getName()} utilization",
        'throughput_t': f"{analytic.getName()} throughput MB/s",
        'throughput_r': f"{analytic.getName()} throughput MB/s",
        'throughput_w': f"{analytic.getName()} throughput MB/s",
        'latency_r': f"{analytic.getName()} latency milliseconds",
        'latency_w': f"{analytic.getName()} latency milliseconds",
        'latency_t': f"{analytic.getName()} latency milliseconds",
    }
    return names.get(metric, metric)


def get_avg_from_analytic(analytic, stat, client='none'):
    """Emulate Perl Analytic_obj::get_avg behavior using analytic.aggreg"""
    if not hasattr(analytic, 'aggreg') or not analytic.aggreg:
        return -1
    # take first timestamp key (Perl takes the first key)
    timestamp = next(iter(analytic.aggreg.keys()), None)
    if not timestamp:
        return -1
    if client not in analytic.aggreg.get(timestamp, {}):
        return -1
    values = analytic.aggreg[timestamp][client].get(stat)
    if not values:
        return -1
    try:
        avg = sum(values) / len(values)
        return float(f"{avg:.2f}")
    except Exception:
        return -1


def nagios_check(engine_name, analytic_list, name, metric, arguments, resolution, raw, crit, warn):
    analytic = analytic_list.getAnalyticByName(name)
    if not analytic:
        print(f"Can't find {name} analytic")
        sys.exit(3)

    ret = analytic.getData(arguments, resolution)
    # Mirror Perl: on getData errors we just continue to try processing (Perl does not check ret)
    analytic.processData(2)
    analytic.doAggregation()

    if raw:
        # print raw CSV output
        analytic.processData(10)
        form = getattr(analytic, '_output', Formater())
        toolkit_helpers.print_output(form, 'csv', False)
        return 0

    avg = get_avg_from_analytic(analytic, metric)

    if avg == -1:
        print(f"Unknown: No data for {engine_name} {metric_desc(analytic, metric)}")
        sys.exit(3)
    elif avg >= crit:
        print(f"CRITICAL: {engine_name} {metric_desc(analytic, metric)} {avg:.2f}")
        sys.exit(2)
    elif avg >= warn:
        print(f"WARNING: {engine_name} {metric_desc(analytic, metric)} {avg:.2f}")
        sys.exit(1)
    else:
        print(f"OK: {engine_name} {metric_desc(analytic, metric)} {avg:.2f}")
        sys.exit(0)


def parse_args(argv):
    p = argparse.ArgumentParser(description="Get cpu utilization and present nagios-like status")
    p.add_argument('-d', '--engine', dest='dx_host')
    p.add_argument('-all', action='store_true')
    # Keep configfile as long option only to avoid conflict with -c (critical)
    p.add_argument('-configfile', dest='config_file')
    p.add_argument('-i', '--interval', dest='interval', default='1')
    p.add_argument('-st', dest='st')
    p.add_argument('-et', dest='et')
    p.add_argument('-w', dest='warn', type=int, default=75)
    p.add_argument('-c', dest='crit', type=int, default=95)
    p.add_argument('-raw', action='store_true')
    p.add_argument('-debug', dest='debug', type=int, nargs='?', const=1)
    p.add_argument('-dever', dest='dever')
    p.add_argument('-version', action='store_true')
    p.add_argument('-nohead', action='store_true')
    return p.parse_args(argv)


def main(argv):
    args = parse_args(argv)

    if args.version:
        print(toolkit_helpers.version)
        return 0

    # Verify resolution
    if args.interval not in ALLOWED_RES:
        print('Wrong interval')
        return 3

    eng = Engine(args.dever, args.debug)
    try:
        eng.load_config(args.config_file)
    except Exception as exc:
        print(f"ERROR: failed to load config file {args.config_file if args.config_file else ''}: {exc}")
        return 1

    engine_list = toolkit_helpers.get_engine_list(args.all, args.dx_host, eng)

    if len(engine_list) > 1:
        print("More than one engine is default. Use -d parameter")
        return 3

    res_symbol = ALLOWED_RES[args.interval]

    # Prepare timestamps
    st_param = args.st if args.st else "-5min"
    st_iso = toolkit_helpers.parse_timestamp(st_param, eng.getTimezone()) if st_param else None
    if not st_iso:
        print("Wrong start time (st) format")
        return 3

    et_iso = None
    if args.et:
        et_iso = toolkit_helpers.parse_timestamp(args.et, eng.getTimezone())
        if not et_iso:
            print("Wrong end time (et) format")
            return 3

    arguments = f"&resolution={args.interval}&numberofDatapoints=10000&startTime={st_iso}"
    if et_iso:
        arguments += f"&endTime={et_iso}"

    ret = 0

    for engine in sorted(engine_list):
        if eng.dlpx_connect(engine):
            print(f"Can't connect to Dephix Engine {engine}")
            ret += 1
            continue

        analytic_list = Analytics(eng, args.debug)
        name = 'cpu'
        metric = 'utilization'

        # Run nagios-style check (may exit internally)
        nagios_check(engine, analytic_list, name, metric, arguments, res_symbol, args.raw, args.crit, args.warn)

    return ret


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
