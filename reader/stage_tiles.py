"""Terrain, movement and deployment are independent source properties."""

PALETTE = {
    'ground': ('地面', '#566c67', '#83958b'),
    'highland': ('高台', '#aa9967', '#d6c48c'),
    'wall': ('墙体／禁入区', '#38312d', '#786b5b'),
    'obstacle': ('地面阻挡', '#566c67', '#d7c895'),
    'hole': ('空洞', '#101514', '#a99b78'),
    'void': ('场外空白', '#18201e', '#56645b'),
    'unknown': ('未分类地块', '#44463d', '#d5c991'),
}
SPECIAL = {'tile_telin': '传送入口', 'tile_telout': '传送出口',
           'tile_start': '侵入点', 'tile_flystart': '空袭侵入点',
           'tile_end': '保护目标', 'tile_hole': '空洞',
           'tile_fence_bound': '围栏', 'tile_grass': '草丛',
           'tile_volcano': '热泵通道', 'tile_healing': '治疗地块',
           'tile_infection': '活性源石', 'tile_pollution_roadf': '特殊地面',
           'tile_bigforce': '特殊部署地块', 'tile_ftstart': '特殊侵入点'}
DEPLOYMENT = {'NONE': '不可部署', 'MELEE': '可部署近战位',
              'RANGED': '可部署远程位', 'ALL': '可部署近战位和远程位'}


def describe_tile(tile):
    key=tile.get('tileKey','');height=tile.get('heightType');build=tile.get('buildableType')
    mask=tile.get('passableMask');passable=True if mask=='ALL' else False if mask=='FLY_ONLY' else None
    if key=='tile_hole':category='hole'
    elif key=='tile_empty':category='void'
    elif height=='HIGHLAND':category='highland' if build in {'RANGED','MELEE','ALL'} else 'wall' if build=='NONE' else 'unknown'
    elif height=='LOWLAND':category='obstacle' if passable is False else 'ground' if passable else 'unknown'
    else:category='unknown'
    label,fill,border=PALETTE[category]
    special=SPECIAL.get(key)
    return {'category':category,'label':label,'fill':fill,'border':border,
        'ground_passable':passable,'flying_passable':mask in {'ALL','FLY_ONLY'},
        'deployment':DEPLOYMENT.get(build,'部署属性未提供'),'undeployable':build=='NONE',
        'height':{'HIGHLAND':'高地','LOWLAND':'低地'}.get(height,'高度未提供'),
        'special':special,'portal_role':{'tile_telin':'in','tile_telout':'out'}.get(key),
        'spawn':key in {'tile_start','tile_flystart','tile_ftstart'},'goal':key=='tile_end',
        'raw':tile}


def tile_at(level,position):
    grid=level.get('mapData',{}).get('map',[])
    row,col=position.get('row',-1),position.get('col',-1)
    if not isinstance(row,int) or not isinstance(col,int) or row<0 or row>=len(grid):return None
    cells=grid[len(grid)-1-row]
    if col<0 or col>=len(cells):return None
    tiles=level['mapData'].get('tiles',[]);index=cells[col]
    return tiles[index] if isinstance(index,int) and 0<=index<len(tiles) else None
