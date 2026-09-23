import pygame
import math
import random
import sys

pygame.init()
WIDTH, HEIGHT = 1200, 850
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("DEM Soft-Sphere: Wadah Sempit + Pantulan Asli (Fix Bounce)")
clock = pygame.time.Clock()

PPM = 100
dt = 1/60
MAX_FRAMES = 400

COLOR_BG = (240, 242, 245)
COLOR_UI = (255, 255, 255)
COLOR_BLUE = (52, 152, 219)
COLOR_RED = (231, 76, 60)
COLOR_GREEN = (46, 204, 113)
COLOR_MAGENTA = (186, 74, 200)
COLOR_ORANGE = (243, 156, 18)
COLOR_WALL = (100, 116, 139)
COLOR_BORDER = (44, 62, 80)
COLOR_TEXT = (30, 30, 30)

# ============================================
# PARAMETER FISIKA
# ============================================
g = 9.8
kn, cn = 3000.0, 5.0    # kn KAKU -> penetrasi kecil, pantulan tegas
ks, cs = 1200.0, 2.0
mu = 0.4
PEN_MAX = 0.15          # penetrasi visual maksimum (clamp posisi SAJA, tanpa bunuh kecepatan)

# >>> WADAH DIPERSEMPIT LAGI: lebar 1.6 m <<<
WALL_LEFT, WALL_RIGHT = 5.2, 6.8
FLOOR_Y, CEILING_Y = 7.3, 2.6
UI_HEIGHT = 260

AUTO_REDROP = True
REST_FRAMES_LIMIT = 90

tang_state = {}

class Body:
    def __init__(self, x, y, r, m, color, name):
        self.x, self.y = x, y
        self.r, self.m = r, m
        self.color, self.name = color, name
        self.vx = self.vy = 0.0
        self.omega = 0.0
        self.theta = 0.0
        self.I = 0.5 * m * r * r
        self.ax = self.ay = 0.0
        self.alpha = 0.0
        self.fx = self.fy = 0.0
        self.tq = 0.0
        self.fn_now = 0.0
        self.history_y = []
        self.history_v = []
        self.history_fn = []
        self.history_omega = []
    
    def get_pos(self):
        return (int(self.x * PPM), int(self.y * PPM))
    
    def clamp(self):
        self.vx = max(-10, min(10, self.vx))
        self.vy = max(-10, min(10, self.vy))
        self.omega = max(-30, min(30, self.omega))

# ============================================
# SKEMA DROP BERTINGKAT (x disesuaikan wadah sempit)
# ============================================
DROP_SCHEME = [
    (5.70, 3.1, 0.50, 3.0, COLOR_BLUE,  "m1"),   # TERTINGGI
    (6.25, 4.4, 0.50, 2.0, COLOR_RED,   "m2"),   # SEDANG
    (5.80, 5.8, 0.55, 2.5, COLOR_GREEN, "m3"),   # RENDAH
]
DROP_VX = [0.5, -0.7, 0.0]

particles = [Body(*a) for a in DROP_SCHEME]
for p, vx0 in zip(particles, DROP_VX):
    p.vx = vx0

drop_count = 1
rest_frames = 0
frame_count = 0

def redrop(jitter=True):
    global drop_count
    for p, (bx, by, r, m, c, n), vx0 in zip(particles, DROP_SCHEME, DROP_VX):
        jx = random.uniform(-0.1, 0.1) if jitter else 0.0
        p.x = max(WALL_LEFT + r + 0.02, min(WALL_RIGHT - r - 0.02, bx + jx))
        p.y = by
        p.vx = vx0
        p.vy = 0.0
        p.omega = 0.0
        p.ax, p.ay, p.alpha = 0.0, g, 0.0
    tang_state.clear()
    drop_count += 1

# ============================================
# DETEKSI KONTAK
# ============================================
def pair_contact(A, B):
    dx, dy = B.x - A.x, B.y - A.y
    d = math.hypot(dx, dy)
    if d < 1e-9: return None
    nx, ny = dx / d, dy / d
    delta = A.r + B.r - d
    if delta <= 0: return None
    cA = (nx * (A.r - delta / 2), ny * (A.r - delta / 2))
    cB = (-nx * (B.r - delta / 2), -ny * (B.r - delta / 2))
    vAc = (A.vx - A.omega * cA[1], A.vy + A.omega * cA[0])
    vBc = (B.vx - B.omega * cB[1], B.vy + B.omega * cB[0])
    rvx, rvy = vBc[0] - vAc[0], vBc[1] - vAc[1]
    vn = rvx * nx + rvy * ny
    vt = (rvx - vn * nx, rvy - vn * ny)
    return dict(A=A, B=B, nx=nx, ny=ny, delta=delta, cA=cA, cB=cB,
                vn=vn, vt=vt, wall=None, key=tuple(sorted((A.name, B.name))))

def wall_contact(A, wall):
    if wall == 'floor':
        pen = A.y + A.r - FLOOR_Y; nx, ny = 0.0, 1.0
    elif wall == 'left':
        pen = WALL_LEFT - (A.x - A.r); nx, ny = -1.0, 0.0
    elif wall == 'right':
        pen = (A.x + A.r) - WALL_RIGHT; nx, ny = 1.0, 0.0
    else:
        pen = CEILING_Y - (A.y - A.r); nx, ny = 0.0, -1.0
    if pen <= 0: return None
    cA = (nx * (A.r - pen / 2), ny * (A.r - pen / 2))
    vAc = (A.vx - A.omega * cA[1], A.vy + A.omega * cA[0])
    rvx, rvy = -vAc[0], -vAc[1]
    vn = rvx * nx + rvy * ny
    vt = (rvx - vn * nx, rvy - vn * ny)
    return dict(A=A, B=None, nx=nx, ny=ny, delta=pen, cA=cA, cB=(0, 0),
                vn=vn, vt=vt, wall=wall, key=tuple(sorted((A.name, wall))))

# ============================================
# GAYA KONTAK
# ============================================
def apply_contact(c, h):
    A, B = c['A'], c['B']
    nx, ny = c['nx'], c['ny']
    
    Fn = kn * c['delta'] - cn * c['vn']
    if Fn < 0: Fn = 0.0
    
    st = tang_state.setdefault(c['key'], [0.0, 0.0])
    vtx, vty = c['vt']
    st[0] += vtx * h
    st[1] += vty * h
    Ftx = -(ks * st[0] + cs * vtx)
    Fty = -(ks * st[1] + cs * vty)
    Ft_mag = math.hypot(Ftx, Fty)
    limit = mu * Fn
    slip = False
    if Fn <= 0:
        Ftx = Fty = 0.0; Ft_mag = 0.0
    elif Ft_mag > limit:
        s = limit / Ft_mag
        Ftx *= s; Fty *= s; Ft_mag = limit
        slip = True
        st[0] = -Ftx / ks
        st[1] = -Fty / ks
    
    FAx, FAy = -Fn * nx - Ftx, -Fn * ny - Fty
    A.fx += FAx; A.fy += FAy
    A.tq += c['cA'][0] * FAy - c['cA'][1] * FAx
    if B is not None:
        FBx, FBy = Fn * nx + Ftx, Fn * ny + Fty
        B.fx += FBx; B.fy += FBy
        B.tq += c['cB'][0] * FBy - c['cB'][1] * FBx
    
    c.update(Fn=Fn, Ft=(Ftx, Fty), Ft_mag=Ft_mag, slip=slip)
    A.fn_now += Fn
    if B is not None: B.fn_now += Fn

# ============================================
# VELOCITY VERLET + CONSTRAINT YANG TIDAK MEMBUNUH PANTULAN
# ============================================
def step(bodies, dt):
    SUB = 10
    h = dt / SUB
    last = []
    for _ in range(SUB):
        for b in bodies:
            b.x += b.vx * h + 0.5 * b.ax * h * h
            b.y += b.vy * h + 0.5 * b.ay * h * h
            b.theta += b.omega * h + 0.5 * b.alpha * h * h
        for b in bodies:
            b.fx, b.fy, b.tq, b.fn_now = 0.0, b.m * g, 0.0, 0.0
        contacts = []
        for i in range(len(bodies)):
            for j in range(i + 1, len(bodies)):
                c = pair_contact(bodies[i], bodies[j])
                if c: contacts.append(c)
        for b in bodies:
            for w in ('floor', 'left', 'right', 'ceil'):
                c = wall_contact(b, w)
                if c: contacts.append(c)
        seen = set()
        for c in contacts:
            apply_contact(c, h)
            seen.add(c['key'])
        for k in list(tang_state.keys()):
            if k not in seen: del tang_state[k]
        for b in bodies:
            axn, ayn = b.fx / b.m, b.fy / b.m
            aln = b.tq / b.I
            b.vx += 0.5 * (b.ax + axn) * h
            b.vy += 0.5 * (b.ay + ayn) * h
            b.omega += 0.5 * (b.alpha + aln) * h
            b.ax, b.ay, b.alpha = axn, ayn, aln
            b.clamp()
        # >>> CONSTRAINT AMAN: hanya clamp POSISI penetrasi dalam,
        # >>> KECEPATAN TIDAK DISENTUH -> pegas yang memantulkan bola <<<
        for b in bodies:
            if b.y + b.r - FLOOR_Y > PEN_MAX:
                b.y = FLOOR_Y - b.r + PEN_MAX
            if WALL_LEFT - (b.x - b.r) > PEN_MAX:
                b.x = WALL_LEFT + b.r - PEN_MAX
            if (b.x + b.r) - WALL_RIGHT > PEN_MAX:
                b.x = WALL_RIGHT - b.r + PEN_MAX
            if CEILING_Y - (b.y - b.r) > PEN_MAX:
                b.y = CEILING_Y + b.r - PEN_MAX
        last = contacts
    return last

# ============================================
# HELPER GAMBAR
# ============================================
def draw_arrow(surf, start, end, color, w=3):
    if math.hypot(end[0]-start[0], end[1]-start[1]) < 2: return
    pygame.draw.line(surf, color, start, end, w)
    ang = math.atan2(end[1]-start[1], end[0]-start[0])
    for a in (-math.pi/6, math.pi/6):
        pygame.draw.line(surf, color, end,
                         (end[0]-10*math.cos(ang-a), end[1]-10*math.sin(ang-a)), w)

def draw_anatomy(surf, contacts):
    f = pygame.font.SysFont("Arial", 10, bold=True)
    SC = 0.15
    for c in contacts:
        A = c['A']
        px, py = A.get_pos()
        nx, ny = c['nx'], c['ny']
        cx = px + c['cA'][0] * PPM
        cy = py + c['cA'][1] * PPM
        
        ex, ey = px + nx * A.r * PPM, py + ny * A.r * PPM
        draw_arrow(surf, (cx, cy), (ex, ey), COLOR_RED, 2)
        surf.blit(f.render(f"δ={c['delta']:.3f}", True, COLOR_RED), (ex + 4, ey - 12))
        
        L = min(80, c['Fn'] * SC)
        draw_arrow(surf, (cx, cy), (cx + nx*L, cy + ny*L), COLOR_GREEN, 3)
        draw_arrow(surf, (cx, cy), (cx - nx*L, cy - ny*L), COLOR_GREEN, 3)
        
        Ftx, Fty = c['Ft']
        Lt = min(60, c['Ft_mag'] * SC)
        if Lt > 2:
            ux, uy = Ftx / (c['Ft_mag']+1e-9), Fty / (c['Ft_mag']+1e-9)
            draw_arrow(surf, (cx, cy), (cx + ux*Lt, cy + uy*Lt), COLOR_ORANGE, 3)
            draw_arrow(surf, (cx, cy), (cx - ux*Lt, cy - uy*Lt), COLOR_ORANGE, 3)
        
        target = c['wall'].upper() if c['wall'] else c['B'].name
        lbl = f"{target}: Fn={c['Fn']:.0f} Ft={c['Ft_mag']:.0f} {'SLIP' if c['slip'] else 'stick'}"
        surf.blit(f.render(lbl, True, COLOR_BORDER), (cx + 8, cy + 8))

def draw_container(surf):
    fp = FLOOR_Y * PPM
    lp, rp = WALL_LEFT * PPM, WALL_RIGHT * PPM
    pygame.draw.rect(surf, COLOR_WALL, (0, fp, WIDTH, HEIGHT - fp))
    pygame.draw.line(surf, COLOR_BORDER, (0, fp), (WIDTH, fp), 4)
    pygame.draw.rect(surf, COLOR_WALL, (0, UI_HEIGHT, lp, fp - UI_HEIGHT))
    pygame.draw.line(surf, COLOR_BORDER, (lp, UI_HEIGHT), (lp, fp), 4)
    pygame.draw.rect(surf, COLOR_WALL, (rp, UI_HEIGHT, WIDTH - rp, fp - UI_HEIGHT))
    pygame.draw.line(surf, COLOR_BORDER, (rp, UI_HEIGHT), (rp, fp), 4)

def draw_particle(surf, p):
    px, py = p.get_pos()
    rp = int(p.r * PPM)
    pygame.draw.circle(surf, p.color, (px, py), rp)
    pygame.draw.circle(surf, COLOR_BORDER, (px, py), rp, 2)
    mx = px + rp * 0.75 * math.cos(p.theta)
    my = py + rp * 0.75 * math.sin(p.theta)
    pygame.draw.line(surf, (255, 255, 255), (px, py), (int(mx), int(my)), 2)
    pygame.draw.circle(surf, (255, 255, 255), (int(mx), int(my)), 3)
    f = pygame.font.SysFont("Arial", 13, bold=True)
    surf.blit(f.render(p.name, True, (255, 255, 255)), (px - 14, py - 8))

def draw_graph(surf, x, y, w, h, title, data_list, colors, labels, max_vals):
    pygame.draw.rect(surf, COLOR_UI, (x, y, w, h))
    pygame.draw.rect(surf, COLOR_BORDER, (x, y, w, h), 2)
    f = pygame.font.SysFont("Arial", 12, bold=True)
    surf.blit(f.render(title, True, COLOR_TEXT), (x + 8, y + 4))
    px0, py0, pw, ph = x + 8, y + 22, w - 16, h - 30
    for data, color, label, mv in zip(data_list, colors, labels, max_vals):
        if not isinstance(data, (list, tuple)) or len(data) < 2: continue
        pts = [(px0 + (i / MAX_FRAMES) * pw,
                py0 + ph - (max(0.0, min(v, mv)) / mv) * ph)
               for i, v in enumerate(data[-MAX_FRAMES:])]
        pygame.draw.lines(surf, color, False, pts, 2)
        fs = pygame.font.SysFont("Arial", 9)
        surf.blit(fs.render(label, True, color), (x + w - 55, y + 6 + labels.index(label) * 10))

# ============================================
# LOOP UTAMA
# ============================================
running = True
f_bold = pygame.font.SysFont("Arial", 15, bold=True)
f_small = pygame.font.SysFont("Arial", 11)

for p in particles:
    p.ax, p.ay = 0.0, g

while running:
    screen.fill(COLOR_BG)
    
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_r:
                for p, (ix, iy, r, m, c, n) in zip(particles, DROP_SCHEME):
                    p.x, p.y = ix, iy
                    p.vx = p.vy = 0.0
                    p.omega = 0.0
                    p.theta = 0.0
                    p.ax, p.ay, p.alpha = 0.0, g, 0.0
                    for hst in (p.history_y, p.history_v, p.history_fn, p.history_omega):
                        hst.clear()
                for p, vx0 in zip(particles, DROP_VX):
                    p.vx = vx0
                tang_state.clear()
                drop_count = 1
                rest_frames = 0
                frame_count = 0
            if event.key == pygame.K_a:
                AUTO_REDROP = not AUTO_REDROP
    
    contacts = step(particles, dt)
    
    all_rest = all(math.hypot(p.vx, p.vy) < 0.05 and abs(p.omega) < 0.05 for p in particles)
    rest_frames = rest_frames + 1 if all_rest else 0
    if AUTO_REDROP and rest_frames > REST_FRAMES_LIMIT:
        redrop(jitter=True)
        rest_frames = 0
    
    for p in particles:
        p.history_y.append(p.y)
        p.history_v.append(math.hypot(p.vx, p.vy))
        p.history_fn.append(p.fn_now)
        p.history_omega.append(abs(p.omega))
        for hst in (p.history_y, p.history_v, p.history_fn, p.history_omega):
            if len(hst) > MAX_FRAMES: hst.pop(0)
    frame_count += 1
    
    # ---- UI ATAS ----
    pygame.draw.rect(screen, COLOR_UI, (10, 10, 240, 240))
    pygame.draw.rect(screen, COLOR_BORDER, (10, 10, 240, 240), 2)
    screen.blit(f_bold.render("DROP BERTINGKAT", True, COLOR_TEXT), (20, 18))
    info = [
        (f"kn={kn:.0f} cn={cn:.1f}", COLOR_TEXT),
        (f"ks={ks:.0f} μ={mu}", COLOR_ORANGE),
        (f"lebar wadah={WALL_RIGHT-WALL_LEFT:.1f} m", COLOR_MAGENTA),
        (f"Frame {frame_count} | kontak {len(contacts)}", COLOR_BLUE),
        (f"Drop ke-{drop_count} | auto={'ON' if AUTO_REDROP else 'OFF'}", COLOR_MAGENTA),
        ("", COLOR_TEXT),
        (f"m1(TINGGI) ω={particles[0].omega:+.1f}", COLOR_BLUE),
        (f"m2(SEDANG) ω={particles[1].omega:+.1f}", COLOR_RED),
        (f"m3(RENDAH) ω={particles[2].omega:+.1f}", COLOR_GREEN),
        ("", COLOR_TEXT),
        ("R=reset | A=auto", COLOR_TEXT),
    ]
    for i, (t, c) in enumerate(info):
        screen.blit(f_small.render(t, True, c), (20, 44 + i * 17))
    
    cols = [COLOR_BLUE, COLOR_RED, COLOR_GREEN]
    labs = ["m1", "m2", "m3"]
    draw_graph(screen, 260, 10, 300, 115, "POSISI Y (m) - lihat pantulan!", [p.history_y for p in particles], cols, labs, [8, 8, 8])
    draw_graph(screen, 570, 10, 300, 115, "|v| (m/s)", [p.history_v for p in particles], cols, labs, [10, 10, 10])
    draw_graph(screen, 880, 10, 310, 115, "|ω| (rad/s)", [p.history_omega for p in particles], cols, labs, [20, 20, 20])
    draw_graph(screen, 260, 135, 930, 115, "GAYA KONTAK NORMAL Fn (N)", [p.history_fn for p in particles], cols, labs, [900, 900, 900])
    
    # ---- SIMULASI ----
    draw_container(screen)
    for p in particles:
        draw_particle(screen, p)
    draw_anatomy(screen, contacts)
    
    lx, ly = 170, 600
    pygame.draw.rect(screen, (255, 255, 255, 220), (lx, ly, 260, 130))
    pygame.draw.rect(screen, COLOR_BORDER, (lx, ly, 260, 130), 1)
    leg = [("WAJAH BARU:", COLOR_TEXT),
           ("wadah 1.6 m -> pile tinggi", COLOR_MAGENTA),
           ("pantulan asli (bug restitusi fixed)", COLOR_GREEN),
           ("δ merah | Fn hijau | Ft oranye", COLOR_TEXT),
           ("garis putih = marker rotasi", COLOR_TEXT),
           ("R=reset | A=auto re-drop", COLOR_TEXT)]
    for i, (t, c) in enumerate(leg):
        screen.blit(f_small.render(t, True, c), (lx + 10, ly + 8 + i * 20))
    
    pygame.display.flip()
    clock.tick(60)

pygame.quit()
sys.exit()