"""Review-only bootstrap fix. Never modifies or runs ATTEMPT-02.

WAITING_FOR_FIRST_SAMPLE is not PASS and never permits an injection lease.
The first check gets one registered 15s monitor period; subsequent freshness
retains the existing 30s bound. Negative/future times fail closed.
"""
def classify(now, start, latest=None):
    if now < start:
        return {'state':'WAITING_FOR_WINDOW','may_authorize_fault':False}
    if latest is None:
        if now < start+15:
            return {'state':'WAITING_FOR_FIRST_SAMPLE','may_authorize_fault':False}
        raise RuntimeError('FIRST_LIVE_SAFETY_SAMPLE_DEADLINE_FAILED')
    if not start<=latest<=now or now-latest>30:
        raise RuntimeError('LIVE_SAFETY_HEARTBEAT_STALE')
    return {'state':'LIVE_SAFETY_READY','may_authorize_fault':True}
