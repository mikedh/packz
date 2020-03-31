

def do():
    import trimesh
    return trimesh.creation.icosphere().convex_hull


if __name__ == '__main__':
    do()
