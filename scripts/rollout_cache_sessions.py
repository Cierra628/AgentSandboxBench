"""Explicit page-cache lifecycle adapter; construction is live, tests inject fakes.

Call prepare before creation/restoration. Role labels never select backend semantics.
No simultaneous-snapshot or cross-guest PFN union is inferred here.
"""
import json
import threading
import time
from live_task_cache import LiveTaskCache


class RolloutCacheSessions:
    def __init__(self, out, container, cli, *, factory=LiveTaskCache, expected_kernel='6.1.175-asb-cache-v3', unified_rounds=False, restored_lower=False, restored_provider=False, event_windows=False):
        self.out, self.container, self.cli = out, container, cli
        self.factory, self.kernel = factory, expected_kernel
        from cache_round_clock import CacheRoundClock
        self.event_windows = event_windows
        self.round_clock = CacheRoundClock() if unified_rounds or event_windows else None
        if restored_provider and not restored_lower:raise ValueError('provider requires restored lower')
        self.restored_provider = restored_provider
        self.restored_lower = restored_lower
        self.roles, self.events = {}, []
        self.lock = threading.RLock()
        self.path = out/'rollout-cache-sessions.json'
        if self.path.exists():
            raise FileExistsError(self.path)
        self._record('INITIALIZED')

    def _record(self, event, **fields):
        self.events.append(dict(event=event, host_monotonic_ns=time.monotonic_ns(), **fields))
        self.path.write_text(json.dumps(dict(events=self.events, atomic_snapshot=False,
            shared_clock=(dict(origin_ns=self.round_clock.origin_ns,period_ns=self.round_clock.period_ns) if self.round_clock else None),
            scope='per-session guest cache and host residency; no cross-session union'), indent=2)+'\n')

    def _new(self, role, kind, generation, **kwargs):
        return self.factory(self.out, self.container, self.cli, session=kind,
            session_name=f'{role}-g{generation}', expected_kernel=self.kernel,
            **(dict(provider_cache=True) if self.restored_provider and kind=='restored' else {}),
            **(dict(lower_cache=True) if self.restored_lower and kind=='restored' else {}),
            **(dict(event_windows=True) if self.event_windows else {}),
            **(dict(round_clock=self.round_clock) if self.round_clock else {}), **kwargs)

    def _window(self, role, boundary, *, start=False):
        state = self.roles.get(role)
        fields = dict(role=role, boundary=boundary)
        if state is None or (not start and state['state'] != 'ACTIVE'):
            self._record('WINDOW_CLOSED', **fields, status='UNKNOWN', reason='role not registered or inactive')
            return
        fields.update(generation=state['generation'], sandbox_id=state['binding']['sandbox_id'],
                      directory=str(state['collector'].directory))
        self._record('WINDOW_OPEN', **fields)
        if start:
            try:
                self._start(role, state)  # includes the exporter bootstrap's one-time page reads
            except Exception as error:
                self._record('WINDOW_CLOSED', **fields, status='UNKNOWN', reason=repr(error))
                raise
        try:
            ticket = state['collector'].window_point()
        except Exception as error:
            self._record('WINDOW_CLOSED', **fields, status='UNKNOWN', reason=repr(error))
        else:
            self._record('WINDOW_CLOSED', **fields, status='OBSERVED', shared_round=ticket)

    def prepare(self, role, *, restored):
        with self.lock:
            if role in self.roles:
                raise ValueError('role already prepared')
            kind = 'restored' if restored else 'parent'
            collector = self._new(role, kind, 1)
            self.roles[role] = dict(state='PREPARED', kind=kind, generation=1, collector=collector)
            self._record('PREPARED_BEFORE_CREATE', role=role, kind=kind)

    def register(self, role, binding):
        with self.lock:
            state = self.roles.get(role)
            if state is None or state['state'] != 'PREPARED':
                if self.event_windows:
                    self._record('WINDOW_CLOSED', role=role, boundary='after_create', status='UNKNOWN', reason='missing preparation or duplicate registration')
                    return
                raise ValueError('RAM capture must be prepared before creation')
            if any(r.get('binding', {}).get('sandbox_id') == binding['sandbox_id'] for r in self.roles.values()):
                if self.event_windows:
                    self._record('WINDOW_CLOSED', role=role, boundary='after_create', status='UNKNOWN', reason='duplicate sandbox identity')
                    return
                raise ValueError('duplicate sandbox identity')
            state['binding'] = dict(binding)
            if self.event_windows:self._window(role, 'after_create', start=True)
            else:self._start(role, state)

    def _start(self, role, state):
        collector = state['collector']
        collector.expected_binding = state['binding']
        state['state'] = 'STARTING'
        try:
            collector.start(state['binding']['sandbox_id'])
        except Exception as error:
            state['state'] = 'FAILED'
            self._record('START_FAILED', role=role, error=repr(error))
            raise
        state['state'] = 'ACTIVE'
        self._record('ACTIVE', role=role, generation=state['generation'], binding=state['binding'],
                     directory=str(collector.directory))

    def saving(self, role):
        with self.lock:
            if self.event_windows:
                self._window(role, 'before_save')
                if self.roles.get(role, {}).get('state') != 'ACTIVE':return
            state = self.roles[role]
            if state['state'] != 'ACTIVE':
                raise ValueError('save requires active cache session')
            state['state'] = 'STOPPING'
            self._record('GAP_BEGIN', role=role, reason='stop exporter before snapshot')
            try:
                state['collector'].close()
            except Exception as error:
                state['state'] = 'FAILED'
                self._record('STOP_FAILED', role=role, error=repr(error))
                raise
            state['state'] = 'SAVING'
            self._record('CLOSED_BEFORE_SAVE', role=role)

    def saved(self, role, binding):
        with self.lock:
            if self.event_windows and self.roles.get(role, {}).get('state') != 'SAVING':
                self._record('WINDOW_CLOSED', role=role, boundary='after_save', status='UNKNOWN', reason='missing save or registration')
                return
            state = self.roles[role]
            if state['state'] != 'SAVING' or binding != state['binding']:
                raise ValueError('source identity changed or save not completed')
            # Same process: reuse its frozen registration evidence, never wait for a new ioctl.
            previous = state['collector']
            state['generation'] += 1
            state['collector'] = self._new(role, state['kind'], state['generation'], capture=previous.capture)
            if self.event_windows:self._window(role, 'after_save', start=True)
            else:self._start(role, state)
            self._record('GAP_END', role=role, generation=state['generation'])

    def stop(self):
        with self.lock:
            errors = []
            for role, state in self.roles.items():
                if state['state'] == 'CLOSED':
                    continue
                if self.event_windows:self._window(role, 'endpoint')
                if self.event_windows and state['state']=='PREPARED':
                    state['collector'].capture.close()  # no exporter or page reader was started
                    state['state']='CLOSED'
                    continue
                if state['state'] == 'SAVING':
                    errors.append(dict(role=role, error='save gap has no resumed session'))
                    continue
                prior = state['state']
                try:
                    state['collector'].close()
                except Exception as error:
                    errors.append(dict(role=role, error=repr(error)))
                if prior != 'ACTIVE':
                    errors.append(dict(role=role, error='session incomplete: '+prior))
                state['state'] = 'CLOSED'
            self._record('FINISHED' if not errors else 'FAILED', errors=errors)
            if errors:
                raise RuntimeError('cache session cleanup/incomplete: '+repr(errors))
            incomplete = self.event_windows and any(e.get('status')=='UNKNOWN' for e in self.events)
            return dict(status='UNKNOWN' if incomplete else 'PASS', index=str(self.path), atomic_snapshot=False)
