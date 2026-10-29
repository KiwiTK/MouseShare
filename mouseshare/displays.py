"""Physical displays in a computer's native, possibly negative, coordinate space."""
import math


def union(monitors):
    left = min(m['x'] for m in monitors)
    top = min(m['y'] for m in monitors)
    return (left, top, max(m['x']+m['w'] for m in monitors)-left,
            max(m['y']+m['h'] for m in monitors)-top)


def validate_screen(value):
    if not isinstance(value, dict):
        return {}
    if any(type(value.get(k)) is not int or not 64 <= value[k] <= 32768 for k in ('w', 'h')):
        return {}
    result = dict(w=value['w'], h=value['h'], x=value.get('x', 0), y=value.get('y', 0))
    if any(type(result[k]) is not int or not -32768 <= result[k] <= 32768 for k in ('x', 'y')):
        return {}
    monitors = value.get('monitors', [])
    if not isinstance(monitors, list) or len(monitors) > 16:
        return {}
    clean, ids = [], set()
    for monitor in monitors:
        if not isinstance(monitor, dict):
            return {}
        ident = monitor.get('id')
        if not isinstance(ident, str) or not 1 <= len(ident) <= 128 or ident in ids:
            return {}
        ids.add(ident)
        if any(type(monitor.get(k)) is not int for k in ('x', 'y', 'w', 'h')):
            return {}
        x, y, w, h = (monitor[k] for k in ('x', 'y', 'w', 'h'))
        if not (64 <= w <= 32768 and 64 <= h <= 32768 and result['x'] <= x
                and result['y'] <= y and x+w <= result['x']+result['w'] and y+h <= result['y']+result['h']):
            return {}
        clean.append(dict(id=ident, x=x, y=y, w=w, h=h, primary=monitor.get('primary') is True))
    if clean:
        result['monitors'] = clean
    return result


def rectangles(screen):
    return screen.get('monitors') or [dict(x=screen.get('x', 0), y=screen.get('y', 0),
                                          w=screen.get('w', 1920), h=screen.get('h', 1080))]


def project(point, screen):
    """Nearest actual pixel; never leave a remote cursor in a desktop's empty gap."""
    x, y = point
    candidates = [(max(m['x'], min(m['x']+m['w']-1, x)), max(m['y'], min(m['y']+m['h']-1, y)))
                  for m in rectangles(screen)]
    return min(candidates, key=lambda p: (p[0]-x)**2+(p[1]-y)**2)


def normalized(point, screen):
    # WindowServer clamps a pinned cursor to the exclusive bound (x == w), not
    # w-1, which divided out to 1.0005 and failed valid_point's 0..1 contract —
    # the edge crossing then found no target exactly when the push was fastest.
    return [min(1.0, max(0.0, (point[0]-screen.get('x', 0))/max(1, screen['w']-1))),
            min(1.0, max(0.0, (point[1]-screen.get('y', 0))/max(1, screen['h']-1)))]


def native(point, screen):
    return project((screen.get('x', 0)+point[0]*(screen['w']-1),
                    screen.get('y', 0)+point[1]*(screen['h']-1)), screen)


def valid_point(point):
    return (isinstance(point, list) and len(point) == 2
            and all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in point))


def mapped_monitors(node, screen):
    for monitor in rectangles(screen):
        yield dict(x=node['x']+(monitor['x']-screen.get('x', 0))*node['w']/screen['w'],
                   y=node['y']+(monitor['y']-screen.get('y', 0))*node['h']/screen['h'],
                   w=monitor['w']*node['w']/screen['w'], h=monitor['h']*node['h']/screen['h'])


def contact_rects(key, node, screen, monitors=None):
    """Where a device's screens actually sit on the canvas, in system order.

    A saved per-monitor placement wins when it still matches the number of
    physical displays; anything else (no placement, resolution change, a
    monitor added or removed) falls back to the system arrangement glued
    inside the node rectangle.
    """
    rects = rectangles(screen)
    placed = (monitors or {}).get(key)
    if placed and len(placed) == len(rects):
        return placed
    return list(mapped_monitors(node, screen))


def screen_index_at(point, screen):
    """Index of the system display holding a native point; nearest if in a gap."""
    x, y = point
    rects = rectangles(screen)
    best, best_d = 0, None
    for index, m in enumerate(rects):
        dx = max(m['x']-x, 0, x-(m['x']+m['w']))
        dy = max(m['y']-y, 0, y-(m['y']+m['h']))
        d = dx*dx+dy*dy
        if best_d is None or d < best_d:
            best, best_d = index, d
    return best


def crossing(nodes, screens, source, side, point, monitors=None):
    """Route between exposed physical edges, returning a native target entry point.

    Each device occupies one rectangle per physical display. Those rectangles
    sit in the system arrangement by default, but a saved placement (monitors)
    may scatter them — so the cursor is located on the display it is actually
    on, mapped through that display's real rectangle, and matched against the
    real rectangles of every other device.
    """
    if source not in nodes or source not in screens or not valid_point(point):
        return None
    node = nodes[source]
    source_screen = screens[source]
    # Locate the cursor's display in system coordinates, then express it inside
    # that display's actual canvas rectangle (which may have been dragged away).
    mx = source_screen.get('x', 0)+point[0]*(source_screen['w']-1)
    my = source_screen.get('y', 0)+point[1]*(source_screen['h']-1)
    index = screen_index_at((mx, my), source_screen)
    system = rectangles(source_screen)[index]
    origin = contact_rects(source, node, source_screen, monitors)[index]
    fx = (mx-system['x'])/max(1, system['w'])
    fy = (my-system['y'])/max(1, system['h'])
    sx = origin['x']+fx*origin['w']
    sy = origin['y']+fy*origin['h']
    hits = []
    for a in contact_rects(source, node, source_screen, monitors):
        edge = {'left': a['x'], 'right': a['x']+a['w'], 'top': a['y'], 'bottom': a['y']+a['h']}[side]
        axis, transverse = (sx, sy) if side in {'left', 'right'} else (sy, sx)
        start, length = (a['y'], a['h']) if side in {'left', 'right'} else (a['x'], a['w'])
        if abs(axis-edge) > 2 or not start <= transverse <= start+length:
            continue
        for key, target in nodes.items():
            if key == source:
                continue
            screen = screens.get(key, dict(w=1920, h=1080))
            target_system = rectangles(screen)
            for j, b in enumerate(contact_rects(key, target, screen, monitors)):
                other = {'left': b['x']+b['w'], 'right': b['x'], 'top': b['y']+b['h'], 'bottom': b['y']}[side]
                bs, bl = (b['y'], b['h']) if side in {'left', 'right'} else (b['x'], b['w'])
                if abs(edge-other) > 1 or min(start+length, bs+bl)-max(start, bs) < 8 or not bs <= transverse < bs+bl:
                    continue
                px, py = (other, transverse) if side in {'left', 'right'} else (transverse, other)
                # Back from canvas coordinates into this display's native pixels.
                m2 = target_system[j] if j < len(target_system) else dict(
                    x=screen.get('x', 0), y=screen.get('y', 0), w=screen['w'], h=screen['h'])
                nx = (px-b['x'])/max(1, b['w'])
                ny = (py-b['y'])/max(1, b['h'])
                tx = m2['x']+nx*m2['w']
                ty = m2['y']+ny*m2['h']
                tx += {'left': -9, 'right': 8}.get(side, 0)
                ty += {'top': -9, 'bottom': 8}.get(side, 0)
                entry = normalized(project((tx, ty), screen), screen)
                ratio = entry[1] if side in {'left', 'right'} else entry[0]
                hits.append((key, ratio, entry))
    unique = {key: (key, ratio, entry) for key, ratio, entry in hits}
    return next(iter(unique.values())) if len(unique) == 1 else None
