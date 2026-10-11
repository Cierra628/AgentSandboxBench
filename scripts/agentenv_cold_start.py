"""Create on the owned loopback service with an explicit HTTP wait budget."""
import argparse
import json
from pathlib import Path
import sys
import tomllib
import urllib.request
from urllib.parse import urlsplit
import uuid


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('cold creation redirects are forbidden')


def create(config_home, service_url, image, *, request_timeout=600, memory_mib=4096):
    url = urlsplit(service_url)
    if (url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port
            or url.path or url.query or url.fragment or url.username or url.password):
        raise ValueError('owned loopback service URL required')
    if not 1 <= request_timeout <= 600:
        raise ValueError('HTTP wait budget must be 1..600 seconds')
    if not isinstance(memory_mib,int) or not 512 <= memory_mib <= 4096:
        raise ValueError('memory must be 512..4096 MiB')
    # Never print credentials or server bodies (including envdAccessToken).
    credentials = tomllib.loads((config_home/'aenv/credentials').read_text())
    if credentials['url'] != service_url or not credentials['api_key']:
        raise ValueError('credentials do not match owned service')
    body = dict(image=image, timeout=1800, cpuCount=2, memoryMB=memory_mib, secure=True)
    request = urllib.request.Request(service_url+'/sandboxes-cold',
        data=json.dumps(body).encode(), method='POST',
        headers={'Content-Type': 'application/json', 'X-API-Key': credentials['api_key']})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=request_timeout) as response:
        if response.status != 201 and response.status != 200:
            raise RuntimeError('unexpected cold creation status')
        payload = response.read(65537)
    if len(payload) > 65536:
        raise ValueError('cold creation response too large')
    sid = json.loads(payload)['sandboxID']
    if not isinstance(sid, str) or str(uuid.UUID(sid)) != sid:
        raise ValueError('invalid sandbox ID')
    return sid


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config-home', type=Path, required=True)
    parser.add_argument('--service-url', required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--memory-mib', type=int, default=4096)
    args = parser.parse_args()
    try:
        print(create(args.config_home, args.service_url, args.image,memory_mib=args.memory_mib))
    except Exception as error:
        print('cold creation failed: '+type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
