"""Static grid path estimates; source waypoints remain available in the UI."""
import heapq
import math
from .stage_tiles import tile_at


def estimate_path(level,route,start,end):
    mode=route.get('motionMode')
    if mode not in {'WALK','FLY'}:return None
    a=(start.get('row'),start.get('col'));b=(end.get('row'),end.get('col'))
    if any(not isinstance(v,int) for v in a+b):return None
    def allowed(point):
        tile=tile_at(level,{'row':point[0],'col':point[1]})
        if tile is None:return False
        if mode=='FLY':return tile.get('passableMask') in {'ALL','FLY_ONLY'}
        if point not in {a,b} and tile.get('tileKey')=='tile_hole':return False
        return tile.get('passableMask')=='ALL'
    if not allowed(a) or not allowed(b):return None
    diagonal=route.get('allowDiagonalMove',False)
    directions=[(-1,0),(1,0),(0,-1),(0,1)]+([(-1,-1),(-1,1),(1,-1),(1,1)] if diagonal else [])
    best={a:0.};parents={};queue=[(math.dist(a,b),0.,a)]
    while queue:
        _,cost,current=heapq.heappop(queue)
        if cost>best[current]:continue
        if current==b:
            points=[b]
            while points[-1]!=a:points.append(parents[points[-1]])
            return [{'row':r,'col':c} for r,c in reversed(points)]
        for dr,dc in directions:
            nxt=(current[0]+dr,current[1]+dc)
            if not allowed(nxt):continue
            # Do not cut diagonally between blocked tile corners.
            if dr and dc and (not allowed((current[0]+dr,current[1])) or not allowed((current[0],current[1]+dc))):continue
            candidate=cost+math.hypot(dr,dc)
            if candidate<best.get(nxt,float('inf')):
                best[nxt]=candidate;parents[nxt]=current
                heapq.heappush(queue,(candidate+math.dist(nxt,b),candidate,nxt))
    return None
