"""Shared monotonic schedule, not an atomic snapshot or barrier."""
import time


class CacheRoundClock:
    def __init__(self, interval=1.0, *, now=time.monotonic_ns):
        if interval <= 0:
            raise ValueError('positive interval required')
        self.period_ns = int(interval*1e9)
        if self.period_ns < 1:
            raise ValueError('interval below nanosecond')
        self.now = now
        self.origin_ns = now()

    def wait_next(self, previous, stop):
        now = self.now()
        # A joining session begins at the next tick, without inventing earlier samples.
        index = max(0, (now-self.origin_ns+self.period_ns-1)//self.period_ns)
        if previous is not None:
            index = max(index, previous+1)
        target = self.origin_ns+index*self.period_ns
        if stop.wait(max(0, target-now)/1e9):
            return None
        actual = self.now()
        return dict(round=index,scheduled_ns=target,dispatch_ns=actual,
            start_lateness_ns=max(0,actual-target),
            skipped_rounds=None if previous is None else index-previous-1,
            first_session_round=previous is None,period_ns=self.period_ns,
            origin_ns=self.origin_ns,atomic_snapshot=False)


def finish_ticket(ticket, start_ns, end_ns):
    """Keep actual times even if dispatch was late or the query overran its period."""
    if end_ns < start_ns:
        raise ValueError('reversed query window')
    if ticket.get('period_ns') is None:
        return dict(ticket,read_start_ns=start_ns,read_end_ns=end_ns,elapsed_ns=end_ns-start_ns)
    return dict(ticket,read_start_ns=start_ns,read_end_ns=end_ns,
                elapsed_ns=end_ns-start_ns,
                finished_after_next_tick=end_ns>ticket['scheduled_ns']+ticket['period_ns'])


def summarize_round(tickets, expected_sessions):
    """Diagnostics only; missing sessions stay explicit and no physical sum is made."""
    expected=set(expected_sessions)
    if set(tickets)-expected:
        raise ValueError('unexpected session')
    if not tickets:
        return dict(status='MISSING',missing_sessions=sorted(expected),atomic_snapshot=False)
    identities={(v['origin_ns'],v['period_ns'],v['round']) for v in tickets.values()}
    if len(identities)!=1:
        raise ValueError('different shared rounds')
    missing=sorted(expected-set(tickets))
    left=min(v['read_start_ns'] for v in tickets.values())
    right=max(v['read_end_ns'] for v in tickets.values())
    overlap_left=max(v['read_start_ns'] for v in tickets.values())
    overlap_right=min(v['read_end_ns'] for v in tickets.values())
    return dict(status='INCOMPLETE' if missing else 'RECORDED',missing_sessions=missing,
        union_window_ns=right-left,
        common_window_ns=None if missing or overlap_right<overlap_left else overlap_right-overlap_left,
        late_sessions=sorted(k for k,v in tickets.items() if v['dispatch_ns']>=v['scheduled_ns']+v['period_ns']),
        overrun_sessions=sorted(k for k,v in tickets.items() if v['finished_after_next_tick']),
        atomic_snapshot=False)
