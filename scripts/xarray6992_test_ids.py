"""Resolve whitespace-truncated benchmark IDs using uniquely collected pytest IDs."""
import json
import os
from pathlib import Path


def resolve_test_ids(required,collected):
    assert len(set(required))==len(required) and len(set(collected))==len(collected)
    resolved=[];aliases={}
    for node in required:
        if '[' not in node or node.endswith(']'):
            resolved.append(node);continue
        matches=[actual for actual in collected if actual.partition(' ')[0]==node and actual.endswith(']')]
        assert len(matches)==1, 'missing or ambiguous truncated test ID: '+node
        actual=matches[0];resolved.append(actual);aliases[actual]=node
    assert len(set(resolved))==len(required)
    return resolved,aliases


def pytest_collection_finish(session):
    Path(os.environ['ASB_TEST_NODE_OUTPUT']).write_text(json.dumps([item.nodeid for item in session.items],indent=2)+'\n')
