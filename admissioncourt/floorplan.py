"""Validate rectangular equipment footprints in a synthetic facility grid."""
def footprint(item, x=None, y=None):
    x = item['x'] if x is None else x
    y = item['y'] if y is None else y
    return {(col,row) for col in range(x,x+item['width']) for row in range(y,y+item['height'])}


def occupancy(plan, exclude=None):
    blocked = {tuple(cell) for cell in plan['walls']}
    for item in plan['racks']:
        if item['id'] != exclude:
            blocked.update(footprint(item))
    return blocked


def inspect(plan, item, x, y):
    cells = footprint(item, x, y)
    if any(not (0 <= col < plan['cols'] and 0 <= row < plan['rows']) for col,row in cells):
        return 'outside_floor'
    return 'occupied' if cells & occupancy(plan, item['id']) else 'available'
