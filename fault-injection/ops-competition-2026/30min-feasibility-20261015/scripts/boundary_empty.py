"""Only a verified zero-hit auxiliary OUTSIDE-window query can be represented as empty."""
def verified_empty(response):
    shards=response.get('_shards',{})
    if response.get('timed_out') is not False or shards.get('total',0)<1 or shards.get('failed')!=0 or shards.get('successful',0)+shards.get('skipped',0)<shards.get('total',0):
        raise ValueError('Boundary query did not prove successful source access')
    total=response.get('hits',{}).get('total',{})
    if total.get('relation')!='eq' or type(total.get('value')) is not int or total['value']<0:raise ValueError('Boundary query total is not exact')
    return total['value']==0
